import json
import random
import re
import logging
from datetime import datetime, timedelta, date
from uuid import uuid4

from django.core.management.base import BaseCommand
from django.db.models import Q, Sum, F, ExpressionWrapper, FloatField, Value, Count
from django.db.models.functions import Coalesce
from django.contrib.auth import get_user_model
from core.models import (
    MealPlan, MealPlanDay, Meal, Recipe, MealPart, MealPartRecipe, UserRecipeFeedback
)
from langchain_ollama.llms import OllamaLLM

# Set up basic logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

User = get_user_model()

# --- Available Tags ---
# Valid tags (case-insensitive): vegetarian, vegan, lunch, dinner, post-workout, pre-workout, soup, dairy, fruit, healthy, breakfast, main course

# --- Updated Meal Parts Structure ---
# Breakfast: 'main course' (required), 'fruit' (optional), 'dairy' (optional)
# Lunch: 'main course' (required), 'soup' (optional)
# Dinner: 'main course' (required), 'soup' (optional)
MEAL_PARTS_STRUCTURE = {
    "breakfast": [
        {"name": "main course", "is_required": True},
        {"name": "fruit", "is_required": False},
        {"name": "dairy", "is_required": False},
    ],
    "lunch": [
        {"name": "main course", "is_required": True},
        {"name": "soup", "is_required": False},
    ],
    "dinner": [
        {"name": "main course", "is_required": True},
        {"name": "soup", "is_required": False},
    ],
}

# --- Simple Meals Configuration ---
# Simple meals: mid_morning, mid_afternoon, supper.
# Mapping: mid_morning/mid_afternoon -> "breakfast", supper -> "dinner"
SIMPLE_MEALS = ['mid_morning', 'mid_afternoon', 'supper']

# --- Helper Functions ---
def fix_invalid_json_keys(json_str):
    return re.sub(r'([{,]\s*)([A-Za-z0-9_]+)(\s*:\s*)', r'\1"\2"\3', json_str)

def extract_json(text):
    if not text:
        logger.error("Cannot extract JSON from empty or None text.")
        raise ValueError("Cannot extract JSON from empty or None text.")
    text = text.strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        logger.debug("Direct JSON parsing failed. Trying to find JSON within text.")
    code_block_match = re.search(r'```json\s*(\{.*?\})\s*```', text, re.DOTALL)
    if code_block_match:
        potential_json = code_block_match.group(1)
        try:
            logger.debug(f"Found JSON in code block: {potential_json[:200]}...")
            return json.loads(potential_json)
        except json.JSONDecodeError as e:
            logger.warning(f"Parsing JSON from code block failed: {e}.")
    json_like_match = re.search(r'(\{.*\})', text, re.DOTALL)
    if json_like_match:
        potential_json = json_like_match.group(1)
        try:
            logger.debug(f"Found JSON-like block: {potential_json[:200]}...")
            return json.loads(potential_json)
        except json.JSONDecodeError:
            logger.warning("Parsing of JSON-like block failed, trying to fix keys.")
            try:
                fixed_json = fix_invalid_json_keys(potential_json)
                return json.loads(fixed_json)
            except json.JSONDecodeError as e_fixed:
                logger.error(f"Parsing fixed JSON failed: {e_fixed}.")
    logger.error(f"Could not extract valid JSON (raw text: {text[:500]}...)")
    raise ValueError("Could not extract valid JSON from LLM output")

def validate_prerequisites(user, required_recipes=30):
    errors = []
    recipes_qs = Recipe.objects.prefetch_related(
        'tags',
        'recipeingredient_set__ingredient__in100g'
    )
    if hasattr(user, 'dietary_preferences') and user.dietary_preferences.exists():
        recipes_qs = recipes_qs.filter(tags__in=user.dietary_preferences.all()).distinct()
    total_recipes = recipes_qs.count()
    if total_recipes < required_recipes:
        errors.append(f"Need at least {required_recipes} recipes matching preferences, but found only {total_recipes}.")
    return errors, recipes_qs

def get_macro_targets(goal):
    if goal == 'weight_loss':
        return {'protein': 0.35, 'carbs': 0.40, 'fat': 0.25}
    elif goal == 'muscle_gain':
        return {'protein': 0.30, 'carbs': 0.50, 'fat': 0.20}
    else:
        return {'protein': 0.25, 'carbs': 0.50, 'fat': 0.25}

def distribute_calories(target_calories, meal_types):
    distribution_main = {
        'breakfast': 0.25, 'lunch': 0.35, 'dinner': 0.30,
        'pre-workout': 0.05, 'post-workout': 0.05
    }
    distribution_simple = {
        'mid_morning': 0.05, 'mid_afternoon': 0.05, 'supper': 0.10
    }
    result = {}
    for mt in meal_types:
        if mt in distribution_main:
            result[mt] = int(target_calories * distribution_main[mt])
        elif mt in distribution_simple:
            result[mt] = int(target_calories * distribution_simple[mt])
        else:
            result[mt] = 0
    return result

def score_recipe(recipe, user, meal_type, part_name, target_calories, user_feedback_cache=None):
    score = 0.0
    recipe_actual_calories = getattr(recipe, 'calculated_calories', recipe.calories)
    if recipe_actual_calories is not None and target_calories and target_calories > 0:
        calorie_diff = abs(recipe_actual_calories - target_calories)
        calorie_score = max(0, 1 - (calorie_diff / target_calories))
        score += calorie_score * 0.4
    recipe_tags = {tag.name.lower() for tag in recipe.tags.all()}
    tag_bonus = 0.0
    if meal_type and meal_type.lower() in recipe_tags:
        tag_bonus += 0.1
    if part_name and part_name.lower() in recipe_tags:
        tag_bonus += 0.1
    score += tag_bonus * 0.2
    user_bonus = 0.0
    feedback = user_feedback_cache.get(recipe.id) if user_feedback_cache else None
    if not feedback and user:
        try:
            feedback = UserRecipeFeedback.objects.get(user=user, recipe=recipe)
        except UserRecipeFeedback.DoesNotExist:
            pass
    if feedback:
        if feedback.rating is not None:
            if feedback.rating >= 4:
                user_bonus += 0.1
            elif feedback.rating <= 2:
                user_bonus -= 0.1
        if feedback.liked is True:
            user_bonus += 0.1
        elif feedback.liked is False:
            user_bonus -= 0.2
        user_bonus += min(feedback.cooked_count, 5) * 0.02
        user_bonus -= min(feedback.skip_count, 5) * 0.02
    score += user_bonus * 0.25
    global_bonus = 0.0
    if hasattr(recipe, 'average_rating') and recipe.average_rating > 0:
        global_bonus += (recipe.average_rating / 5.0) * 0.05
    if hasattr(recipe, 'global_cooked_count') and recipe.global_cooked_count > 0:
        global_bonus += min(recipe.global_cooked_count / 100.0, 1.0) * 0.05
    score += global_bonus
    score += random.uniform(0, 0.05)
    return score

def select_recipe_for_part(recipes_qs_with_calories, part_name, meal_type=None, target_calories=None, user=None, user_feedback_cache=None):
    best_score = -1.0
    best_recipes = []
    filtered_qs = recipes_qs_with_calories
    if meal_type and part_name:
        filtered_qs = filtered_qs.filter(tags__name__iexact=meal_type.lower()).filter(tags__name__iexact=part_name.lower())
    elif meal_type:
        filtered_qs = filtered_qs.filter(tags__name__iexact=meal_type.lower())
    elif part_name:
        filtered_qs = filtered_qs.filter(tags__name__iexact=part_name.lower())
    for recipe in filtered_qs.distinct():
        score_val = score_recipe(recipe, user, meal_type, part_name, target_calories, user_feedback_cache)
        if score_val > best_score:
            best_score = score_val
            best_recipes = [recipe]
        elif score_val == best_score and best_score != -1.0:
            best_recipes.append(recipe)
    if best_recipes:
        return random.choice(best_recipes)
    logger.warning(f"Deterministic: No suitable recipe found for part '{part_name}' of meal '{meal_type}'. Candidates after filter: {filtered_qs.count()}")
    return None

def select_recipe_for_simple_meal(recipes_qs_with_calories, meal_type, target_calories, user=None, user_feedback_cache=None):
    if meal_type.lower() in ['mid_morning', 'mid_afternoon']:
        meal_tag = "breakfast"
    elif meal_type.lower() == "supper":
        meal_tag = "dinner"
    else:
        meal_tag = meal_type.lower()
    filtered_qs = recipes_qs_with_calories.filter(tags__name__iexact=meal_tag).filter(tags__name__iexact="main course").distinct()
    best_score = -1.0
    best_recipes = []
    for recipe in filtered_qs.distinct():
        score_val = score_recipe(recipe, user, meal_tag, "main course", target_calories, user_feedback_cache)
        if score_val > best_score:
            best_score = score_val
            best_recipes = [recipe]
        elif score_val == best_score and best_score != -1.0:
            best_recipes.append(recipe)
    if best_recipes:
        return random.choice(best_recipes)
    logger.warning(f"Deterministic: No suitable recipe found for simple meal '{meal_type}'. Candidates after filter: {filtered_qs.count()}")
    return None

def calculate_day_nutrition(day_obj):
    totals = {'calories': 0.0, 'protein': 0.0, 'carbohydrate': 0.0, 'fat': 0.0}
    for meal in day_obj.meals.all():
        for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
            recipe = mpr.recipe
            if recipe:
                totals['calories'] += recipe.calories if recipe.calories is not None else 0.0
                totals['protein'] += recipe.protein if recipe.protein is not None else 0.0
                totals['carbohydrate'] += recipe.carbohydrate if recipe.carbohydrate is not None else 0.0
                totals['fat'] += recipe.fat if recipe.fat is not None else 0.0
    return totals

# --- Validate AI Meal Plan ---
def validate_ai_meal_plan(ai_json_data, daily_calories, recipes_qs_with_calories):
    errors = []
    required_day_types = {'regular', 'workout', 'rest'}
    day_types = {day.get('day_type', '').lower() for day in ai_json_data.get('days', [])}
    if day_types != required_day_types:
        errors.append(f"Meal plan must include exactly one regular, one workout, and one rest day. Found: {day_types}")

    regular_meal_types = ['breakfast', 'lunch', 'dinner', 'mid_morning', 'mid_afternoon', 'supper']
    workout_meal_types = regular_meal_types + ['pre-workout', 'post-workout']

    for day_idx, day_data in enumerate(ai_json_data.get('days', [])):
        day_type = day_data.get('day_type', '').lower()
        target = daily_calories
        if day_type == 'workout':
            target = int(daily_calories * 1.20)
        elif day_type == 'rest':
            target = int(daily_calories * 0.90)

        expected_meals = workout_meal_types if day_type == 'workout' else regular_meal_types
        meal_types = {meal.get('meal_type', '').lower() for meal in day_data.get('meals', [])}
        missing_meals = set(expected_meals) - meal_types
        if missing_meals:
            errors.append(f"Day {day_idx+1} ({day_type}): Missing required meals: {missing_meals}")

        day_calories = 0.0
        for meal_data in day_data.get('meals', []):
            meal_type = meal_data.get('meal_type', '').lower()
            parts = meal_data.get('parts', [])
            if meal_type in MEAL_PARTS_STRUCTURE:
                required_parts = {part['name'] for part in MEAL_PARTS_STRUCTURE[meal_type] if part['is_required']}
                part_names = {part.get('name', '').lower() for part in parts}
                missing_parts = required_parts - part_names
                if missing_parts:
                    errors.append(f"Day {day_idx+1} ({day_type}), Meal {meal_type}: Missing required parts: {missing_parts}")

                for part in parts:
                    recipe_id = part.get('selected_recipe_id')
                    if recipe_id is not None:
                        try:
                            recipe = recipes_qs_with_calories.get(id=recipe_id)
                            day_calories += recipe.calories or 0.0
                            recipe_tags = {tag.name.lower() for tag in recipe.tags.all()}
                            part_name = part.get('name','').lower()
                            if part_name not in recipe_tags or meal_type not in recipe_tags:
                                errors.append(f"Day {day_idx+1} ({day_type}), Meal {meal_type}, Part {part_name}: Recipe ID {recipe_id} lacks required tags")
                        except Recipe.DoesNotExist:
                            errors.append(f"Day {day_idx+1} ({day_type}), Meal {meal_type}, Part {part.get('name')}: Invalid recipe ID {recipe_id}")
            elif meal_type in SIMPLE_MEALS + ['pre-workout', 'post-workout']:
                if not parts or not any(part.get('selected_recipe_id') for part in parts):
                    errors.append(f"Day {day_idx+1} ({day_type}), Meal {meal_type}: No recipe selected")
                for part in parts:
                    recipe_id = part.get('selected_recipe_id')
                    if recipe_id is not None:
                        try:
                            recipe = recipes_qs_with_calories.get(id=recipe_id)
                            day_calories += recipe.calories or 0.0
                            recipe_tags = {tag.name.lower() for tag in recipe.tags.all()}
                            expected_meal_tag = 'breakfast' if meal_type in ['mid_morning', 'mid_afternoon'] else ('dinner' if meal_type == 'supper' else meal_type)
                            if 'main course' not in recipe_tags or expected_meal_tag not in recipe_tags:
                                errors.append(f"Day {day_idx+1} ({day_type}), Meal {meal_type}: Recipe ID {recipe_id} lacks required tags")
                        except Recipe.DoesNotExist:
                            errors.append(f"Day {day_idx+1} ({day_type}), Meal {meal_type}: Invalid recipe ID {recipe_id}")
        calorie_tolerance = 0.15
        if not (target * (1 - calorie_tolerance) <= day_calories <= target * (1 + calorie_tolerance)):
            errors.append(f"Day {day_idx+1} ({day_type}): Total calories {day_calories:.2f} outside target {target} ±15%")
    return errors

# --- Fix AI Meal Plan ---
def fix_ai_meal_plan(ai_json_data, user, daily_calories, recipes_qs_with_calories, user_feedback_cache):
    logger.info("Fixing AI-generated meal plan to meet deterministic criteria")
    fixed_days = []
    required_day_types = ['regular', 'workout', 'rest']
    for day_type in required_day_types:
        target = daily_calories
        if day_type == 'workout':
            target = int(daily_calories * 1.20)
        elif day_type == 'rest':
            target = int(daily_calories * 0.90)
        meal_types = ['breakfast', 'lunch', 'dinner', 'mid_morning', 'mid_afternoon', 'supper']
        if day_type == 'workout':
            meal_types.extend(['pre-workout', 'post-workout'])
        meal_allocations = distribute_calories(target, meal_types)
        day_data = next((day for day in ai_json_data.get('days', []) if day.get('day_type', '').lower() == day_type), None)
        if not day_data:
            day_data = {
                'date': (date.today() + timedelta(days=len(fixed_days))).isoformat(),
                'day_type': day_type,
                'target_calories_for_day': target,
                'meals': []
            }
        fixed_meals = []
        for meal_type in meal_types:
            allocated = meal_allocations.get(meal_type, 0)
            existing_meal = next((meal for meal in day_data.get('meals', []) if meal.get('meal_type','').lower() == meal_type), None)
            if meal_type in MEAL_PARTS_STRUCTURE:
                parts_defs = MEAL_PARTS_STRUCTURE[meal_type]
                fixed_parts = []
                for part_def in parts_defs:
                    part_name = part_def['name']
                    is_required = part_def['is_required']
                    existing_part = None
                    if existing_meal:
                        existing_part = next((p for p in existing_meal.get('parts', []) if p.get('name', '').lower() == part_name), None)
                    selected_recipe = None
                    if existing_part and existing_part.get('selected_recipe_id'):
                        try:
                            recipe = recipes_qs_with_calories.get(id=existing_part['selected_recipe_id'])
                            recipe_tags = {tag.name.lower() for tag in recipe.tags.all()}
                            if part_name in recipe_tags and meal_type in recipe_tags:
                                selected_recipe = recipe
                        except Recipe.DoesNotExist:
                            pass
                    if not selected_recipe and (is_required or random.choice([True, False])):
                        selected_recipe = select_recipe_for_part(
                            recipes_qs_with_calories, part_name, meal_type, allocated/len(parts_defs),
                            user, user_feedback_cache
                        )
                    fixed_parts.append({
                        'name': part_name,
                        'selected_recipe_id': selected_recipe.id if selected_recipe else None
                    })
                fixed_meals.append({
                    'meal_type': meal_type,
                    'allocated_calories_for_meal': allocated,
                    'parts': fixed_parts
                })
            else:
                selected_recipe = None
                if existing_meal and existing_meal.get('parts') and existing_meal['parts'][0].get('selected_recipe_id'):
                    try:
                        recipe = recipes_qs_with_calories.get(id=existing_meal['parts'][0]['selected_recipe_id'])
                        recipe_tags = {tag.name.lower() for tag in recipe.tags.all()}
                        expected_tag = 'breakfast' if meal_type in ['mid_morning','mid_afternoon'] else ('dinner' if meal_type=='supper' else meal_type)
                        if 'main course' in recipe_tags and expected_tag in recipe_tags:
                            selected_recipe = recipe
                    except Recipe.DoesNotExist:
                        pass
                if not selected_recipe:
                    selected_recipe = select_recipe_for_simple_meal(
                        recipes_qs_with_calories, meal_type, allocated, user, user_feedback_cache
                    )
                fixed_meals.append({
                    'meal_type': meal_type,
                    'allocated_calories_for_meal': allocated,
                    'parts': [{'name': 'main', 'selected_recipe_id': selected_recipe.id if selected_recipe else None}]
                })
        fixed_days.append({
            'date': day_data.get('date', (date.today() + timedelta(days=len(fixed_days))).isoformat()),
            'day_type': day_type,
            'target_calories_for_day': target,
            'meals': fixed_meals
        })
    ai_json_data['days'] = fixed_days
    return ai_json_data

# --- AI Agent Meal Plan Generation with RAG ---
def generate_meal_plan_agent(user, daily_calories, goal, model_name):
    logger.info(f"Starting AI meal plan generation for user {user.email} with {daily_calories} kcal, goal: {goal}.")
    errors, recipes_qs = validate_prerequisites(user)
    if errors:
        logger.error(f"Prerequisite validation failed for AI: {errors}")
        raise ValueError("\n".join(errors))
    candidate_qs = recipes_qs.annotate(
        calculated_calories=Coalesce(
            Sum(ExpressionWrapper(
                (F('recipeingredient__quantity') * F('recipeingredient__ingredient__in100g__energy') / Value(100.0)),
                output_field=FloatField()
            )),
            Value(0.0, output_field=FloatField())
        )
    ).distinct()

    user_feedback_cache = {fb.recipe_id: fb for fb in UserRecipeFeedback.objects.filter(user=user)}
    base_calories = daily_calories
    if hasattr(user, 'physical_activity') and user.physical_activity and user.physical_activity.lower() in ['high', 'moderate']:
        base_calories = int(daily_calories * 1.1)
    day_types = ['regular', 'workout', 'rest']
    day_calorie_targets = {
        'regular': base_calories,
        'workout': int(base_calories * 1.20),
        'rest': int(base_calories * 0.90)
    }
    candidate_data_for_prompt = {}
    logger.info("Fetching candidate recipes for the LLM prompt...")
    for day_type in day_types:
        meal_types = ['breakfast', 'lunch', 'dinner', 'mid_morning', 'mid_afternoon', 'supper']
        if day_type == 'workout':
            meal_types.extend(['pre-workout', 'post-workout'])
        allocations = distribute_calories(day_calorie_targets[day_type], meal_types)
        for meal_type in meal_types:
            allocated = allocations.get(meal_type, 0)
            if meal_type in MEAL_PARTS_STRUCTURE:
                for part_def in MEAL_PARTS_STRUCTURE[meal_type]:
                    part_name = part_def['name']
                    key = f"{day_type}_{meal_type}_{part_name}"
                    qs_meal = candidate_qs.filter(tags__name__iexact=meal_type.lower())
                    current_candidates_qs = qs_meal.filter(tags__name__iexact=part_name.lower()).distinct()
                    current_candidates_list = list(current_candidates_qs[:10])
                    candidate_data_for_prompt[key] = [{
                        "recipe_id": rec.id,
                        "title": rec.title,
                        "calories": round(rec.calculated_calories,2) if hasattr(rec,'calculated_calories') else 0,
                        "tags": [t.name for t in rec.tags.all()]
                    } for rec in current_candidates_list]
                    logger.debug(f"Fetched {len(candidate_data_for_prompt[key])} candidates for {key}")
            elif meal_type in SIMPLE_MEALS + ['pre-workout', 'post-workout']:
                key = f"{day_type}_{meal_type}_main"
                meal_tag = 'breakfast' if meal_type in ['mid_morning','mid_afternoon'] else ('dinner' if meal_type=='supper' else meal_type)
                current_candidates_qs = candidate_qs.filter(tags__name__iexact=meal_tag).filter(tags__name__iexact="main course").distinct()
                current_candidates_list = list(current_candidates_qs[:10])
                candidate_data_for_prompt[key] = [{
                    "recipe_id": rec.id,
                    "title": rec.title,
                    "calories": round(rec.calculated_calories,2) if hasattr(rec,'calculated_calories') else 0,
                    "tags": [t.name for t in rec.tags.all()]
                } for rec in current_candidates_list]
                logger.debug(f"Fetched {len(candidate_data_for_prompt[key])} candidates for {key}")

    prompt_introduction = (
        f"You are an expert meal planning assistant. Generate a 3-day JSON meal plan for user {user.email} targeting approximately {daily_calories} kcal/day (adjusted per day type) with goal '{goal}'.\n"
        f"**Requirements**:\n"
        f"1. Three days exactly: one 'regular', one 'workout', and one 'rest' day.\n"
        f"2. Calorie targets:\n"
        f"   - Regular: {daily_calories} kcal\n"
        f"   - Workout: {int(daily_calories*1.20)} kcal\n"
        f"   - Rest: {int(daily_calories*0.90)} kcal\n"
        f"   - Meal distribution: breakfast (25%), lunch (35%), dinner (30%), mid_morning (5%), mid_afternoon (5%), supper (10%), and for workout days add pre-workout (5%) and post-workout (5%).\n"
        f"3. Meal structure:\n"
        f"   - Breakfast: 'main course' (required), 'fruit' (optional), 'dairy' (optional).\n"
        f"   - Lunch: 'main course' (required), 'soup' (optional).\n"
        f"   - Dinner: 'main course' (required), 'soup' (optional).\n"
        f"   - Simple meals and workout meals: Only 'main course', mapping mid_morning/mid_afternoon -> 'breakfast' and supper -> 'dinner'.\n"
        f"4. Recipe selection: Use provided candidate recipes. For required parts, always select one if available (use null if no candidate exists). For optional parts, select 50% of the time.\n"
        f"5. Valid tags: vegetarian, vegan, lunch, dinner, post-workout, pre-workout, soup, dairy, fruit, healthy, breakfast, main course.\n"
        f"6. Output a single valid JSON object starting with '{{' and ending with '}}' with no extra text.\n\n"
        f"**Candidate Recipes**:\n"
    )
    prompt_candidate_sections = ""
    for key, candidates in candidate_data_for_prompt.items():
        day_type, meal_type, part_name = key.split('_',2)
        if not candidates:
            prompt_candidate_sections += (
                f"\nFor '{day_type}' day, '{meal_type}' meal, '{part_name}' part: NO CANDIDATES FOUND. Use 'selected_recipe_id': null.\n"
            )
            continue
        prompt_candidate_sections += (
            f"\nFor '{day_type}' day, '{meal_type}' meal, '{part_name}' part (Target ~{distribute_calories(day_calorie_targets[day_type], [meal_type]).get(meal_type, 0)} kcal):\n"
            f"Candidates: {json.dumps(candidates, indent=2)}\n"
            f"Select: {{\"name\": \"{part_name}\", \"selected_recipe_id\": <recipe_id_or_null>}}\n"
        )
    prompt_json_structure_example = (
        "\n**Output Format**:\n"
        "{\n"
        '  "meal_plan_title": "AI Generated Meal Plan for ' + (user.name or user.email) + '",\n'
        '  "user_email": "' + user.email + '",\n'
        '  "base_daily_calories": ' + str(daily_calories) + ',\n'
        '  "goal": "' + goal + '",\n'
        '  "macro_targets": ' + json.dumps(get_macro_targets(goal)) + ',\n'
        '  "days": [\n'
        '    {\n'
        '      "date": "YYYY-MM-DD",\n'
        '      "day_type": "regular",\n'
        '      "target_calories_for_day": ' + str(daily_calories) + ',\n'
        '      "meals": [\n'
        '        {\n'
        '          "meal_type": "breakfast",\n'
        '          "allocated_calories_for_meal": ' + str(int(daily_calories*0.25)) + ',\n'
        '          "parts": [\n'
        '            {"name": "main course", "selected_recipe_id": <id_or_null>},\n'
        '            {"name": "fruit", "selected_recipe_id": <id_or_null>},\n'
        '            {"name": "dairy", "selected_recipe_id": <id_or_null>}\n'
        '          ]\n'
        '        },\n'
        '        // ... other meals\n'
        '      ]\n'
        '    }\n'
        '    // ... workout and rest days\n'
        '  ]\n'
        '}\n'
    )
    full_prompt = prompt_introduction + prompt_candidate_sections + prompt_json_structure_example
    logger.debug(f"Generated LLM Prompt (first 500 chars):\n{full_prompt[:500]}")
    try:
        llm = OllamaLLM(model=model_name)
        logger.info(f"Invoking LLM ({model_name})...")
        response_text = llm.invoke(full_prompt)
        logger.info("LLM invocation complete.")
        logger.debug(f"Raw LLM Response:\n{response_text}")
        ai_json_data = extract_json(response_text)
        logger.info("Successfully parsed JSON from LLM response.")
        if not ai_json_data.get('days') or not isinstance(ai_json_data['days'], list) or len(ai_json_data['days']) != 3:
            raise ValueError("LLM output must have exactly 3 days")
        validation_errors = validate_ai_meal_plan(ai_json_data, daily_calories, candidate_qs)
        if validation_errors:
            logger.warning(f"LLM meal plan issues: {validation_errors}. Attempting fix...")
            ai_json_data = fix_ai_meal_plan(ai_json_data, user, daily_calories, candidate_qs, user_feedback_cache)
            validation_errors = validate_ai_meal_plan(ai_json_data, daily_calories, candidate_qs)
            if validation_errors:
                logger.error(f"Fixed meal plan issues persist: {validation_errors}")
                raise ValueError(f"Unable to fix meal plan: {validation_errors}")
        if not ai_json_data.get('meal_plan_title'):
            ai_json_data['meal_plan_title'] = f"AI Plan for {user.name or user.email}"
        return store_ai_meal_plan(ai_json_data, user, candidate_qs)
    except Exception as e:
        logger.error(f"AI generation failed: {str(e)}", exc_info=True)
        raise Exception(f"AI generation failed: {str(e)}")

# --- Store AI Meal Plan ---
def store_ai_meal_plan(ai_json_data, user, recipes_qs_with_calories):
    logger.info(f"Storing AI-generated meal plan titled: {ai_json_data.get('meal_plan_title')}")
    meal_plan = MealPlan.objects.create(
        user=user,
        title=ai_json_data.get('meal_plan_title', f"AI Plan for {user.name or user.email}"),
        description=(f"AI generated meal plan (RAG). Base Calories: {ai_json_data.get('base_daily_calories', 'N/A')}, Goal: {ai_json_data.get('goal', 'N/A')}")
    )
    final_daily_summaries = []
    for day_idx, day_data in enumerate(ai_json_data.get('days', [])):
        try:
            day_date_str = day_data.get('date')
            current_day_date = datetime.strptime(day_date_str, "%Y-%m-%d").date() if day_date_str else date.today() + timedelta(days=day_idx)
        except ValueError:
            logger.warning(f"Invalid date format '{day_data.get('date')}' in LLM output. Using sequential date.")
            current_day_date = date.today() + timedelta(days=day_idx)
        day_obj = MealPlanDay.objects.create(
            meal_plan=meal_plan, day_type=day_data.get('day_type', "regular"), date=current_day_date
        )
        for meal_data in day_data.get('meals', []):
            meal_type_str = meal_data.get('meal_type')
            if not meal_type_str:
                logger.warning("Meal data missing 'meal_type', skipping meal.")
                continue
            meal_obj = Meal.objects.create(meal_plan_day=day_obj, meal_type=meal_type_str)
            for part_data in meal_data.get('parts', []):
                part_name_str = part_data.get('name')
                selected_recipe_id = part_data.get('selected_recipe_id')
                if not part_name_str:
                    logger.warning(f"Meal part missing 'name' for meal '{meal_type_str}', skipping part.")
                    continue
                if selected_recipe_id is None:
                    logger.info(f"LLM indicated no recipe for part '{part_name_str}' of meal '{meal_type_str}'. Skipping.")
                    continue
                try:
                    recipe_obj = recipes_qs_with_calories.get(id=selected_recipe_id)
                except Recipe.DoesNotExist:
                    logger.error(f"Recipe ID {selected_recipe_id} for part '{part_name_str}' not found. Skipping.")
                    continue
                except ValueError:
                    logger.error(f"Invalid recipe ID '{selected_recipe_id}' for part '{part_name_str}'. Skipping.")
                    continue
                part_obj, created = MealPart.objects.get_or_create(
                    name=part_name_str, meal_type=meal_type_str, defaults={"is_required": True}
                )
                if created:
                    logger.info(f"Created new MealPart: {part_name_str} for meal type {meal_type_str}")
                MealPartRecipe.objects.create(
                    meal=meal_obj, meal_part=part_obj, recipe=recipe_obj, is_selected=True
                )
        day_nutrition_summary = calculate_day_nutrition(day_obj)
        final_daily_summaries.append({
            'date': day_obj.date.isoformat(),
            'day_type': day_obj.day_type,
            'total_calories': round(day_nutrition_summary['calories'], 2),
            'protein': round(day_nutrition_summary['protein'], 2),
            'carbohydrate': round(day_nutrition_summary['carbohydrate'], 2),
            'fat': round(day_nutrition_summary['fat'], 2)
        })
    stored_result_summary = {
        'meal_plan_id': meal_plan.id,
        'title': meal_plan.title,
        'user_email': user.email,
        'base_daily_calories': ai_json_data.get('base_daily_calories'),
        'goal': ai_json_data.get('goal'),
        'macro_targets': ai_json_data.get('macro_targets'),
        'days': final_daily_summaries
    }
    logger.info(f"Successfully stored AI meal plan ID: {meal_plan.id}")
    return stored_result_summary

# --- Deterministic Meal Plan Generation (Fallback) ---
def generate_meal_plan(user, daily_calories, goal="maintenance", day_types=None):
    if day_types is None:
        day_types = ['regular', 'workout', 'rest']
    logger.info(f"Starting deterministic meal plan generation for user {user.email}.")
    errors, recipes_qs = validate_prerequisites(user)
    if errors:
        logger.error(f"Deterministic prerequisites failed: {errors}")
        raise ValueError("\n".join(errors))
    recipes_qs_with_calories = recipes_qs.annotate(
        calculated_calories=Coalesce(
            Sum(ExpressionWrapper(
                (F('recipeingredient__quantity') * F('recipeingredient__ingredient__in100g__energy') / Value(100.0)),
                output_field=FloatField()
            )),
            Value(0.0, output_field=FloatField())
        )
    ).distinct()
    adjusted_daily_calories = daily_calories
    if hasattr(user, 'physical_activity') and user.physical_activity and user.physical_activity.lower() in ['high', 'moderate']:
        adjusted_daily_calories = int(daily_calories * 1.10)
        logger.info(f"Adjusted base daily calories to {adjusted_daily_calories} due to activity level.")
    macro_targets = get_macro_targets(goal)
    meal_plan = MealPlan.objects.create(
        user=user, title=f"Personalized Plan for {user.name or user.email}",
        description=f"Deterministically generated plan targeting ~{adjusted_daily_calories} kcal/day for goal: {goal}."
    )
    user_feedback_cache = {fb.recipe_id: fb for fb in UserRecipeFeedback.objects.filter(user=user)}
    final_daily_summaries = []
    for day_idx, day_type_str in enumerate(day_types):
        current_calories = adjusted_daily_calories
        if day_type_str == 'workout':
            current_calories = int(adjusted_daily_calories * 1.20)
        elif day_type_str == 'rest':
            current_calories = int(adjusted_daily_calories * 0.90)
        logger.info(f"Deterministic Day {day_idx+1}: Type '{day_type_str}', Target Calories: {current_calories}")
        day_obj = MealPlanDay.objects.create(
            meal_plan=meal_plan, day_type=day_type_str, date=date.today() + timedelta(days=day_idx)
        )
        current_meal_types = ['breakfast', 'lunch', 'dinner'] + SIMPLE_MEALS
        if day_type_str == 'workout':
            current_meal_types.extend(['pre-workout', 'post-workout'])
        meal_cal_allocations = distribute_calories(current_calories, current_meal_types)
        for meal_type_str in current_meal_types:
            allocated = meal_cal_allocations.get(meal_type_str, 0)
            if allocated == 0 and meal_type_str not in MEAL_PARTS_STRUCTURE:
                continue
            meal_obj = Meal.objects.create(meal_plan_day=day_obj, meal_type=meal_type_str)
            if meal_type_str in MEAL_PARTS_STRUCTURE:
                parts_defs = MEAL_PARTS_STRUCTURE[meal_type_str]
                for part_def in parts_defs:
                    part_name, is_required = part_def["name"], part_def["is_required"]
                    part_obj, _ = MealPart.objects.get_or_create(
                        name=part_name, meal_type=meal_type_str, defaults={"is_required": is_required}
                    )
                    selected_recipe = select_recipe_for_part(
                        recipes_qs_with_calories, part_name, meal_type_str,
                        allocated/len(parts_defs), user, user_feedback_cache
                    )
                    if selected_recipe:
                        MealPartRecipe.objects.create(
                            meal=meal_obj, meal_part=part_obj, recipe=selected_recipe, is_selected=True
                        )
                    elif is_required:
                        logger.error(f"Deterministic: Required part '{part_name}' for '{meal_type_str}' has no recipe.")
            elif meal_type_str in SIMPLE_MEALS or meal_type_str in ['pre-workout', 'post-workout']:
                selected_recipe = select_recipe_for_simple_meal(
                    recipes_qs_with_calories, meal_type_str, allocated, user, user_feedback_cache
                )
                if selected_recipe:
                    default_part, _ = MealPart.objects.get_or_create(
                        name="main", meal_type=meal_type_str, defaults={"is_required": True}
                    )
                    MealPartRecipe.objects.create(
                        meal=meal_obj, meal_part=default_part, recipe=selected_recipe, is_selected=True
                    )
                else:
                    logger.warning(f"Deterministic: No recipe for simple/workout meal '{meal_type_str}'.")
        day_nutrition_summary = calculate_day_nutrition(day_obj)
        final_daily_summaries.append({
            'date': day_obj.date.isoformat(),
            'day_type': day_obj.day_type,
            'total_calories': round(day_nutrition_summary['calories'], 2),
            'protein': round(day_nutrition_summary['protein'], 2),
            'carbohydrate': round(day_nutrition_summary['carbohydrate'], 2),
            'fat': round(day_nutrition_summary['fat'], 2)
        })
    result_summary = {
        'meal_plan_id': meal_plan.id,
        'title': meal_plan.title,
        'user_email': user.email,
        'daily_calories': daily_calories,
        'goal': goal,
        'macro_targets': macro_targets,
        'days': final_daily_summaries
    }
    logger.info(f"Successfully generated deterministic meal plan ID: {meal_plan.id}")
    return result_summary

# --- Management Command ---
class Command(BaseCommand):
    help = "Generate a personalized meal plan using an AI agent (RAG) with a deterministic fallback."

    def add_arguments(self, parser):
        parser.add_argument('--user_email', type=str, required=True, help="User's email")
        parser.add_argument('--calories', type=int, required=True, help="Base daily calorie intake")
        parser.add_argument('--goal', type=str, default="maintenance",
                            choices=['weight_loss', 'muscle_gain', 'maintenance'], help="User's goal")
        parser.add_argument('--model', type=str, default="llama3",
                            help="LLM model from Ollama (e.g., llama3, mistral, llama3:8b)")
        parser.add_argument('--force_deterministic', action='store_true',
                            help="Force deterministic generation, skipping AI.")

    def handle(self, *args, **options):
        start_time = datetime.now()
        user_email = options["user_email"]
        daily_calories = options["calories"]
        goal = options["goal"]
        model_name = options["model"]
        force_deterministic = options["force_deterministic"]
        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"User {user_email} not found."))
            return
        result = None
        generation_method = ""
        if not force_deterministic:
            try:
                self.stdout.write(self.style.HTTP_INFO(f"Attempting AI meal plan (model: '{model_name}')..."))
                result = generate_meal_plan_agent(user, daily_calories, goal, model_name)
                generation_method = "AI (RAG)"
                self.stdout.write(self.style.SUCCESS(f"AI plan '{result['title']}' (ID: {result['meal_plan_id']}) created!"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"AI generation failed: {e}"))
                self.stdout.write(self.style.WARNING("Falling back to deterministic generation..."))
        if result is None:
            try:
                self.stdout.write(self.style.HTTP_INFO("Using deterministic meal plan generation..."))
                result = generate_meal_plan(user, daily_calories, goal=goal)
                generation_method = "Deterministic"
                self.stdout.write(self.style.SUCCESS(f"Deterministic plan '{result['title']}' (ID: {result['meal_plan_id']}) created!"))
            except Exception as e2:
                self.stderr.write(self.style.ERROR(f"Deterministic generation failed: {e2}"))
                return
        if result:
            self.stdout.write(self.style.SUCCESS(f"\nMeal Plan Summary (Method: {generation_method}):"))
            self.stdout.write(f"  Plan ID: {result['meal_plan_id']}")
            self.stdout.write(f"  Title: {result['title']}")
            self.stdout.write(f"  User: {result['user_email']}")
            self.stdout.write(f"  Goal: {result['goal']}")
            self.stdout.write(f"  Base Daily Calories: {result.get('base_daily_calories') or result.get('daily_calories')}")
            self.stdout.write("\nDaily Nutrition Details:")
            for day_summary in result['days']:
                self.stdout.write(
                    f"  Date: {day_summary['date']}, Type: {day_summary['day_type']:<10} - "
                    f"Calories: {day_summary['total_calories']:.2f}, "
                    f"Protein: {day_summary['protein']:.2f}g, "
                    f"Carbs: {day_summary['carbohydrate']:.2f}g, "
                    f"Fat: {day_summary['fat']:.2f}g"
                )
        else:
            self.stderr.write(self.style.ERROR("Failed to generate any meal plan."))
        self.stdout.write(f"\nTotal generation time: {datetime.now() - start_time}")