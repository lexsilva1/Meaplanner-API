import json
import random
import re
import logging
from datetime import datetime, timedelta, date
from uuid import uuid4
from typing import Optional, Dict, Any

from django.core.management.base import BaseCommand
from django.db.models import Q, Sum, F, ExpressionWrapper, FloatField, Value, Count
from django.db.models.functions import Coalesce
from django.contrib.auth import get_user_model
from core.models import (
    MealPlan, MealPlanDay, Meal, Recipe, MealPart, MealPartRecipe, UserRecipeFeedback
)
from langchain_ollama.llms import OllamaLLM
from langchain.tools import StructuredTool
from langchain.agents import AgentExecutor, create_react_agent
from langchain.prompts import PromptTemplate
from pydantic import BaseModel, Field

# Set up basic logging
logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

User = get_user_model()

# --- Corrected Tag Structure Definition ---
MEAL_TAG_MAPPING = {
    'breakfast': 'breakfast',
    'lunch': 'lunch',
    'dinner': 'dinner',
    'mid_morning': 'mid_morning',
    'mid_afternoon': 'mid_afternoon',
    'supper': 'supper',
    'pre-workout': 'pre-workout',
    'post-workout': 'post-workout'
}

MEAL_PARTS_STRUCTURE = {
    "breakfast": [
        {"name": "main course", "is_required": True},
        {"name": "fruit", "is_required": False},
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

SIMPLE_MEALS = ['mid_morning', 'mid_afternoon', 'supper', 'pre-workout', 'post-workout']

# --- Helper Functions ---
def get_recipe_calories_safe(recipe):
    """Safely get recipe calories with reasonable limits"""
    try:
        calories = getattr(recipe, 'calculated_calories', None)
        if calories is None or calories == 0:
            calories = recipe.calories or 0

        if calories > 800:
            calories = 800
        elif calories < 50:
            calories = 200

        return float(calories)
    except Exception as e:
        logger.warning(f"Error getting calories for recipe {recipe.id}: {e}")
        return 200.0

def filter_recipes_by_tags(recipes_qs, meal_type, part_name=None):
    """Filter recipes based on meal type and part name tags"""
    meal_tag = MEAL_TAG_MAPPING.get(meal_type, meal_type)

    if part_name:
        if part_name == "main course":
            if meal_type == "breakfast":
                filtered_qs = recipes_qs.filter(tags__name__iexact="breakfast").distinct()
            else:
                filtered_qs = recipes_qs.filter(
                    tags__name__iexact=meal_tag
                ).filter(
                    tags__name__iexact="main course"
                ).distinct()
        elif part_name in ["fruit", "soup"]:
            filtered_qs = recipes_qs.filter(tags__name__iexact=part_name).distinct()
        else:
            filtered_qs = recipes_qs.filter(
                Q(tags__name__iexact=meal_tag) | Q(tags__name__iexact=part_name)
            ).distinct()
    else:
        filtered_qs = recipes_qs.filter(tags__name__iexact=meal_tag).distinct()

    return filtered_qs

def get_macro_targets(goal):
    if goal == 'weight_loss':
        return {'protein': 0.35, 'carbs': 0.40, 'fat': 0.25}
    elif goal == 'muscle_gain':
        return {'protein': 0.30, 'carbs': 0.50, 'fat': 0.20}
    else:
        return {'protein': 0.25, 'carbs': 0.50, 'fat': 0.25}

def distribute_calories(target_calories, meal_types):
    distribution = {
        'breakfast': 0.25, 'lunch': 0.35, 'dinner': 0.30,
        'mid_morning': 0.05, 'mid_afternoon': 0.05, 'supper': 0.10,
        'pre-workout': 0.05, 'post-workout': 0.05
    }

    result = {}
    for mt in meal_types:
        result[mt] = int(target_calories * distribution.get(mt, 0))
    return result

def score_recipe_simple(recipe, target_calories=None):
    """Simplified recipe scoring"""
    score = random.uniform(0.1, 0.3)

    if target_calories:
        recipe_calories = get_recipe_calories_safe(recipe)
        calorie_diff = abs(recipe_calories - target_calories)
        if calorie_diff < target_calories * 0.5:
            score += 0.5
        else:
            score += 0.2

    if hasattr(recipe, 'average_rating') and recipe.average_rating:
        score += recipe.average_rating / 10.0

    return score

def select_recipe_with_tags(recipes_qs, meal_type, part_name=None, target_calories=None):
    """Select recipe with proper tag filtering"""
    filtered_qs = filter_recipes_by_tags(recipes_qs, meal_type, part_name)

    if not filtered_qs.exists():
        logger.warning(f"No recipes found for meal_type='{meal_type}', part_name='{part_name}' with proper tags")
        return None

    candidates = list(filtered_qs)
    scored_recipes = [(r, score_recipe_simple(r, target_calories)) for r in candidates]
    scored_recipes.sort(key=lambda x: x[1], reverse=True)

    top_recipes = [r for r, _ in scored_recipes[:5]]
    selected = random.choice(top_recipes) if top_recipes else None

    if selected:
        logger.debug(f"Selected recipe '{selected.title}' (ID: {selected.id}) for {meal_type}/{part_name}")

    return selected

def calculate_nutrition_safe(day_obj):
    """Calculate nutrition with safety checks"""
    totals = {'calories': 0.0, 'protein': 0.0, 'carbohydrate': 0.0, 'fat': 0.0}

    for meal in day_obj.meals.all():
        for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
            recipe = mpr.recipe
            if recipe:
                calories = get_recipe_calories_safe(recipe)
                totals['calories'] += calories

                protein = min(recipe.protein or 0, 50)
                carbs = min(recipe.carbohydrate or 0, 100)
                fat = min(recipe.fat or 0, 40)

                totals['protein'] += protein
                totals['carbohydrate'] += carbs
                totals['fat'] += fat

    return totals

# --- Global Variables for Tool Functions (Fix for Pydantic issue) ---
_global_recipes_qs = None
_global_user = None
_global_daily_calories = None
_global_goal = None

def set_global_context(user, daily_calories, goal, recipes_qs):
    """Set global context for tool functions"""
    global _global_user, _global_daily_calories, _global_goal, _global_recipes_qs
    _global_user = user
    _global_daily_calories = daily_calories
    _global_goal = goal
    _global_recipes_qs = recipes_qs

# --- Tool Input Models ---
class RecipeSearchInput(BaseModel):
    meal_type: str = Field(description="The meal type (breakfast, lunch, dinner, etc.)")
    part_name: Optional[str] = Field(default=None, description="The meal part name (main course, fruit, soup)")
    target_calories: Optional[int] = Field(default=None, description="Target calories for the recipe")

class MealPlanBuilderInput(BaseModel):
    meal_plan_data: str = Field(description="JSON string containing the complete meal plan structure")

# --- Tool Functions ---
def search_recipes_tool(meal_type: str, part_name: Optional[str] = None, target_calories: Optional[int] = None) -> str:
    """Search for recipes by meal type and part"""
    try:
        if _global_recipes_qs is None:
            return "Error: Recipe database not available"

        filtered_qs = filter_recipes_by_tags(_global_recipes_qs, meal_type, part_name)
        recipes = list(filtered_qs[:10])  # Limit to 10 recipes

        if not recipes:
            return f"No recipes found for meal_type='{meal_type}', part_name='{part_name}'"

        result = []
        for recipe in recipes:
            calories = get_recipe_calories_safe(recipe)
            tags = [tag.name for tag in recipe.tags.all()]
            result.append({
                "id": recipe.id,
                "title": recipe.title,
                "calories": int(calories),
                "tags": tags
            })

        return json.dumps(result, indent=2)
    except Exception as e:
        return f"Error searching recipes: {str(e)}"

def build_meal_plan_tool(meal_plan_data: str) -> str:
    """Build and store a complete meal plan"""
    try:
        if not all([_global_user, _global_daily_calories, _global_goal, _global_recipes_qs]):
            return "Error: Missing required context (user, calories, goal, or recipes)"

        # Parse the meal plan data
        plan_data = json.loads(meal_plan_data)

        # Validate structure
        if 'days' not in plan_data or len(plan_data['days']) != 3:
            return "Error: Meal plan must have exactly 3 days"

        # Create meal plan
        meal_plan = MealPlan.objects.create(
            user=_global_user,
            title=f"AI Plan for {_global_user.name or _global_user.email}",
            description=f"AI generated meal plan. Target: {_global_daily_calories} kcal/day, Goal: {_global_goal}"
        )

        daily_summaries = []

        for day_idx, day_data in enumerate(plan_data['days']):
            day_type = day_data.get('day_type', 'regular')

            day_obj = MealPlanDay.objects.create(
                meal_plan=meal_plan,
                day_type=day_type,
                date=date.today() + timedelta(days=day_idx)
            )

            for meal_data in day_data.get('meals', []):
                meal_type = meal_data.get('meal_type')
                if not meal_type:
                    continue

                meal_obj = Meal.objects.create(
                    meal_plan_day=day_obj,
                    meal_type=meal_type
                )

                # Handle structured meals with parts
                if 'parts' in meal_data and meal_type in MEAL_PARTS_STRUCTURE:
                    for part_data in meal_data['parts']:
                        part_name = part_data.get('part_name')
                        recipe_id = part_data.get('recipe_id')

                        if not part_name:
                            continue

                        part_obj, _ = MealPart.objects.get_or_create(
                            name=part_name,
                            meal_type=meal_type,
                            defaults={"is_required": True}
                        )

                        if recipe_id:
                            try:
                                recipe = _global_recipes_qs.get(id=recipe_id)
                                MealPartRecipe.objects.create(
                                    meal=meal_obj,
                                    meal_part=part_obj,
                                    recipe=recipe,
                                    is_selected=True
                                )
                            except Recipe.DoesNotExist:
                                logger.warning(f"Recipe {recipe_id} not found")

                # Handle simple meals
                elif 'recipe_id' in meal_data:
                    recipe_id = meal_data.get('recipe_id')
                    if recipe_id:
                        try:
                            recipe = _global_recipes_qs.get(id=recipe_id)
                            part_obj, _ = MealPart.objects.get_or_create(
                                name="main",
                                meal_type=meal_type,
                                defaults={"is_required": True}
                            )
                            MealPartRecipe.objects.create(
                                meal=meal_obj,
                                meal_part=part_obj,
                                recipe=recipe,
                                is_selected=True
                            )
                        except Recipe.DoesNotExist:
                            logger.warning(f"Recipe {recipe_id} not found")

            # Calculate nutrition
            totals = {'calories': 0.0, 'protein': 0.0, 'carbohydrate': 0.0, 'fat': 0.0}
            for meal in day_obj.meals.all():
                for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
                    recipe = mpr.recipe
                    if recipe:
                        calories = get_recipe_calories_safe(recipe)
                        totals['calories'] += calories
                        totals['protein'] += min(recipe.protein or 0, 50)
                        totals['carbohydrate'] += min(recipe.carbohydrate or 0, 100)
                        totals['fat'] += min(recipe.fat or 0, 40)

            daily_summaries.append({
                'date': day_obj.date.isoformat(),
                'day_type': day_obj.day_type,
                'total_calories': round(totals['calories'], 2),
                'protein': round(totals['protein'], 2),
                'carbohydrate': round(totals['carbohydrate'], 2),
                'fat': round(totals['fat'], 2)
            })

        result = {
            'meal_plan_id': meal_plan.id,
            'title': meal_plan.title,
            'user_email': _global_user.email,
            'base_daily_calories': _global_daily_calories,
            'goal': _global_goal,
            'days': daily_summaries
        }

        return f"MEAL_PLAN_CREATED:{json.dumps(result)}"

    except Exception as e:
        return f"Error building meal plan: {str(e)}"

# --- RAG-based AI Generation with Agent ---
def generate_meal_plan_rag_agent(user, daily_calories, goal, model_name):
    """RAG-based AI meal plan generation using ReAct agent"""
    logger.info(f"Starting RAG-based AI generation for {user.email}")

    # Get recipes queryset
    recipes_qs = Recipe.objects.prefetch_related('tags')
    if hasattr(user, 'dietary_preferences') and user.dietary_preferences.exists():
        recipes_qs = recipes_qs.filter(tags__in=user.dietary_preferences.all()).distinct()

    if recipes_qs.count() < 10:
        raise ValueError(f"Not enough recipes: {recipes_qs.count()}")

    # Set global context for tools
    set_global_context(user, daily_calories, goal, recipes_qs)

    # Initialize LLM
    llm = OllamaLLM(model=model_name)

    # Create tools using StructuredTool
    search_tool = StructuredTool.from_function(
        func=search_recipes_tool,
        name="search_recipes",
        description="Search for recipes by meal type and part. Returns JSON list of recipes with ID, title, calories, and tags.",
        args_schema=RecipeSearchInput
    )

    build_tool = StructuredTool.from_function(
        func=build_meal_plan_tool,
        name="build_meal_plan",
        description="Build and store a complete meal plan. Expects JSON string with meal plan structure.",
        args_schema=MealPlanBuilderInput
    )

    tools = [search_tool, build_tool]

    # Create agent prompt
    prompt = PromptTemplate.from_template("""
You are a meal planning assistant. Create a complete meal plan with exactly 3 days for {daily_calories} calories per day.

IMPORTANT RULES:
1. ALWAYS use search_recipes tool to find available recipes before building the plan
2. Day 1: "regular" type with 6 meals: breakfast, lunch, dinner, mid_morning, mid_afternoon, supper
3. Day 2: "workout" type with 8 meals: all regular meals PLUS pre-workout, post-workout
4. Day 3: "rest" type with 6 meals: same as regular

TAG REQUIREMENTS:
- breakfast main course: search with meal_type="breakfast", part_name="main course"
- lunch/dinner main course: search with meal_type="lunch/dinner", part_name="main course"
- fruit parts: search with meal_type="breakfast", part_name="fruit"
- soup parts: search with meal_type="lunch/dinner", part_name="soup"
- Simple meals: search with meal_type only (mid_morning, mid_afternoon, supper, pre-workout, post-workout)

STRUCTURED MEALS (with parts):
- breakfast: main course (required) + fruit (optional)
- lunch: main course (required) + soup (optional)
- dinner: main course (required) + soup (optional)

PROCESS:
1. Search for recipes for each meal type and part systematically
2. Select appropriate recipe IDs from search results
3. Build the complete meal plan using build_meal_plan tool

JSON FORMAT for build_meal_plan:
{{
  "days": [
    {{
      "day_type": "regular",
      "meals": [
        {{"meal_type": "breakfast", "parts": [
          {{"part_name": "main course", "recipe_id": SELECTED_ID}},
          {{"part_name": "fruit", "recipe_id": SELECTED_ID_OR_NULL}}
        ]}},
        {{"meal_type": "lunch", "parts": [
          {{"part_name": "main course", "recipe_id": SELECTED_ID}},
          {{"part_name": "soup", "recipe_id": SELECTED_ID_OR_NULL}}
        ]}},
        {{"meal_type": "dinner", "parts": [
          {{"part_name": "main course", "recipe_id": SELECTED_ID}},
          {{"part_name": "soup", "recipe_id": SELECTED_ID_OR_NULL}}
        ]}},
        {{"meal_type": "mid_morning", "recipe_id": SELECTED_ID}},
        {{"meal_type": "mid_afternoon", "recipe_id": SELECTED_ID}},
        {{"meal_type": "supper", "recipe_id": SELECTED_ID}}
      ]
    }},
    {{
      "day_type": "workout",
      "meals": [
        // All regular meals PLUS:
        {{"meal_type": "pre-workout", "recipe_id": SELECTED_ID}},
        {{"meal_type": "post-workout", "recipe_id": SELECTED_ID}}
      ]
    }},
    {{
      "day_type": "rest",
      "meals": [
        // Same as regular day
      ]
    }}
  ]
}}

You have access to the following tools:

{tools}

Use the following format:

Question: the input question you must answer
Thought: you should always think about what to do
Action: the action to take, should be one of [{tool_names}]
Action Input: the input to the action
Observation: the result of the action
... (this Thought/Action/Action Input/Observation can repeat N times)
Thought: I now know the final answer
Final Answer: the final answer to the original question

Begin!

Question: Create a complete meal plan with exactly 3 days for {daily_calories} calories per day
Thought: I need to systematically search for recipes for each meal type and part, then build the complete meal plan.

{agent_scratchpad}""")

    # Create agent
    agent = create_react_agent(llm, tools, prompt)
    agent_executor = AgentExecutor(
        agent=agent,
        tools=tools,
        verbose=True,
        max_iterations=30,
        return_intermediate_steps=True,
        handle_parsing_errors=True
    )

    try:
        logger.info("Starting agent execution...")
        result = agent_executor.invoke({
            "daily_calories": daily_calories,
            "tools": [f"{tool.name}: {tool.description}" for tool in tools],
            "tool_names": [tool.name for tool in tools]
        })

        # Look for meal plan creation in the output or intermediate steps
        output = result.get("output", "")

        # Check if meal plan was created
        if "MEAL_PLAN_CREATED:" in output:
            json_start = output.find("MEAL_PLAN_CREATED:") + len("MEAL_PLAN_CREATED:")
            json_data = output[json_start:].strip()
            try:
                meal_plan_data = json.loads(json_data)
                logger.info(f"Successfully created meal plan via agent: {meal_plan_data['meal_plan_id']}")
                return meal_plan_data
            except json.JSONDecodeError as e:
                logger.error(f"Failed to parse meal plan JSON: {e}")

        # Check intermediate steps for meal plan creation
        for step in result.get("intermediate_steps", []):
            if len(step) >= 2 and "MEAL_PLAN_CREATED:" in str(step[1]):
                try:
                    step_output = str(step[1])
                    json_start = step_output.find("MEAL_PLAN_CREATED:") + len("MEAL_PLAN_CREATED:")
                    json_data = step_output[json_start:].strip()
                    meal_plan_data = json.loads(json_data)
                    logger.info(f"Found meal plan in intermediate steps: {meal_plan_data['meal_plan_id']}")
                    return meal_plan_data
                except (json.JSONDecodeError, KeyError):
                    continue

        raise ValueError(f"Agent did not successfully create a meal plan. Output: {output}")

    except Exception as e:
        logger.error(f"RAG agent execution failed: {e}")
        raise

# --- Deterministic Generation (Fallback) ---
def generate_meal_plan_deterministic(user, daily_calories, goal="maintenance"):
    """Deterministic meal plan generation with proper tag filtering"""
    logger.info(f"Starting deterministic generation for {user.email}")

    # Get recipes queryset
    recipes_qs = Recipe.objects.prefetch_related('tags')
    if hasattr(user, 'dietary_preferences') and user.dietary_preferences.exists():
        recipes_qs = recipes_qs.filter(tags__in=user.dietary_preferences.all()).distinct()

    if recipes_qs.count() < 10:
        raise ValueError(f"Not enough recipes: {recipes_qs.count()}")

    meal_plan = MealPlan.objects.create(
        user=user,
        title=f"Plan for {user.name or user.email}",
        description=f"Generated plan. Target: {daily_calories} kcal/day, Goal: {goal}"
    )

    day_types = ['regular', 'workout', 'rest']
    daily_summaries = []

    for day_idx, day_type in enumerate(day_types):
        day_obj = MealPlanDay.objects.create(
            meal_plan=meal_plan,
            day_type=day_type,
            date=date.today() + timedelta(days=day_idx)
        )

        # Define meals for each day type
        meal_types = ['breakfast', 'lunch', 'dinner', 'mid_morning', 'mid_afternoon', 'supper']
        if day_type == 'workout':
            meal_types.extend(['pre-workout', 'post-workout'])

        allocations = distribute_calories(daily_calories, meal_types)

        for meal_type in meal_types:
            target_calories = allocations.get(meal_type, 200)

            meal_obj = Meal.objects.create(
                meal_plan_day=day_obj,
                meal_type=meal_type
            )

            # Handle structured meals
            if meal_type in MEAL_PARTS_STRUCTURE:
                parts_defs = MEAL_PARTS_STRUCTURE[meal_type]
                part_target = target_calories / len(parts_defs)

                for part_def in parts_defs:
                    part_name = part_def['name']
                    is_required = part_def['is_required']

                    # Create meal part
                    part_obj, _ = MealPart.objects.get_or_create(
                        name=part_name,
                        meal_type=meal_type,
                        defaults={"is_required": is_required}
                    )

                    # Try to find recipe with proper tags
                    recipe = select_recipe_with_tags(recipes_qs, meal_type, part_name, part_target)

                    if recipe:
                        MealPartRecipe.objects.create(
                            meal=meal_obj,
                            meal_part=part_obj,
                            recipe=recipe,
                            is_selected=True
                        )
                    elif is_required:
                        logger.warning(f"No recipe found for required part '{part_name}' in '{meal_type}'")

            # Handle simple meals
            else:
                recipe = select_recipe_with_tags(recipes_qs, meal_type, None, target_calories)

                if recipe:
                    part_obj, _ = MealPart.objects.get_or_create(
                        name="main",
                        meal_type=meal_type,
                        defaults={"is_required": True}
                    )
                    MealPartRecipe.objects.create(
                        meal=meal_obj,
                        meal_part=part_obj,
                        recipe=recipe,
                        is_selected=True
                    )
                else:
                    logger.warning(f"No recipe found for simple meal '{meal_type}'")

        # Calculate nutrition
        nutrition = calculate_nutrition_safe(day_obj)
        daily_summaries.append({
            'date': day_obj.date.isoformat(),
            'day_type': day_obj.day_type,
            'total_calories': round(nutrition['calories'], 2),
            'protein': round(nutrition['protein'], 2),
            'carbohydrate': round(nutrition['carbohydrate'], 2),
            'fat': round(nutrition['fat'], 2)
        })

    result = {
        'meal_plan_id': meal_plan.id,
        'title': meal_plan.title,
        'user_email': user.email,
        'daily_calories': daily_calories,
        'goal': goal,
        'macro_targets': get_macro_targets(goal),
        'days': daily_summaries
    }

    logger.info(f"Successfully generated deterministic meal plan ID: {meal_plan.id}")
    return result

# --- Management Command ---
class Command(BaseCommand):
    help = "Generate a personalized meal plan with RAG-based AI and deterministic fallback."

    def add_arguments(self, parser):
        parser.add_argument('--user_email', type=str, required=True, help="User's email")
        parser.add_argument('--calories', type=int, required=True, help="Daily calorie target")
        parser.add_argument('--goal', type=str, default="maintenance",
                            choices=['weight_loss', 'muscle_gain', 'maintenance'], help="User's goal")
        parser.add_argument('--model', type=str, default="llama3:8b",
                            help="LLM model from Ollama")
        parser.add_argument('--force_deterministic', action='store_true',
                            help="Skip AI and use deterministic generation")

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

        # Try RAG-based AI first (unless forced to skip)
        if not force_deterministic:
            try:
                self.stdout.write(self.style.HTTP_INFO(f"Attempting RAG-based AI generation with {model_name}..."))
                result = generate_meal_plan_rag_agent(user, daily_calories, goal, model_name)
                generation_method = "RAG-based AI Agent"
                if isinstance(result, dict) and 'meal_plan_id' in result:
                    self.stdout.write(self.style.SUCCESS(f"✅ RAG AI plan created! ID: {result['meal_plan_id']}"))
                else:
                    self.stdout.write(self.style.SUCCESS(f"✅ RAG AI execution completed: {result}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ RAG AI generation failed: {e}"))
                self.stdout.write(self.style.WARNING("Falling back to deterministic generation..."))

        # Fallback to deterministic
        if result is None or not isinstance(result, dict) or 'meal_plan_id' not in result:
            try:
                self.stdout.write(self.style.HTTP_INFO("Using tag-aware deterministic generation..."))
                result = generate_meal_plan_deterministic(user, daily_calories, goal)
                generation_method = "Deterministic (Tag-Aware)"
                self.stdout.write(self.style.SUCCESS(f"✅ Deterministic plan created! ID: {result['meal_plan_id']}"))
            except Exception as e:
                self.stderr.write(self.style.ERROR(f"❌ Deterministic generation failed: {e}"))
                return

        # Display results
        if result and isinstance(result, dict) and 'meal_plan_id' in result:
            self.stdout.write(self.style.SUCCESS(f"\n📊 Meal Plan Summary ({generation_method}):"))
            self.stdout.write(f"  Plan ID: {result['meal_plan_id']}")
            self.stdout.write(f"  Title: {result['title']}")
            self.stdout.write(f"  User: {result['user_email']}")
            self.stdout.write(f"  Goal: {result['goal']}")
            self.stdout.write(f"  Target Daily Calories: {result.get('base_daily_calories') or result.get('daily_calories')}")

            if 'days' in result:
                self.stdout.write("\n📈 Daily Nutrition:")
                for day in result['days']:
                    self.stdout.write(
                        f"  {day['date']} ({day['day_type']:8s}): "
                        f"{day['total_calories']:6.0f} kcal, "
                        f"{day['protein']:5.1f}g protein, "
                        f"{day['carbohydrate']:5.1f}g carbs, "
                        f"{day['fat']:5.1f}g fat"
                    )
        else:
            self.stderr.write(self.style.ERROR("❌ Failed to generate any meal plan."))

        duration = datetime.now() - start_time
        self.stdout.write(f"\n⏱️  Total time: {duration}")