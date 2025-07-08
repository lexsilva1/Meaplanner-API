from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from core import models

# Custom meal ordering as per the list order
MEAL_ORDER = {
    'breakfast': 0,
    'mid_morning': 1,
    'lunch': 2,
    'mid_afternoon': 3,
    'dinner': 4,
    'supper': 5,
    'pre_workout': 6,
    'post_workout': 7,
}

class UserAdmin(BaseUserAdmin):
    ordering = ['id']
    list_display = ['email', 'name']
    search_fields = ['email', 'name']
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('name', 'height', 'weight', 'date_of_birth', 'physical_activity', 'dietary_preferences')}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser')}),
        ('Important dates', {'fields': ('last_login',)}),
    )
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )
    readonly_fields = ['last_login']
    filter_horizontal = ('dietary_preferences',)

class IngredientAdmin(admin.ModelAdmin):
    list_display = ['name', 'english_name', 'external_id', 'dose_gr', 'is_recipe', 'calories_display', 'groups_display']
    search_fields = ['name', 'english_name', 'external_id']
    list_filter = ['groups']
    filter_horizontal = ('groups',)

    def groups_display(self, obj):
        return ", ".join([group.name for group in obj.groups.all()])
    groups_display.short_description = "Groups"

    def calories_display(self, obj):
        if hasattr(obj, 'in100g') and obj.in100g:
            return f"{obj.in100g.energy:.2f} kcal / 100g"
        return "N/A"
    calories_display.short_description = "Calories (per 100g)"

class RecipeIngredientInline(admin.TabularInline):
    model = models.RecipeIngredient
    extra = 0
    verbose_name = "Recipe Ingredient"
    verbose_name_plural = "Recipe Ingredients"
    readonly_fields = ('amount_in_grams',)
    fields = ('ingredient', 'quantity', 'amount_in_grams',)

    def amount_in_grams(self, obj):
        if obj.ingredient and obj.quantity and obj.ingredient.dose_gr:
            return obj.quantity * obj.ingredient.dose_gr
        return None
    amount_in_grams.short_description = "Amount (grams)"

class RecipeAdmin(admin.ModelAdmin):
    list_display = ['id', 'title', 'user', 'external_id', 'creation_time', 'calories_display']
    search_fields = ['id', 'title', 'external_id', 'user__email']
    list_filter = ['tags']
    inlines = [RecipeIngredientInline]
    filter_horizontal = ('tags',)

    def calories_display(self, obj):
        return f"{obj.calories:.2f}"
    calories_display.short_description = "Calories"

# Inline for MealPlanDay to display meals, meal parts, and total nutrients per day
class MealPlanDayInline(admin.StackedInline):
    model = models.MealPlanDay
    extra = 0
    readonly_fields = (
        'day_type', 'date', 'meals_display',
        'total_calories', 'total_protein', 'total_carbohydrate', 'total_fat'
    )
    fields = (
        'day_type', 'date', 'meals_display',
        'total_calories', 'total_protein', 'total_carbohydrate', 'total_fat'
    )

    def meals_display(self, obj):
        meals = []
        # Sort meals according to our custom order
        sorted_meals = sorted(obj.meals.all(), key=lambda m: MEAL_ORDER.get(m.meal_type, 100))
        for meal in sorted_meals:
            parts = []
            for mpr in meal.mealpartrecipe_set.all():
                parts.append(f"{mpr.meal_part.name}: {mpr.recipe.title}")
            meals.append(f"{meal.get_meal_type_display()}: " + ", ".join(parts))
        return "; ".join(meals)
    meals_display.short_description = "Meals (Meal Parts: Recipes)"

    def total_calories(self, obj):
        total = 0
        for meal in obj.meals.all():
            for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
                if mpr.recipe and mpr.recipe.calories:
                    total += mpr.recipe.calories
        return total
    total_calories.short_description = "Total Calories"

    def total_protein(self, obj):
        total = 0
        for meal in obj.meals.all():
            for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
                if mpr.recipe:
                    total += mpr.recipe.protein
        return f"{total:.2f}"
    total_protein.short_description = "Total Protein"

    def total_carbohydrate(self, obj):
        total = 0
        for meal in obj.meals.all():
            for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
                if mpr.recipe:
                    total += mpr.recipe.carbohydrate
        return f"{total:.2f}"
    total_carbohydrate.short_description = "Total Carbohydrates"

    def total_fat(self, obj):
        total = 0
        for meal in obj.meals.all():
            for mpr in meal.mealpartrecipe_set.filter(is_selected=True):
                if mpr.recipe:
                    total += mpr.recipe.fat
        return f"{total:.2f}"
    total_fat.short_description = "Total Fat"

class MealPlanAdmin(admin.ModelAdmin):
    list_display = ['title', 'user', 'creation_time', 'modification_time']
    search_fields = ['title', 'description']
    inlines = [MealPlanDayInline]

admin.site.register(models.User, UserAdmin)
admin.site.register(models.Recipe, RecipeAdmin)
admin.site.register(models.Tag)
admin.site.register(models.Ingredient, IngredientAdmin)
admin.site.register(models.Group)
admin.site.register(models.MealPlan, MealPlanAdmin)