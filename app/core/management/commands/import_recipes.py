import json
import re
import requests
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import Recipe, Ingredient, RecipeIngredient

User = get_user_model()

def camel_to_snake(name):
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

def convert_keys(d):
    return {camel_to_snake(k): v for k, v in d.items()}

class Command(BaseCommand):
    help = "Import recipes from external API with scaled details and store them in the database."

    def add_arguments(self, parser):
        parser.add_argument('--user_email', type=str, default="admin@admin.com",
                            help="User email to assign recipes to")
        parser.add_argument('--recipes_api_url', type=str,
                            default="http://212.47.240.143:8080/recipes/all",
                            help="API URL to fetch the list of recipes")
        parser.add_argument('--scaled_from_url', type=str,
                            default="http://212.47.240.143:8080/recipes/getScaledFromRecipe",
                            help="Endpoint for scaled recipe ids. Append ?recipeId=<id>")
        parser.add_argument('--scaled_recipe_url', type=str,
                            default="http://212.47.240.143:8080/recipes/getScaledRecipe",
                            help="Endpoint for scaled recipe details. Append ?scaledRecipeId=<id>")
        parser.add_argument('--token', type=str, required=True,
                            help="API token to be used as the Bearer token")

    def handle(self, *args, **options):
        user_email = options["user_email"]
        recipes_api_url = options["recipes_api_url"]
        scaled_from_url = options["scaled_from_url"]
        scaled_recipe_url = options["scaled_recipe_url"]
        token = options["token"]

        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            self.stderr.write(f"User with email {user_email} does not exist.")
            return

        headers = {"Authorization": f"Bearer {token}"}

        self.stdout.write(f"Fetching recipes from: {recipes_api_url}")
        recipes_response = requests.get(recipes_api_url, headers=headers)
        if recipes_response.status_code != 200:
            self.stderr.write(f"Failed to fetch recipes. Status code: {recipes_response.status_code}")
            return

        try:
            recipes_data = recipes_response.json()
        except json.JSONDecodeError:
            self.stderr.write("Recipes response content is not valid JSON.")
            return

        # Iterate over each recipe (assumed to be a list)
        for rec in recipes_data:
            rec = convert_keys(rec)
            recipe_id = rec.get("id_recipe")
            if not recipe_id:
                self.stdout.write("Skipping recipe without 'id_recipe'.")
                continue

            self.stdout.write(f"Processing recipe id: {recipe_id} - {rec.get('name', '')}")

            # Call the endpoint to fetch scaled recipe ids
            scaled_from_endpoint = f"{scaled_from_url}?recipeId={recipe_id}"
            scaled_from_resp = requests.get(scaled_from_endpoint, headers=headers)
            if scaled_from_resp.status_code != 200:
                self.stderr.write(f"Failed to fetch scaled recipe ids for recipe id {recipe_id}.")
                continue

            try:
                scaled_ids = scaled_from_resp.json()
            except json.JSONDecodeError:
                self.stderr.write("Scaled recipe ids response is not valid JSON.")
                continue

            if not scaled_ids:
                self.stdout.write(f"No scaled recipe available for recipe id {recipe_id}.")
                continue

            first_scaled = scaled_ids[0]
            scaled_recipe_id = first_scaled.get("idScaledRecipe")
            if not scaled_recipe_id:
                self.stdout.write(f"Skipping recipe id {recipe_id}; no scaledRecipe id found.")
                continue

            # Get full scaled recipe details.
            scaled_recipe_endpoint = f"{scaled_recipe_url}?scaledRecipeId={scaled_recipe_id}"
            self.stdout.write(f"Fetching scaled recipe details from: {scaled_recipe_endpoint}")
            scaled_recipe_resp = requests.get(scaled_recipe_endpoint, headers=headers)
            if scaled_recipe_resp.status_code != 200:
                self.stderr.write(f"Failed to fetch details for scaledRecipeId {scaled_recipe_id}.")
                continue

            try:
                scaled_data = scaled_recipe_resp.json()
            except json.JSONDecodeError:
                self.stderr.write("Scaled recipe details response is not valid JSON.")
                continue

            # Convert keys from camelCase to snake_case.
            scaled_data = convert_keys(scaled_data)

            # Use the id_scaled_recipe from the scaled data as external_id.
            ext_id = scaled_data.get("id_scaled_recipe")
            if not ext_id:
                self.stderr.write(f"Scaled data for recipe id {recipe_id} does not contain 'id_scaled_recipe'.")
                continue

            # The scaled_data contains a nested "recipe" object with base details.
            base_recipe = scaled_data.get("recipe", {})
            recipe_fields = {
                "title": base_recipe.get("name", "Untitled Recipe"),
                "description": base_recipe.get("description", ""),
                "link": rec.get("link", ""),
                "is_orderable": rec.get("is_orderable", False),
                "is_hidden": rec.get("is_hidden", False),
                "external_id": ext_id  # Use the scaled recipe id as external_id.
            }

            recipe, created = Recipe.objects.update_or_create(
                external_id=recipe_fields["external_id"],
                defaults={**recipe_fields, "user": user}
            )
            action = "Created" if created else "Updated"
            self.stdout.write(f"{action} recipe: {recipe.title}")

            # Process scaled ingredients.
            # Expecting a key "scaled_recipe_ingredients", a list of items each having "quantity" and an embedded "ingredient" object.
            scaled_ingredients = scaled_data.get("scaled_recipe_ingredients", [])
            # Clear any existing RecipeIngredient entries for this recipe.
            RecipeIngredient.objects.filter(recipe=recipe).delete()
            for item in scaled_ingredients:
                quantity = item.get("quantity", 0)
                ingr_data = item.get("ingredient", {})
                # Instead of searching by name, we search by the externalID provided by the ingredient data.
                ext_ing = ingr_data.get("externalID") or ingr_data.get("external_id")
                if not ext_ing:
                    self.stdout.write("Skipping scaled ingredient with no externalID.")
                    continue
                # Normalize the externalID: strip whitespace, uppercase, and remove the "IS" prefix.
                ext_ing = str(ext_ing).strip().upper()
                if ext_ing.startswith("IS"):
                    try:
                        ext_ing = str(int(ext_ing[2:]))
                    except ValueError:
                        pass
                # Look up the ingredient by the normalized external_id.
                ingredient = Ingredient.objects.filter(external_id=ext_ing).first()
                if ingredient:
                    RecipeIngredient.objects.create(
                        recipe=recipe,
                        ingredient=ingredient,
                        quantity=quantity
                    )
                else:
                    self.stdout.write(f"Skipping unknown ingredient with externalID: {ext_ing}")

        self.stdout.write("Recipes import complete.")