import json
import re
import requests
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import Ingredient

User = get_user_model()

def camel_to_snake(name):
    s1 = re.sub(r"(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub(r"([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

def convert_keys(d):
    return {camel_to_snake(k): v for k, v in d.items()}

class Command(BaseCommand):
    help = "Update ingredient quantities based on external dose values from API, matching external_id to externalID."

    def add_arguments(self, parser):
        parser.add_argument('--user_email', type=str, default="admin@admin.com",
                            help="User email to filter ingredients.")
        parser.add_argument('--ingredients_api_url', type=str,
                            default="http://212.47.240.143:8080/ingredients/all",
                            help="API URL to fetch the list of ingredients")
        parser.add_argument('--token', type=str, required=True,
                            help="API token to be used as the Bearer token")

    def handle(self, *args, **options):
        user_email = options["user_email"]
        ingredients_api_url = options["ingredients_api_url"]
        token = options["token"]

        try:
            # We're not using 'user' further here but we check its existence.
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            self.stderr.write(f"User with email {user_email} does not exist.")
            return

        headers = {"Authorization": f"Bearer {token}"}
        self.stdout.write(f"Fetching ingredients from: {ingredients_api_url}")
        response = requests.get(ingredients_api_url, headers=headers)
        if response.status_code != 200:
            self.stderr.write(f"Failed to fetch ingredients. Status code: {response.status_code}")
            return

        try:
            ingredients_data = response.json()
        except json.JSONDecodeError:
            self.stderr.write("Ingredients response content is not valid JSON.")
            return

        updated_count = 0
        skipped_count = 0

        for rec in ingredients_data:
            # Convert keys from camelCase to snake_case.
            rec_converted = convert_keys(rec)
            # Try to get external_id from the converted version.
            external_id = rec_converted.get("external_id")
            # If not found, fallback to original key.
            if not external_id:
                external_id = rec.get("externalID")
            # Normalize by stripping whitespace and converting to uppercase.
            if external_id:
                external_id = str(external_id).strip().upper()
                # If the external_id starts with "IS", remove it and normalize numeric part.
                if external_id.startswith("IS"):
                    try:
                        external_id = str(int(external_id[2:]))
                    except ValueError:
                        pass
            # Retrieve the dose value (doseGr in external data)
            dose = rec_converted.get("dose_gr")
            if dose is None:
                dose = rec.get("doseGr")

            if not external_id:
                self.stderr.write("Skipping ingredient without external_id.")
                skipped_count += 1
                continue

            try:
                # Lookup using the normalized external_id.
                ingredient_obj = Ingredient.objects.get(external_id=external_id)
            except Ingredient.DoesNotExist:
                self.stdout.write(f"Ingredient with external_id '{external_id}' not found. Skipping.")
                skipped_count += 1
                continue

            if dose is not None:
                ingredient_obj.dose_gr = dose
                ingredient_obj.save()
                updated_count += 1
                self.stdout.write(f"Updated ingredient '{ingredient_obj.name}' (external_id: {external_id}) with dose_gr: {dose}")
            else:
                self.stderr.write(f"Ingredient '{ingredient_obj.name}' (external_id: {external_id}) has no dose_gr provided.")
                skipped_count += 1

        self.stdout.write(f"Update complete. {updated_count} ingredients updated, {skipped_count} ingredients skipped.")