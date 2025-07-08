"""
URL mappings for the core API (meal plans).
"""

from django.urls import path
from core import views

app_name = 'core'

urlpatterns = [
    path('meal-plans/create/', views.create_personalized_meal_plan, name='create-meal-plan'),
    path('meal-plans/', views.list_user_meal_plans, name='list-meal-plans'),
    path('meal-plans/<int:meal_plan_id>/', views.get_meal_plan_detail, name='meal-plan-detail'),
]
