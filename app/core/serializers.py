"""
Serializers for the core API
"""

from rest_framework import serializers
from core.models import MealPlan, MealPlanDay, Meal, MealPart, MealPartRecipe, Recipe


class RecipeSimpleSerializer(serializers.ModelSerializer):
    """Simple recipe serializer for nested use"""

    class Meta:
        model = Recipe
        fields = ['id', 'title', 'calories', 'protein', 'carbohydrate', 'fat']


class MealPartRecipeSerializer(serializers.ModelSerializer):
    """Serializer for meal part recipes"""
    recipe = RecipeSimpleSerializer(read_only=True)
    meal_part_name = serializers.CharField(source='meal_part.name', read_only=True)

    class Meta:
        model = MealPartRecipe
        fields = ['id', 'meal_part_name', 'recipe', 'is_selected']


class MealSerializer(serializers.ModelSerializer):
    """Serializer for meals"""
    recipes = MealPartRecipeSerializer(source='mealpartrecipe_set', many=True, read_only=True)

    class Meta:
        model = Meal
        fields = ['id', 'meal_type', 'recipes']


class MealPlanDaySerializer(serializers.ModelSerializer):
    """Serializer for meal plan days"""
    meals = MealSerializer(many=True, read_only=True)

    class Meta:
        model = MealPlanDay
        fields = ['id', 'day_type', 'date', 'meals']


class MealPlanSerializer(serializers.ModelSerializer):
    """Serializer for meal plans"""
    days = MealPlanDaySerializer(many=True, read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)

    class Meta:
        model = MealPlan
        fields = ['id', 'title', 'description', 'user_email', 'creation_time', 'modification_time', 'days']


class CreateMealPlanSerializer(serializers.Serializer):
    """Serializer for creating meal plans"""

    GOAL_CHOICES = [
        ('weight_loss', 'Weight Loss'),
        ('muscle_gain', 'Muscle Gain'),
        ('maintenance', 'Maintenance'),
    ]

    calories = serializers.IntegerField(min_value=1200, max_value=5000, help_text="Daily calorie target")
    goal = serializers.ChoiceField(choices=GOAL_CHOICES, default='maintenance', help_text="User's fitness goal")
    model = serializers.CharField(max_length=100, default='llama3:8b', help_text="LLM model to use for generation")
    force_deterministic = serializers.BooleanField(default=False, help_text="Skip AI generation and use deterministic method")
