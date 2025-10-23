from django.urls import path, include
from .views import (
    MenuCategoryListCreateAPIView,
    MenuCategoryRetrieveUpdateDestroyAPIView,
    MenuItemListCreateAPIView,
    MenuItemRetrieveUpdateDestroyAPIView,
    IngredientListCreateAPIView,
    IngredientRetrieveUpdateDestroyAPIView,
    RecipeListCreateAPIView,
    RecipeRetrieveUpdateDestroyAPIView,
    RecipeIngredientListCreateAPIView,
    RecipeIngredientRetrieveUpdateDestroyAPIView,
)

urlpatterns = [
    # Menu Categories
    path('categories/', MenuCategoryListCreateAPIView.as_view(), name='menu-category-list-create'),
    path('categories/<uuid:pk>/', MenuCategoryRetrieveUpdateDestroyAPIView.as_view(), name='menu-category-detail'),

    # Menu Items (can be filtered by category)
    path('items/', MenuItemListCreateAPIView.as_view(), name='menu-item-list-create'),
    path('items/<uuid:pk>/', MenuItemRetrieveUpdateDestroyAPIView.as_view(), name='menu-item-detail'),

    # Ingredients
    path('ingredients/', IngredientListCreateAPIView.as_view(), name='ingredient-list-create'),
    path('ingredients/<uuid:pk>/', IngredientRetrieveUpdateDestroyAPIView.as_view(), name='ingredient-detail'),

    # Recipes
    path('recipes/', RecipeListCreateAPIView.as_view(), name='recipe-list-create'),
    path('recipes/<uuid:pk>/', RecipeRetrieveUpdateDestroyAPIView.as_view(), name='recipe-detail'),

    # Recipe Ingredients (nested under recipes)
    path('recipes/<uuid:recipe_pk>/ingredients/', RecipeIngredientListCreateAPIView.as_view(), name='recipe-ingredient-list-create'),
    path('recipes/<uuid:recipe_pk>/ingredients/<uuid:pk>/', RecipeIngredientRetrieveUpdateDestroyAPIView.as_view(), name='recipe-ingredient-detail'),
]
