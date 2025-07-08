import json
import re
import requests
from django.core.management.base import BaseCommand
from core.models import Recipe, Tag

def camel_to_snake(name):
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

def convert_keys(d):
    return {camel_to_snake(k): v for k, v in d.items()}

class Command(BaseCommand):
    help = "Update recipe tags by fetching scaled recipe details for meal division and using the base recipe's unsuitable diets."

    def add_arguments(self, parser):
        parser.add_argument('--scaled_recipe_url', type=str,
                            default="http://212.47.240.143:8080/recipes/getScaledRecipe",
                            help="Endpoint for scaled recipe details. Append ?scaledRecipeId=<id>")
        parser.add_argument('--scaled_from_url', type=str,
                            default="http://212.47.240.143:8080/recipes/getScaledFromRecipe",
                            help="Endpoint for scaled recipe ids. Append ?recipeId=<id>")
        parser.add_argument('--token', type=str, required=True,
                            help="API token to be used as the Bearer token")

    def handle(self, *args, **options):
        scaled_recipe_url = options["scaled_recipe_url"]
        scaled_from_url   = options["scaled_from_url"]
        token             = options["token"]
        headers = {"Authorization": f"Bearer {token}"}

        recipes = Recipe.objects.all()
        self.stdout.write(f"Found {recipes.count()} recipes to update tags.")

        for recipe in recipes:
            # Determine base id for fetching scaled recipes.
            base_id = getattr(recipe, "external_base_id", None) or recipe.external_id
            if not base_id:
                self.stdout.write(f"Skipping recipe '{recipe.title}' without a valid external id.")
                continue

            # Fetch the list of scaled recipes for this recipe.
            scaled_from_endpoint = f"{scaled_from_url}?recipeId={base_id}"
            self.stdout.write(f"\nFetching scaled recipe ids for '{recipe.title}' from {scaled_from_endpoint}")
            sf_resp = requests.get(scaled_from_endpoint, headers=headers)
            if sf_resp.status_code != 200:
                self.stderr.write(f"Failed to fetch scaled recipe ids for '{recipe.title}'. Status: {sf_resp.status_code}")
                continue
            try:
                scaled_ids = sf_resp.json()
            except json.JSONDecodeError:
                self.stderr.write(f"Scaled recipe ids response is not valid JSON for '{recipe.title}'.")
                continue

            if not scaled_ids:
                self.stdout.write(f"No scaled recipes available for '{recipe.title}'.")
                continue

            # Initialize a set for meal division tags.
            tags_to_add = set()

            # Iterate over all scaled recipes to collect meal division data.
            for scaled in scaled_ids:
                scaled_recipe_id = scaled.get("idScaledRecipe")
                if not scaled_recipe_id:
                    self.stdout.write(f"Skipping an entry for '{recipe.title}'; no scaledRecipe id found.")
                    continue

                scaled_recipe_endpoint = f"{scaled_recipe_url}?scaledRecipeId={scaled_recipe_id}"
                self.stdout.write(f"Fetching scaled recipe details from: {scaled_recipe_endpoint}")
                sr_resp = requests.get(scaled_recipe_endpoint, headers=headers)
                if sr_resp.status_code != 200:
                    self.stderr.write(f"Failed to fetch details for scaledRecipeId {scaled_recipe_id}.")
                    continue
                try:
                    scaled_data = sr_resp.json()
                except json.JSONDecodeError:
                    self.stderr.write(f"Scaled recipe details response is not valid JSON for scaledRecipeId {scaled_recipe_id}.")
                    continue

                scaled_data = convert_keys(scaled_data)
                self.stdout.write(f"Scaled data keys for id {scaled_recipe_id}: {list(scaled_data.keys())}")

                # Collect meal division tags from the scaled recipe.
                meal_div = scaled_data.get("meal_of_day_division", {})
                meal_of_day = meal_div.get("meal_of_day")
                meal_part   = meal_div.get("meal_part")
                if meal_of_day:
                    tags_to_add.add(meal_of_day.lower())
                if meal_part:
                    tags_to_add.add(meal_part.lower())

            # Now get unsuitable diets from the base recipe.
            # (Assumes the Recipe model has an attribute 'unsuitable_diets'; adjust if needed.)
            combined_unsuitables = set()
            if hasattr(recipe, "unsuitable_diets") and recipe.unsuitable_diets:
                combined_unsuitables.update(d.lower() for d in recipe.unsuitable_diets)
            self.stdout.write(f"Unsuitable diets for '{recipe.title}': {combined_unsuitables}")
            self.stdout.write(f"Preliminary tags (meal division) for '{recipe.title}': {tags_to_add}")

            # Process unsuitable diets.
            # Logic: if both 'vegan' and 'vegetarian' are present then nothing is added;
            # if one is missing, add the missing one; if both are missing, add both.
            if "vegan" in combined_unsuitables and "vegetarian" in combined_unsuitables:
                self.stdout.write(f"'{recipe.title}' is unsuitable for both vegan and vegetarian; not adding diet tags.")
            elif "vegan" not in combined_unsuitables and "vegetarian" in combined_unsuitables:
                tags_to_add.add("vegan")
            elif "vegetarian" not in combined_unsuitables and "vegan" in combined_unsuitables:
                tags_to_add.add("vegetarian")
            elif "vegan" not in combined_unsuitables and "vegetarian" not in combined_unsuitables:
                tags_to_add.add("vegan")
                tags_to_add.add("vegetarian")

            self.stdout.write(f"Final tags to add for '{recipe.title}': {tags_to_add}")

            # Clear existing tags and update with the new set.
            recipe.tags.clear()
            for tag_name in tags_to_add:
                tag, _ = Tag.objects.get_or_create(name=tag_name, user=recipe.user)
                recipe.tags.add(tag)
            recipe.save()
            self.stdout.write(f"Updated tags for recipe: {recipe.title}")

        self.stdout.write(self.style.SUCCESS("Recipe tags update complete."))