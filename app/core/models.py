import uuid
import os
from django.conf import settings
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin

def recipe_image_file_path(instance, filename):
    """Generate file path for new recipe image"""
    ext = os.path.splitext(filename)[1]
    filename = f"{uuid.uuid4()}{ext}"
    return os.path.join("uploads", "recipe", filename)

def ingredient_image_file_path(instance, filename):
    """Generate file path for new ingredient image"""
    ext = os.path.splitext(filename)[1]
    filename = f"{uuid.uuid4()}{ext}"
    return os.path.join("uploads", "ingredient", filename)

class UserManager(BaseUserManager):
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError("Users must have an email address")
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user

    def create_superuser(self, email, password):
        user = self.create_user(email, password)
        user.is_staff = True
        user.is_superuser = True
        user.save(using=self._db)
        return user

class User(AbstractBaseUser, PermissionsMixin):
    email = models.EmailField(max_length=255, unique=True)
    name = models.CharField(max_length=255, default="")
    # New fields for user profile
    height = models.FloatField(null=True, blank=True, help_text="Height in centimeters")
    weight = models.FloatField(null=True, blank=True, help_text="Weight in kilograms")
    date_of_birth = models.DateField(null=True, blank=True)

    PHYSICAL_ACTIVITY_CHOICES = [
        ('none', 'None'),
        ('light', 'Light'),
        ('moderate', 'Moderate'),
        ('high', 'High'),
    ]
    physical_activity = models.CharField(max_length=10, choices=PHYSICAL_ACTIVITY_CHOICES, default='none')
    dietary_preferences = models.ManyToManyField("Tag", blank=True, related_name="prefering_users")

    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)

    objects = UserManager()
    USERNAME_FIELD = "email"

    def get_full_name(self):
        return self.name or self.email

    def get_short_name(self):
        return self.name or self.email

    def __str__(self):
        return self.email

class Tag(models.Model):
    name = models.CharField(max_length=255, default="")
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)

    def __str__(self):
        return self.name

class Group(models.Model):
    id_group = models.IntegerField(unique=True, db_column="idGroup", default=0)
    name = models.CharField(max_length=255, default="")
    known_food_group_type = models.CharField(max_length=50, db_column="knownFoodGroupType", default="")

    def __str__(self):
        return self.name

class In100g(models.Model):
    ingredient = models.OneToOneField("Ingredient", on_delete=models.CASCADE, related_name="in100g", null=True, blank=True)
    energy = models.FloatField(default=0.0)
    carbohydrate = models.FloatField(default=0.0)
    cholesterol = models.FloatField(default=0.0)
    fat = models.FloatField(default=0.0)
    fiber = models.FloatField(default=0.0)
    protein = models.FloatField(default=0.0)
    water = models.FloatField(default=0.0)
    alcohol = models.FloatField(default=0.0)
    starch = models.FloatField(default=0.0)
    sugar = models.FloatField(default=0.0)
    salt = models.FloatField(default=0.0)
    vitamin_c = models.FloatField(db_column="vitaminC", default=0.0)
    thiamin = models.FloatField(default=0.0)
    ribo_flavin = models.FloatField(db_column="riboFlavin", default=0.0)
    niacin = models.FloatField(default=0.0)
    vitamin_b6 = models.FloatField(db_column="vitaminB6", default=0.0)
    folate = models.FloatField(default=0.0)
    vitamin_b12 = models.FloatField(db_column="vitaminB12", default=0.0)
    vitamin_a = models.FloatField(db_column="vitaminA", default=0.0)
    vitamin_d = models.FloatField(db_column="vitaminD", default=0.0)
    calcium = models.FloatField(default=0.0)
    iron = models.FloatField(default=0.0)
    magnesium = models.FloatField(default=0.0)
    phosphorus = models.FloatField(default=0.0)
    potassium = models.FloatField(default=0.0)
    zinc = models.FloatField(default=0.0)
    sodium = models.FloatField(default=0.0)
    saturated_fatty_acids = models.FloatField(default=0.0)
    mono_unsaturated_fatty_acids = models.FloatField(db_column="monoUnsaturatedFattyAcids", default=0.0)
    poly_unsaturated_fatty_acids = models.FloatField(db_column="polyUnsaturatedFattyAcids", default=0.0)
    trans_fatty_acids = models.FloatField(db_column="transFattyAcids", default=0.0)

    def __str__(self):
        return f"Energy: {self.energy}, Protein: {self.protein}"

class FattyAcids(models.Model):
    ingredient = models.OneToOneField("Ingredient", on_delete=models.CASCADE, related_name="fatty_acids", null=True, blank=True)
    saturated_fatty_acids = models.FloatField(db_column="saturatedFattyAcids", default=0.0)
    mono_unsaturated_fatty_acids = models.FloatField(db_column="monoUnsaturatedFattyAcids", default=0.0)
    poly_unsaturated_fatty_acids = models.FloatField(db_column="polyUnsaturatedFattyAcids", default=0.0)
    trans_fatty_acids = models.FloatField(db_column="transFattyAcids", default=0.0)

    def __str__(self):
        return f"Saturated: {self.saturated_fatty_acids}"

class Vitamins(models.Model):
    ingredient = models.OneToOneField("Ingredient", on_delete=models.CASCADE, related_name="vitamins", null=True, blank=True)
    vitamin_c = models.FloatField(db_column="vitaminC", default=0.0)
    thiamin = models.FloatField(default=0.0)
    ribo_flavin = models.FloatField(db_column="riboFlavin", default=0.0)
    niacin = models.FloatField(default=0.0)
    vitamin_b6 = models.FloatField(db_column="vitaminB6", default=0.0)
    folate = models.FloatField(default=0.0)
    vitamin_b12 = models.FloatField(db_column="vitaminB12", default=0.0)
    vitamin_a = models.FloatField(db_column="vitaminA", default=0.0)
    vitamin_d = models.FloatField(db_column="vitaminD", default=0.0)

    def __str__(self):
        return f"Vitamin C: {self.vitamin_c}"

class Minerals(models.Model):
    ingredient = models.OneToOneField("Ingredient", on_delete=models.CASCADE, related_name="minerals", null=True, blank=True)
    calcium = models.FloatField(default=0.0)
    iron = models.FloatField(default=0.0)
    magnesium = models.FloatField(default=0.0)
    phosphorus = models.FloatField(default=0.0)
    potassium = models.FloatField(default=0.0)
    zinc = models.FloatField(default=0.0)
    sodium = models.FloatField(default=0.0)

    def __str__(self):
        return f"Calcium: {self.calcium}"

class Ingredient(models.Model):
    id_ingredient = models.IntegerField(unique=True, db_column="idIngredient", default=0)
    hide_from_user = models.BooleanField(default=False, db_column="hideFromUser")
    name = models.CharField(max_length=255, default="")
    english_name = models.CharField(max_length=255, db_column="englishName", default="")
    original_name = models.CharField(max_length=255, db_column="originalName", default="")
    external_id = models.CharField(max_length=50, db_column="externalID", default="")
    is_recipe = models.BooleanField(default=False, db_column="isRecipe")
    dose_gr = models.FloatField(db_column="doseGr", default=0.0)
    is_liquid = models.BooleanField(db_column="isLiquid", default=False)
    image = models.ImageField(null=True, upload_to=ingredient_image_file_path)
    groups = models.ManyToManyField("Group", blank=True)
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, default=1)

    def __str__(self):
        return self.name

class Recipe(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, default="")
    description = models.TextField(blank=True, default="")
    link = models.CharField(max_length=255, blank=True, default="")
    tags = models.ManyToManyField("Tag", blank=True)
    ingredients = models.ManyToManyField("Ingredient", blank=True)  # Legacy field
    ingredients_v2 = models.ManyToManyField("Ingredient", through="RecipeIngredient", blank=True, related_name="recipes_v2")
    image = models.ImageField(null=True, upload_to=recipe_image_file_path)
    external_id = models.IntegerField(unique=True, null=True, blank=True)
    is_orderable = models.BooleanField(default=False)
    is_hidden = models.BooleanField(default=False)
    creation_time = models.DateTimeField(auto_now_add=True, null=True)
    modification_time = models.DateTimeField(auto_now=True, null=True)

    # Aggregated learning fields for AI-driven selection
    average_rating = models.FloatField(default=0.0)
    global_cooked_count = models.PositiveIntegerField(default=0)
    global_skip_count = models.PositiveIntegerField(default=0)
    preference_score = models.FloatField(default=0.0)

    def calculate_nutrition(self):
        nutrition = {
            'energy': 0.0,
            'protein': 0.0,
            'carbohydrate': 0.0,
            'fat': 0.0,
        }
        for recipe_ing in self.recipeingredient_set.all():
            ingredient = recipe_ing.ingredient
            quantity = recipe_ing.quantity
            actual_grams = quantity * ingredient.dose_gr if ingredient.dose_gr > 0 else quantity
            if hasattr(ingredient, 'in100g') and ingredient.in100g:
                ratio = actual_grams / 100.0
                in100g = ingredient.in100g
                nutrition['energy'] += in100g.energy * ratio
                nutrition['protein'] += in100g.protein * ratio
                nutrition['carbohydrate'] += in100g.carbohydrate * ratio
                nutrition['fat'] += in100g.fat * ratio
        return nutrition

    @property
    def calories(self):
        nutrition = self.calculate_nutrition()
        return nutrition.get('energy', 0)

    @property
    def protein(self):
        return self.calculate_nutrition().get('protein', 0)

    @property
    def carbohydrate(self):
        return self.calculate_nutrition().get('carbohydrate', 0)

    @property
    def fat(self):
        return self.calculate_nutrition().get('fat', 0)

    def __str__(self):
        return self.title

class RecipeIngredient(models.Model):
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE)
    ingredient = models.ForeignKey(Ingredient, on_delete=models.CASCADE)
    quantity = models.FloatField(default=0.0)
    notes = models.CharField(max_length=255, blank=True, default="")

    class Meta:
        unique_together = ('recipe', 'ingredient')

    def __str__(self):
        return f"{self.quantity}g of {self.ingredient.name} in {self.recipe.title}"

# New model: UserRecipeFeedback
class UserRecipeFeedback(models.Model):
    """
    Stores explicit and implicit feedback for a given user and recipe.
    """
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE)
    rating = models.IntegerField(null=True, blank=True)  # e.g., 1 to 5
    liked = models.BooleanField(null=True, blank=True)   # True if liked, False if disliked
    cooked_count = models.PositiveIntegerField(default=0)
    skip_count = models.PositiveIntegerField(default=0)
    last_interacted_on = models.DateTimeField(auto_now=True)

    class Meta:
        unique_together = ('user', 'recipe')

    def __str__(self):
        return f"Feedback by {self.user} on {self.recipe}"

class MealPlan(models.Model):
    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE)
    title = models.CharField(max_length=255, default="")
    description = models.TextField(blank=True, default="")
    recipes = models.ManyToManyField(Recipe, blank=True)  # Legacy field
    creation_time = models.DateTimeField(auto_now_add=True, null=True)
    modification_time = models.DateTimeField(auto_now=True, null=True)

    def __str__(self):
        return self.title

class MealPlanDay(models.Model):
    DAY_TYPE_CHOICES = [
         ('regular', 'Regular Day'),
         ('workout', 'Workout Day'),
         ('rest', 'Rest Day'),
    ]
    meal_plan = models.ForeignKey(MealPlan, on_delete=models.CASCADE, related_name="days")
    day_type = models.CharField(max_length=10, choices=DAY_TYPE_CHOICES, default='regular')
    date = models.DateField(null=True, blank=True)

    def __str__(self):
         return f"{self.get_day_type_display()} for {self.meal_plan.title}"

class Meal(models.Model):
    MEAL_TYPE_CHOICES = [
        ('breakfast', 'Breakfast'),
        ('mid_morning', 'Mid Morning'),
        ('lunch', 'Lunch'),
        ('mid_afternoon', 'Mid Afternoon'),
        ('dinner', 'Dinner'),
        ('supper', 'Supper'),
        ('pre_workout', 'Pre-Workout'),
        ('post_workout', 'Post-Workout'),
    ]
    meal_plan_day = models.ForeignKey(MealPlanDay, on_delete=models.CASCADE, related_name="meals")
    meal_type = models.CharField(max_length=20, choices=MEAL_TYPE_CHOICES)

    def __str__(self):
         return f"{self.get_meal_type_display()} on {self.meal_plan_day.get_day_type_display()}"

    def get_selected_recipes(self):
         return [mpr.recipe for mpr in self.mealpartrecipe_set.filter(is_selected=True)]

class MealPart(models.Model):
    name = models.CharField(max_length=50)
    is_required = models.BooleanField(default=False)
    meal_type = models.CharField(max_length=20, choices=Meal.MEAL_TYPE_CHOICES)

    def __str__(self):
         return self.name

class MealPartRecipe(models.Model):
    meal = models.ForeignKey(Meal, on_delete=models.CASCADE, related_name="mealpartrecipe_set")
    meal_part = models.ForeignKey(MealPart, on_delete=models.CASCADE)
    recipe = models.ForeignKey(Recipe, on_delete=models.CASCADE)
    is_selected = models.BooleanField(default=False)

    def __str__(self):
         return f"{self.meal_part.name}: {self.recipe.title} (Selected: {self.is_selected})"