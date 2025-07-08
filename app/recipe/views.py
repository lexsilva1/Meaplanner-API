"""
Views for the recipe app
"""
from django.db.models import Q
from drf_spectacular.utils import (
    extend_schema_view,
    extend_schema,
    OpenApiParameter,
    OpenApiTypes
)
from rest_framework import viewsets, mixins, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication
from rest_framework.permissions import IsAuthenticated

from core.models import Recipe, Tag, Ingredient
from recipe import serializers

@extend_schema_view(
    list=extend_schema(
        parameters=[
            OpenApiParameter(
                name='tags',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Filter recipes by comma separated tag IDs'
            ),
            OpenApiParameter(
                name='ingredients',
                type=OpenApiTypes.STR,
                location=OpenApiParameter.QUERY,
                description='Filter recipes by comma separated ingredient IDs'
            )
        ]
    )
)
class RecipeViewSet(viewsets.ModelViewSet):
    """Manage recipes in the database"""
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]
    queryset = Recipe.objects.all()
    serializer_class = serializers.RecipeDetailSerializer

    def _params_to_ints(self, qs):
        """Convert a list of string IDs to a list of integers"""
        return [int(str_id) for str_id in qs.split(',')]

    def get_queryset(self):
        """Return recipes for the current authenticated user only"""
        tags = self.request.query_params.get('tags')
        ingredients = self.request.query_params.get('ingredients')
        queryset = self.queryset
        if tags:
            tag_ids = self._params_to_ints(tags)
            queryset = queryset.filter(tags__id__in=tag_ids)
        if ingredients:
            ingredient_ids = self._params_to_ints(ingredients)
            queryset = queryset.filter(ingredients__id__in=ingredient_ids)
        return queryset.filter(user=self.request.user).order_by('-id').distinct()

    def perform_create(self, serializer):
        """Create a new recipe"""
        serializer.save(user=self.request.user)

    def get_serializer_class(self):
        """Return appropriate serializer class"""
        if self.action == 'list':
            return serializers.RecipeSerializer
        elif self.action == 'upload_image':
            return serializers.RecipeImageSerializer
        return self.serializer_class

    @action(methods=['POST'], detail=True, url_path='upload-image')
    def upload_image(self, request, pk=None):
        """Upload an image to a recipe"""
        recipe = self.get_object()
        serializer = self.get_serializer(recipe, data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    # New custom action to return recipes filtered by one or more tags.
    @action(detail=False, methods=['GET'], url_path='by-tag')
    def by_tag(self, request):
        """
        Retrieve recipes filtered by one or more tags.
        For example: /api/recipes/by-tag/?tag=breakfast,lunch
        returns recipes tagged as "breakfast" OR "lunch".
        """
        tags_param = request.query_params.get('tag')
        if not tags_param:
            return Response({"error": "Tag query parameter is required."},
                            status=status.HTTP_400_BAD_REQUEST)
        tag_list = [tag.strip() for tag in tags_param.split(',') if tag.strip()]
        if not tag_list:
            return Response({"error": "No valid tag provided."},
                            status=status.HTTP_400_BAD_REQUEST)
        conditions = Q()
        for tag in tag_list:
            conditions |= Q(tags__name__iexact=tag)
        filtered_queryset = self.queryset.filter(conditions).filter(user=self.request.user).distinct()
        serializer = self.get_serializer(filtered_queryset, many=True)
        return Response(serializer.data)

# Base viewset for user-owned recipe attributes.
class BaseRecipeAttrViewSet(mixins.UpdateModelMixin,
                            mixins.DestroyModelMixin,
                            mixins.ListModelMixin,
                            mixins.CreateModelMixin,
                            viewsets.GenericViewSet):
    """Base viewset for user owned recipe attributes"""
    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticated]
    queryset = None  # Set this in child classes.
    serializer_class = None  # Set this in child classes.

    def get_queryset(self):
        """Return objects for the current authenticated user only"""
        assigned_only = bool(int(self.request.query_params.get('assigned_only', 0)))
        queryset = self.queryset
        if assigned_only:
            queryset = queryset.filter(recipe__isnull=False)
        return queryset.filter(user=self.request.user).order_by('-name').distinct()

    def perform_create(self, serializer):
        """Create a new object"""
        serializer.save(user=self.request.user)

class TagViewSet(BaseRecipeAttrViewSet):
    """Manage tags in the database"""
    queryset = Tag.objects.all()
    serializer_class = serializers.TagSerializer

class IngredientViewSet(BaseRecipeAttrViewSet):
    """Manage ingredients in the database"""
    queryset = Ingredient.objects.all()
    serializer_class = serializers.IngredientSerializer

    @action(detail=True, methods=['GET'], url_path='groups')
    def get_groups(self, request, pk=None):
        """Return groups for the selected ingredient."""
        ingredient = self.get_object()
        groups = ingredient.groups.all()
        # Ensure GroupSerializer is available in your serializers module.
        serializer = serializers.GroupSerializer(groups, many=True)
        return Response(serializer.data)