import json
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import Recipe, Ingredient, Tag, RecipeIngredient
from langchain_ollama.llms import OllamaLLM

User = get_user_model()

class Command(BaseCommand):
    help = "Generate a healthy recipe using AI and save it into the database."

    def handle(self, *args, **options):
        # Use first available user as the recipe creator
        user = User.objects.first()
        if not user:
            self.stderr.write("No user found in the database. Please create a user first.")
            return

        # Get existing ingredients
        existing_ingredients = list(Ingredient.objects.values_list('name', flat=True))
        if not existing_ingredients:
            self.stderr.write("No ingredients found in the database. Please add ingredients first.")
            return

        # Initialize the AI model
        model = OllamaLLM(
            model="llama3.2",
            base_url="http://ollama:11434",
            temperature=0.7
        )

        # Create the prompt directly as a string
        prompt = f"""You are a professional chef creating healthy recipes.
Generate a recipe using ONLY these ingredients: {', '.join(existing_ingredients)}.

Return the recipe in this exact JSON format:
{{
    "title": "Recipe name",
    "description": "Recipe description",
    "ingredients": [
        {{"name": "Ingredient1", "quantity": 100}},
        {{"name": "Ingredient2", "quantity": 50}}
    ],
    "instructions": "Step 1...\\nStep 2..."
}}

IMPORTANT:
- Only use the ingredients listed above
- Return ONLY the JSON with no additional text
- Quantity should be in grams
"""

        self.stdout.write("Generating recipe using AI...")
        try:
            # Invoke the model directly with the prompt
            output = model.invoke(prompt)
            self.stdout.write("AI output:")
            self.stdout.write(output)

            # Clean the output
            json_str = output.strip()
            if json_str.startswith("```json"):
                json_str = json_str[7:-3].strip()
            elif json_str.startswith("```"):
                json_str = json_str[3:-3].strip()

            recipe_data = json.loads(json_str)

            # Create the Recipe instance
            recipe = Recipe.objects.create(
                user=user,
                title=recipe_data["title"],
                description=recipe_data["description"],
                is_orderable=False,
                is_hidden=False
            )

            # Add healthy tag
            healthy_tag, _ = Tag.objects.get_or_create(name="Healthy", defaults={"user": user})
            recipe.tags.add(healthy_tag)

        # In your create_healthy_recipe.py command
            for ing in recipe_data["ingredients"]:
                name = ing["name"]
                quantity = ing["quantity"]
                ingredient = Ingredient.objects.filter(name__iexact=name).first()
                if ingredient:
                    RecipeIngredient.objects.create(
                        recipe=recipe,
                        ingredient=ingredient,
                        quantity=quantity
                    )
                else:
                    self.stdout.write(f"Skipping unknown ingredient: {name}")

            if added_ingredients == 0:
                raise ValueError("No valid ingredients found in the recipe")

            # Add instructions
            recipe.description += f"\n\nInstructions:\n{recipe_data['instructions']}"
            recipe.save()

            self.stdout.write(self.style.SUCCESS(f"Successfully created recipe: {recipe.title}"))

        except json.JSONDecodeError:
            self.stderr.write(self.style.ERROR("Failed to parse JSON output from AI"))
            self.stderr.write(f"Raw output was:\n{output}")
        except Exception as e:
            self.stderr.write(self.style.ERROR(f"Error: {str(e)}"))