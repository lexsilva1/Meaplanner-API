import json
import logging
import re
from datetime import datetime, timedelta, date

from django.core.management.base import BaseCommand
from django.db import transaction
from django.db.models import Sum, F, Value, FloatField, ExpressionWrapper
from core.models import MealPlan, MealPlanDay, Meal, MealPart, MealPartRecipe, Recipe, UserRecipeFeedback
from langchain_ollama.llms import OllamaLLM

# Set up basic logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

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
            return json.loads(potential_json)
        except json.JSONDecodeError as e:
            logger.warning(f"JSON in code block could not be parsed: {e}")
    json_like_match = re.search(r'(\{.*\})', text, re.DOTALL)
    if json_like_match:
        potential_json = json_like_match.group(1)
        try:
            return json.loads(potential_json)
        except json.JSONDecodeError:
            fixed = fix_invalid_json_keys(potential_json)
            return json.loads(fixed)
    logger.error("Could not extract valid JSON from the input.")
    raise ValueError("Invalid JSON input.")

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

# This function converts an existing meal plan into our JSON schema.
# Instead of placeholders, it uses dynamic values if available (assuming meal_plan has attributes base_daily_calories and macro_targets).
def mealplan_to_json(meal_plan):
    days = []
    for day in meal_plan.days.all().order_by('date'):
        meals = []
        for meal in day.meals.all():
            parts = []
            for mpr in meal.mealpartrecipe_set.filter(is_selected=True).select_related('recipe', 'meal_part'):
                parts.append({
                    "name": mpr.meal_part.name,
                    "selected_recipe_id": mpr.recipe.id if mpr.recipe else None
                })
            meals.append({
                "meal_type": meal.meal_type,
                "allocated_calories_for_meal": 0,  # Not used in optimization prompt
                "parts": parts
            })
        days.append({
            "date": day.date.isoformat() if day.date else date.today().isoformat(),
            "day_type": day.day_type,
            "target_calories_for_day": getattr(meal_plan, "base_daily_calories", 3000),  # default to 3000 if not set
            "meals": meals
        })
    plan_json = {
        "meal_plan_title": meal_plan.title,
        "user_email": meal_plan.user.email if hasattr(meal_plan, "user") else "",
        "base_daily_calories": getattr(meal_plan, "base_daily_calories", 3000),
        "goal": getattr(meal_plan, "goal", "maintenance"),
        "macro_targets": getattr(meal_plan, "macro_targets", {"protein": 0.25, "carbs": 0.50, "fat": 0.25}),
        "days": days
    }
    return plan_json

@transaction.atomic
def update_meal_plan_from_json(meal_plan, optimized_json, recipes_qs):
    meal_plan.title = optimized_json.get("meal_plan_title", meal_plan.title)
    meal_plan.description = "Optimized meal plan (AI)."
    meal_plan.save()
    # Delete existing days (with meals) before re-creating them
    meal_plan.days.all().delete()
    from core.models import MealPlanDay, Meal, MealPart, MealPartRecipe
    for day_data in optimized_json.get("days", []):
        try:
            day_date_str = day_data.get("date")
            current_day_date = datetime.strptime(day_date_str, "%Y-%m-%d").date() if day_date_str else date.today()
        except ValueError:
            current_day_date = date.today()
        day_obj = MealPlanDay.objects.create(
            meal_plan=meal_plan,
            day_type=day_data.get("day_type", "regular"),
            date=current_day_date
        )
        for meal_data in day_data.get("meals", []):
            meal_type_str = meal_data.get("meal_type")
            if not meal_type_str:
                continue
            meal_obj = Meal.objects.create(meal_plan_day=day_obj, meal_type=meal_type_str)
            for part_data in meal_data.get("parts", []):
                part_name_str = part_data.get("name")
                selected_recipe_id = part_data.get("selected_recipe_id")
                if not part_name_str or selected_recipe_id is None:
                    continue
                try:
                    recipe_obj = recipes_qs.get(id=selected_recipe_id)
                except Recipe.DoesNotExist:
                    continue
                part_obj, _ = MealPart.objects.get_or_create(
                    name=part_name_str, meal_type=meal_type_str,
                    defaults={"is_required": True}
                )
                MealPartRecipe.objects.create(
                    meal=meal_obj, meal_part=part_obj,
                    recipe=recipe_obj, is_selected=True
                )
    return meal_plan

# --- Validation Functions (with detailed error reporting) ---
def validate_ai_meal_plan(ai_json_data, daily_calories, recipes_qs_with_calories):
    errors = []
    required_day_types = {'regular', 'workout', 'rest'}
    found_day_types = {day.get('day_type', '').lower() for day in ai_json_data.get('days', [])}
    if found_day_types != required_day_types:
        errors.append(f"Meal plan must include exactly one regular, one workout, and one rest day. Found: {found_day_types}")

    regular_meal_types = ['breakfast', 'lunch', 'dinner', 'mid_morning', 'mid_afternoon', 'supper']
    workout_meal_types = regular_meal_types + ['pre-workout', 'post-workout']

    for day_idx, day_data in enumerate(ai_json_data.get('days', []), start=1):
        day_type = day_data.get('day_type', '').lower()
        target = daily_calories
        if day_type == 'workout':
            target = int(daily_calories * 1.20)
        elif day_type == 'rest':
            target = int(daily_calories * 0.90)
        expected_meals = workout_meal_types if day_type == 'workout' else regular_meal_types
        meal_types_in_day = {meal.get('meal_type', '').lower() for meal in day_data.get('meals', [])}
        missing_meals = set(expected_meals) - meal_types_in_day
        if missing_meals:
            errors.append(f"Day {day_idx} ({day_type}): Missing required meals: {missing_meals}")
        day_calories = 0.0
        for meal_idx, meal_data in enumerate(day_data.get('meals', []), start=1):
            meal_type = meal_data.get('meal_type', '').lower()
            parts = meal_data.get('parts', [])
            # For breakfast, lunch, dinner, require at least a 'main course'
            if meal_type in ['breakfast', 'lunch', 'dinner']:
                required_parts = {'main course'}
                part_names = {part.get('name', '').lower() for part in parts}
                missing_parts = required_parts - part_names
                if missing_parts:
                    errors.append(f"Day {day_idx} ({day_type}), Meal {meal_type}: Missing required parts: {missing_parts}")
            # Sum calories for each part (if available)
            for part in parts:
                recipe_id = part.get('selected_recipe_id')
                if recipe_id is not None:
                    try:
                        recipe = recipes_qs_with_calories.get(id=recipe_id)
                        day_calories += recipe.calories or 0.0
                    except Recipe.DoesNotExist:
                        errors.append(f"Day {day_idx} ({day_type}), Meal {meal_type}: Invalid recipe ID {recipe_id}")
        calorie_tolerance = 0.15
        if not (target * (1 - calorie_tolerance) <= day_calories <= target * (1 + calorie_tolerance)):
            errors.append(f"Day {day_idx} ({day_type}): Total calories {day_calories:.2f} outside target {target} ±15%")
    return errors

# --- Granular Fix Function with Reassignment of Missing Recipes ---
def fix_ai_meal_plan(ai_json_data, user, daily_calories, recipes_qs_with_calories, user_feedback_cache):
    logger.info("Running advanced fixes on the optimized meal plan.")
    # For each day in the plan, ensure all required meals exist and all required parts have a recipe.
    for day_data in ai_json_data.get("days", []):
        day_type = day_data.get("day_type", "").lower()
        target = daily_calories
        if day_type == "workout":
            target = int(daily_calories * 1.20)
        elif day_type == "rest":
            target = int(daily_calories * 0.90)
        # Determine required meals based on day type
        required_meals = (["breakfast", "lunch", "dinner", "mid_morning", "mid_afternoon", "supper"]
                          if day_type != "workout" else
                          ["breakfast", "lunch", "dinner", "mid_morning", "mid_afternoon", "supper", "pre-workout", "post-workout"])
        existing_meals = {meal.get("meal_type", "").lower() for meal in day_data.get("meals", [])}
        for meal in required_meals:
            if meal not in existing_meals:
                allocated = distribute_calories(target, [meal]).get(meal, 0)
                day_data.setdefault("meals", []).append({
                    "meal_type": meal,
                    "allocated_calories_for_meal": allocated,
                    "parts": []
                })
        # For each meal, ensure required parts exist; for breakfast, lunch, dinner the required part is 'main course'
        for meal in day_data.get("meals", []):
            meal_type = meal.get("meal_type", "").lower()
            if meal_type in ["breakfast", "lunch", "dinner"]:
                required_parts = {"main course"}
                existing_parts = {part.get("name", "").lower() for part in meal.get("parts", [])}
                missing_parts = required_parts - existing_parts
                for part in missing_parts:
                    # Re-query candidate recipe based on meal type and part tag
                    candidate = recipes_qs_with_calories.filter(tags__name__iexact=meal_type, tags__name__iexact=part).first()
                    meal.setdefault("parts", []).append({
                        "name": part,
                        "selected_recipe_id": candidate.id if candidate else None
                    })
            else:
                # For other meals such as simple or workout meals, ensure at least one part exists.
                if not meal.get("parts"):
                    candidate = recipes_qs_with_calories.filter(tags__name__iexact=meal.get("meal_type", ""), tags__name__iexact="main course").first()
                    meal["parts"] = [{
                        "name": "main",
                        "selected_recipe_id": candidate.id if candidate else None
                    }]
    return ai_json_data

# --- Management Command ---
class Command(BaseCommand):
    help = ("Optimize an existing meal plan using the AI agent and update it in the database. "
            "Provide the mealplan_id and optionally the LLM model name.")

    def add_arguments(self, parser):
        parser.add_argument(
            '--mealplan_id',
            type=int,
            required=True,
            help="ID of the meal plan to optimize and update."
        )
        parser.add_argument(
            '--model',
            type=str,
            default="llama3",
            help="LLM model from Ollama to use (default: llama3)."
        )

    def handle(self, *args, **options):
        model_name = options["model"]
        mealplan_id = options["mealplan_id"]
        try:
            meal_plan = MealPlan.objects.get(id=mealplan_id)
        except MealPlan.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"MealPlan with id {mealplan_id} not found."))
            return

        self.stdout.write(self.style.HTTP_INFO("Exporting existing meal plan to JSON..."))
        mealplan_json = mealplan_to_json(meal_plan)
        prompt = (
            "You are an expert meal planning assistant. Optimize the following meal plan so that it meets all the pre-established rules "
            "(calorie targets, required meals, nutritional balance, and valid recipe selections). "
            "Return the optimized meal plan in the SAME JSON format with no extra commentary.\n\n"
            "Meal Plan Input:\n"
            f"{json.dumps(mealplan_json, indent=2)}"
        )

        self.stdout.write(self.style.HTTP_INFO("Invoking LLM to optimize the meal plan..."))
        try:
            llm = OllamaLLM(model=model_name)
            response = llm.invoke(prompt)
            optimized_plan = extract_json(response)
            self.stdout.write(self.style.SUCCESS("Optimized meal plan received from LLM."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Optimization failed: {e}"))
            return

        # Build an efficient candidate query with prefetching; caching candidate results for improvements.
        recipes_qs = Recipe.objects.all().prefetch_related("tags")
        candidate_qs = recipes_qs.annotate(
            calculated_calories=F('calories')  # Assuming calories are stored directly; adjust if using a calculation as before.
        ).distinct()
        # Load real user feedback for integration.
        user_feedback_cache = {fb.recipe_id: fb for fb in UserRecipeFeedback.objects.filter(user=meal_plan.user)}
        # Use optimized plan's base daily calories if available; otherwise default to 3000.
        daily_calories = optimized_plan.get("base_daily_calories", 3000)
        validation_errors = validate_ai_meal_plan(optimized_plan, daily_calories, candidate_qs)
        if validation_errors:
            self.stdout.write(self.style.WARNING(f"Validation errors: {validation_errors}. Attempting granular fixes..."))
            optimized_plan = fix_ai_meal_plan(optimized_plan, meal_plan.user, daily_calories, candidate_qs, user_feedback_cache)
            validation_errors = validate_ai_meal_plan(optimized_plan, daily_calories, candidate_qs)
            if validation_errors:
                self.stderr.write(self.style.ERROR(f"Optimized meal plan validation failed after fixes: {validation_errors}"))
                return

        self.stdout.write(self.style.HTTP_INFO("Updating meal plan in database..."))
        try:
            updated_plan = update_meal_plan_from_json(meal_plan, optimized_plan, recipes_qs)
            self.stdout.write(self.style.SUCCESS(f"Meal plan ID {updated_plan.id} updated successfully."))
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Failed to update meal plan: {e}"))