import json
import re
import requests
import pandas as pd
from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model
from core.models import Ingredient, In100g, FattyAcids, Vitamins, Minerals, Group
from django.db import models # Import models to use Max aggregation

User = get_user_model()

def camel_to_snake(name):
    """Converts camelCase strings to snake_case."""
    s1 = re.sub("(.)([A-Z][a-z]+)", r"\1_\2", name)
    return re.sub("([a-z0-9])([A-Z])", r"\1_\2", s1).lower()

def convert_keys(d):
    """Converts all keys in a dictionary from camelCase to snake_case."""
    return {camel_to_snake(k): v for k, v in d.items()}

class Command(BaseCommand):
    help = "Import ingredients from an Excel file, creating groups from each group's column cell, and adding them to the ingredient."

    def add_arguments(self, parser):
        parser.add_argument('--user_email', type=str, default="xanosilva@gmail.com",
                            help="User email to assign ingredients to")
        parser.add_argument('--file_path', type=str, required=True,
                            help="Path to the Excel file to import ingredients from")

    def handle(self, *args, **options):
        user_email = options["user_email"]
        file_path = options["file_path"]

        try:
            user = User.objects.get(email=user_email)
        except User.DoesNotExist:
            self.stderr.write(f"Error: User with email {user_email} does not exist. Please create the user first.")
            return

        # Read and process Excel file
        try:
            # Read excel without header (all rows as data)
            df = pd.read_excel(file_path, header=None)

            # Determine the number of header rows to skip.
            # Based on previous errors, index 0 is a header row containing descriptive text.
            num_header_rows_to_skip = 1

            # Skip the header rows and create a copy to avoid SettingWithCopyWarning
            df = df.iloc[num_header_rows_to_skip:].copy()

            # Reset index after skipping rows, so the first data row has index 0
            df.reset_index(drop=True, inplace=True)

            # Map the Excel column indices to meaningful names for the *data rows*.
            # These indices correspond to the original Excel columns' positions.
            column_mapping = {
                0: 'external_id_raw', # The original ID from Excel (e.g., 725, 712)
                1: 'name',            # Ingredient name (e.g., 'Vinho generoso do Porto, seco')
                2: 'group_description', # Corresponds to column C in Excel
                3: 'subgroup_1',      # Corresponds to column D in Excel
                4: 'subgroup_2',      # Corresponds to column E in Excel
                5: 'energy',
                6: 'carbohydrate',
                7: 'cholesterol',
                8: 'fat',
                9: 'fiber',
                10: 'protein',
                11: 'water',
                12: 'alcohol',
                13: 'starch',
                14: 'sugar',
                15: 'salt',
                16: 'vitamin_c',
                17: 'thiamin',
                18: 'ribo_flavin',
                19: 'niacin',
                20: 'vitamin_b6',
                21: 'folate',
                22: 'vitamin_b12',
                23: 'vitamin_a',
                24: 'vitamin_d',
                25: 'calcium',
                26: 'iron',
                27: 'magnesium',
                28: 'phosphorus',
                29: 'potassium',
                30: 'zinc',
                31: 'sodium',
                32: 'saturated_fatty_acids',
                33: 'mono_unsaturated_fatty_acids',
                34: 'poly_unsaturated_fatty_acids',
                35: 'trans_fatty_acids',
                # Add mappings for any other relevant columns if they exist in your models.
            }

            # Rename columns based on the mapping.
            df.rename(columns=column_mapping, inplace=True)

            # Drop any columns that were not explicitly mapped and are not needed.
            current_columns = df.columns.tolist()
            columns_to_drop = [col for col in current_columns if col not in column_mapping.values()]
            df.drop(columns=columns_to_drop, inplace=True, errors='ignore')

        except Exception as e:
            self.stderr.write(f"Error: Failed to read or process Excel file '{file_path}': {e}")
            return

        # Get allowed model fields
        ingredient_model_fields = {field.name for field in Ingredient._meta.fields}
        in100g_model_fields = {f.name for f in In100g._meta.fields if f.name not in ("id", "ingredient")}

        # Initialize counter for internal ingredient IDs.
        internal_ingredient_id_counter = 1

        # Get the maximum existing id_group to ensure new ones are unique.
        # This is done once before the loop for efficiency.
        # Note: In a highly concurrent environment, this might lead to race conditions
        # if multiple import scripts run simultaneously. For a management command, it's generally fine.
        max_group_id_result = Group.objects.all().aggregate(models.Max('id_group'))
        current_max_group_id = max_group_id_result['id_group__max'] if max_group_id_result['id_group__max'] is not None else 0


        for index, row in df.iterrows():
            raw_data = row.to_dict()

            # --- Process Ingredient Data ---
            ingredient_data = {}

            # Generate a new internal ID for the Ingredient model's primary key
            ingredient_data['id_ingredient'] = internal_ingredient_id_counter
            internal_ingredient_id_counter += 1

            # Map external_id from Excel's first column (now named 'external_id_raw')
            external_id_val = raw_data.get('external_id_raw')
            if pd.isna(external_id_val):
                self.stderr.write(f"Row skipped (index {index}): Missing or NaN value for external ID. Data: {raw_data}")
                continue
            try:
                # Convert external_id to string as per your model definition (CharField).
                # Handle cases where it might be read as float (e.g., 725.0) and convert to int first.
                if pd.notna(external_id_val) and isinstance(external_id_val, (int, float)):
                    ingredient_data['external_id'] = str(int(external_id_val))
                else:
                    ingredient_data['external_id'] = str(external_id_val)
            except ValueError:
                self.stderr.write(f"Row skipped (index {index}): Could not convert external ID '{external_id_val}' to string. Data: {raw_data}")
                continue

            # Map ingredient name
            ingredient_data['name'] = str(raw_data.get('name', '')).strip()
            if not ingredient_data['name']:
                self.stderr.write(f"Row skipped (index {index}): Missing ingredient name. Data: {raw_data}")
                continue

            # Default values for other Ingredient fields not directly from Excel
            ingredient_data['english_name'] = ""
            ingredient_data['original_name'] = raw_data.get('name', '') # Using name as original name
            ingredient_data['hide_from_user'] = False
            ingredient_data['is_recipe'] = False
            ingredient_data['dose_gr'] = 0.0
            ingredient_data['is_liquid'] = False
            # image and groups are not handled by this import script, will default to None/empty

            # Filter ingredient data to only include fields present in the Ingredient model
            safe_ingredient_data = {k: v for k, v in ingredient_data.items() if k in ingredient_model_fields}
            safe_ingredient_data['user'] = user # Assign the user

            # --- Process In100g Data ---
            in100g_data = {}
            for field_name in in100g_model_fields:
                value = raw_data.get(field_name)
                try:
                    in100g_data[field_name] = float(value) if pd.notna(value) else 0.0
                except (ValueError, TypeError):
                    self.stderr.write(f"Warning: Could not convert '{value}' to float for {field_name} at row index {index}. Setting to 0.0.")
                    in100g_data[field_name] = 0.0

            try:
                # Update or create the Ingredient instance.
                # Using 'external_id' for update_or_create to ensure uniqueness based on Excel's ID.
                ingredient, created = Ingredient.objects.update_or_create(
                    external_id=safe_ingredient_data["external_id"],
                    defaults={**safe_ingredient_data}
                )

            except Exception as e:
                self.stderr.write(f"Error: Failed to update_or_create Ingredient with external ID '{safe_ingredient_data.get('external_id')}' (index {index}): {e}")
                continue

            try:
                # Update or create the In100g instance linked to the ingredient
                In100g.objects.update_or_create(
                    ingredient=ingredient,
                    defaults=in100g_data
                )
            except Exception as e:
                self.stderr.write(f"Error: Failed to update_or_create In100g for Ingredient '{ingredient.name}' (ID: {ingredient.id_ingredient}) (index {index}): {e}")
                continue

            # --- Process Groups from columns: group_description, subgroup_1, subgroup_2 ---
            groups_to_assign = []
            group_fields = ['group_description', 'subgroup_1', 'subgroup_2']
            for field in group_fields:
                group_value = raw_data.get(field)
                if pd.notna(group_value) and str(group_value).strip():
                    group_name = str(group_value).strip()
                    try:
                        # Attempt to get an existing group by name
                        group_instance, grp_created = Group.objects.get_or_create(
                            name=group_name,
                            # Provide a unique id_group only if a new group is being created
                            # This prevents IntegrityError due to default=0 on a unique field
                            defaults={'id_group': current_max_group_id + 1}
                        )
                        if grp_created:
                            current_max_group_id += 1 # Increment only if a new group was created

                        groups_to_assign.append(group_instance)
                        self.stdout.write(f"Group {'created' if grp_created else 'found'}: {group_name} (ID: {group_instance.id_group})")

                    except Exception as e:
                        self.stderr.write(f"Warning (row {index}): Failed to get_or_create Group for value '{group_value}' in field '{field}': {e}")

            if groups_to_assign:
                ingredient.groups.set(groups_to_assign) # Assign all collected groups to the ingredient

            self.stdout.write(f"{'Created' if created else 'Updated'} ingredient: {ingredient.name} (External ID: {ingredient.external_id}, Internal ID: {ingredient.id_ingredient})")

        self.stdout.write("Ingredients import from Excel complete.")
