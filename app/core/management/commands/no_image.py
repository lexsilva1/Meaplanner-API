import os
from django.core.management.base import BaseCommand
from django.core.files.base import ContentFile
from django.conf import settings
from core.models import Ingredient, Recipe

class Command(BaseCommand):
    help = "Update every Ingredient and Recipe image with the default image in the project root"

    def handle(self, *args, **options):
        # Assume the default image is named 'default_image.jpg' and resides in the project root.
        default_image_path = os.path.join(settings.BASE_DIR, 'default_image.jpg')
        if not os.path.exists(default_image_path):
            self.stderr.write(f"Default image {default_image_path} not found.")
            return

        with open(default_image_path, 'rb') as f:
            image_content = f.read()

        filename = os.path.basename(default_image_path)

        ingredients = Ingredient.objects.all()
        recipes = Recipe.objects.all()

        self.stdout.write(f"Updating {ingredients.count()} ingredients and {recipes.count()} recipes...")

        for ingredient in ingredients:
            # Update the image field with the default image.
            ingredient.image.save(filename, ContentFile(image_content), save=True)
            self.stdout.write(f"Updated image for Ingredient: {ingredient.name}")

        for recipe in recipes:
            recipe.image.save(filename, ContentFile(image_content), save=True)
            self.stdout.write(f"Updated image for Recipe: {recipe.title}")

        self.stdout.write(self.style.SUCCESS("Images have been successfully updated for all ingredients and recipes."))