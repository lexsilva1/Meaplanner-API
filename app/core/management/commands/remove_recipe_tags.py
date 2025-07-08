from django.core.management.base import BaseCommand
from core.models import Recipe

class Command(BaseCommand):
    help = "Remove all tags from every recipe"

    def handle(self, *args, **options):
        recipes = Recipe.objects.all()
        total = recipes.count()
        self.stdout.write(f"Found {total} recipes. Removing tags...")
        for recipe in recipes:
            recipe.tags.clear()
            recipe.save()
            self.stdout.write(f"Cleared tags for: {recipe.title}")
        self.stdout.write(self.style.SUCCESS("All recipe tags have been removed."))