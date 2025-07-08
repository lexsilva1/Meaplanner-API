"""Serializers for recipe app"""
from rest_framework import serializers
from core.models import Recipe, Tag, Ingredient, Group, In100g, FattyAcids, Vitamins, Minerals

class GroupSerializer(serializers.ModelSerializer):
    idGroup = serializers.IntegerField(source="id_group")
    knownFoodGroupType = serializers.CharField(source="known_food_group_type")

    class Meta:
        model = Group
        fields = ['idGroup', 'name', 'knownFoodGroupType']
        read_only_fields = ['idGroup']

class NutritionalMixin:
    def to_representation(self, instance):
        data = super().to_representation(instance)
        # Round nutritional values to 2 decimals for display
        for field in ['energy', 'carbohydrate', 'fat', 'protein']:
            if field in data and data[field] is not None:
                data[field] = round(data[field], 2)
        return data

class In100gSerializer(NutritionalMixin, serializers.ModelSerializer):
    class Meta:
        model = In100g
        exclude = ['id']

class FattyAcidsSerializer(NutritionalMixin, serializers.ModelSerializer):
    class Meta:
        model = FattyAcids
        exclude = ['id']

class VitaminsSerializer(NutritionalMixin, serializers.ModelSerializer):
    class Meta:
        model = Vitamins
        exclude = ['id']

class MineralsSerializer(NutritionalMixin, serializers.ModelSerializer):
    class Meta:
        model = Minerals
        exclude = ['id']

class IngredientSerializer(serializers.ModelSerializer):
    # Expose camelCase keys for JSON.
    englishName = serializers.CharField(source="english_name")
    originalName = serializers.CharField(source="original_name")
    externalID = serializers.CharField(source="external_id")
    doseGr = serializers.FloatField(source="dose_gr")
    isLiquid = serializers.BooleanField(source="is_liquid")
    hideFromUser = serializers.BooleanField(source="hide_from_user")
    isRecipe = serializers.BooleanField(source="is_recipe")

    in100g = In100gSerializer()
    fattyAcids = FattyAcidsSerializer(source="fatty_acids")
    vitamins = VitaminsSerializer()
    minerals = MineralsSerializer()
    groups = GroupSerializer(many=True)
    image = serializers.ImageField(required=False)

    class Meta:
        model = Ingredient
        fields = [
            'id', 'name', 'englishName', 'originalName', 'externalID',
            'doseGr', 'isLiquid', 'image', 'in100g', 'fattyAcids',
            'vitamins', 'minerals', 'groups', 'hideFromUser', 'isRecipe'
        ]
        read_only_fields = ['id']

class TagSerializer(serializers.ModelSerializer):
    class Meta:
        model = Tag
        fields = ['id', 'name']
        read_only_fields = ['id']

class RecipeSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, required=False)
    ingredients = IngredientSerializer(many=True, required=False)
    image = serializers.ImageField(required=False)
    externalID = serializers.CharField(source="external_id")

    class Meta:
        model = Recipe
        fields = [
            'id', 'externalID', 'title', 'description', 'link',
            'tags', 'ingredients', 'image', 'is_orderable', 'is_hidden',
            'creation_time', 'modification_time'
        ]
        read_only_fields = ['id', 'creation_time', 'modification_time']

    def _handle_nested_creation(self, model_class, data):
        return model_class.objects.create(**data)

    def _process_ingredient(self, auth_user, ingredient_data):
        groups_data = ingredient_data.pop('groups', [])
        nested_models = {
            'in100g': (In100g, ingredient_data.pop('in100g', {})),
            'fatty_acids': (FattyAcids, ingredient_data.pop('fattyAcids', {})),
            'vitamins': (Vitamins, ingredient_data.pop('vitamins', {})),
            'minerals': (Minerals, ingredient_data.pop('minerals', {}))
        }
        nested_objs = {
            key: self._handle_nested_creation(model, data)
            for key, (model, data) in nested_models.items()
        }
        ingredient = Ingredient.objects.create(
            user=auth_user,
            **nested_objs,
            **ingredient_data
        )
        for group_data in groups_data:
            group, _ = Group.objects.get_or_create(
                id_group=group_data.get('idGroup'),
                defaults={
                    'name': group_data.get('name'),
                    'known_food_group_type': group_data.get('knownFoodGroupType')
                }
            )
            ingredient.groups.add(group)
        return ingredient

    def _handle_ingredients(self, ingredients, recipe):
        auth_user = self.context['request'].user
        for ingredient_data in ingredients:
            ingredient = self._process_ingredient(auth_user, ingredient_data)
            recipe.ingredients.add(ingredient)

    def _update_or_create_tags(self, tags, recipe):
        auth_user = self.context['request'].user
        tag_objs = []
        for tag in tags:
            if tag.get('id'):
                tag_obj = Tag.objects.filter(id=tag['id'], user=auth_user).first()
                if tag_obj:
                    tag_obj.name = tag.get('name', tag_obj.name)
                    tag_obj.save()
                else:
                    tag_obj = Tag.objects.create(user=auth_user, **tag)
            else:
                tag_obj, created = Tag.objects.get_or_create(user=auth_user, name=tag['name'])
            tag_objs.append(tag_obj)
        recipe.tags.set(tag_objs)

    def create(self, validated_data):
        tags = validated_data.pop('tags', [])
        ingredients = validated_data.pop('ingredients', [])
        recipe = Recipe.objects.create(**validated_data)
        if tags:
            self._update_or_create_tags(tags, recipe)
        if ingredients:
            self._handle_ingredients(ingredients, recipe)
        return recipe

    def update(self, instance, validated_data):
        tags = validated_data.pop('tags', None)
        ingredients = validated_data.pop('ingredients', None)
        if tags is not None:
            self._update_or_create_tags(tags, instance)
        if ingredients is not None:
            instance.ingredients.clear()
            self._handle_ingredients(ingredients, instance)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        instance.save()
        return instance

class RecipeImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Recipe
        fields = ['id', 'image']
        read_only_fields = ['id']
        extra_kwargs = {'image': {'required': True}}

class RecipeDetailSerializer(RecipeSerializer):
    """Detailed view of a recipe (includes all fields)"""
    class Meta(RecipeSerializer.Meta):
        fields = RecipeSerializer.Meta.fields

class IngredientImageSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ingredient
        fields = ['id', 'image']
        read_only_fields = ['id']
        extra_kwargs = {'image': {'required': True}}