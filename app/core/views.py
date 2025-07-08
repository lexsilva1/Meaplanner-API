"""
Views for the core API (meal plans, etc.)
"""

import logging
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import authentication_classes
from django.core.management import call_command
from django.contrib.auth import get_user_model
from core.models import MealPlan
from core.serializers import MealPlanSerializer, CreateMealPlanSerializer

logger = logging.getLogger(__name__)
User = get_user_model()


@api_view(['POST'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def create_personalized_meal_plan(request):
    """
    Create a personalized meal plan for the authenticated user.

    Expected payload:
    {
        "calories": 2000,
        "goal": "maintenance",  # options: weight_loss, muscle_gain, maintenance
        "model": "llama3:8b",   # optional, default model
        "force_deterministic": false  # optional, skip AI generation
    }
    """
    serializer = CreateMealPlanSerializer(data=request.data)

    if not serializer.is_valid():
        return Response(
            {"error": "Invalid input", "details": serializer.errors},
            status=status.HTTP_400_BAD_REQUEST
        )

    user = request.user
    calories = serializer.validated_data['calories']
    goal = serializer.validated_data.get('goal', 'maintenance')
    model = serializer.validated_data.get('model', 'llama3:8b')
    force_deterministic = serializer.validated_data.get('force_deterministic', False)

    try:
        # Call the management command programmatically
        logger.info(f"Creating meal plan for user {user.email} with {calories} calories")

        # Prepare command arguments
        command_args = [
            f'--user_email={user.email}',
            f'--calories={calories}',
            f'--goal={goal}',
            f'--model={model}',
        ]

        if force_deterministic:
            command_args.append('--force_deterministic')

        # Execute the command
        call_command('create_personalized_mealplan_2', *command_args)

        # Get the most recent meal plan for this user
        latest_meal_plan = MealPlan.objects.filter(user=user).order_by('-creation_time').first()

        if latest_meal_plan:
            meal_plan_data = MealPlanSerializer(latest_meal_plan).data

            return Response({
                "success": True,
                "message": "Meal plan created successfully",
                "meal_plan": meal_plan_data
            }, status=status.HTTP_201_CREATED)
        else:
            return Response({
                "error": "Meal plan creation failed",
                "message": "No meal plan was created"
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    except Exception as e:
        logger.error(f"Error creating meal plan for user {user.email}: {str(e)}")
        return Response({
            "error": "Internal server error",
            "message": str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def list_user_meal_plans(request):
    """
    List all meal plans for the authenticated user.
    """
    user = request.user
    meal_plans = MealPlan.objects.filter(user=user).order_by('-creation_time')

    serializer = MealPlanSerializer(meal_plans, many=True)

    return Response({
        "meal_plans": serializer.data
    }, status=status.HTTP_200_OK)


@api_view(['GET'])
@authentication_classes([TokenAuthentication])
@permission_classes([IsAuthenticated])
def get_meal_plan_detail(request, meal_plan_id):
    """
    Get detailed information about a specific meal plan.
    """
    user = request.user

    try:
        meal_plan = MealPlan.objects.get(id=meal_plan_id, user=user)
        serializer = MealPlanSerializer(meal_plan)

        return Response({
            "meal_plan": serializer.data
        }, status=status.HTTP_200_OK)

    except MealPlan.DoesNotExist:
        return Response({
            "error": "Meal plan not found"
        }, status=status.HTTP_404_NOT_FOUND)
