from rest_framework import generics, permissions
from .models import MenuCategory, MenuItem, Ingredient, Recipe, RecipeIngredient
from .serializers import (
    MenuCategorySerializer,
    MenuItemSerializer,
    IngredientSerializer,
    RecipeSerializer,
    RecipeIngredientSerializer,
)
from accounts.views import IsAdmin, IsManagerOrAdmin

class MenuCategoryListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = MenuCategorySerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return MenuCategory.objects.filter(restaurant=self.request.user.restaurant).order_by('display_order')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class MenuCategoryRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = MenuCategorySerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return MenuCategory.objects.filter(restaurant=self.request.user.restaurant)

class MenuItemListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = MenuItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        queryset = MenuItem.objects.filter(restaurant=self.request.user.restaurant)
        category_id = self.request.query_params.get('category_id')
        if category_id:
            queryset = queryset.filter(category__id=category_id)
        return queryset.order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class MenuItemRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = MenuItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return MenuItem.objects.filter(restaurant=self.request.user.restaurant)

class IngredientListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = IngredientSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return Ingredient.objects.filter(restaurant=self.request.user.restaurant).order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class IngredientRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = IngredientSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Ingredient.objects.filter(restaurant=self.request.user.restaurant)

class RecipeListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = RecipeSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return Recipe.objects.filter(menu_item__restaurant=self.request.user.restaurant).order_by('menu_item__name')

    def perform_create(self, serializer):
        menu_item_id = self.request.data.get('menu_item')
        menu_item = MenuItem.objects.get(id=menu_item_id, restaurant=self.request.user.restaurant)
        serializer.save(menu_item=menu_item)

class RecipeRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = RecipeSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Recipe.objects.filter(menu_item__restaurant=self.request.user.restaurant)

class RecipeIngredientListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = RecipeIngredientSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        recipe_id = self.kwargs.get('recipe_pk')
        return RecipeIngredient.objects.filter(recipe__id=recipe_id, recipe__menu_item__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        recipe_id = self.kwargs.get('recipe_pk')
        recipe = Recipe.objects.get(id=recipe_id, menu_item__restaurant=self.request.user.restaurant)
        serializer.save(recipe=recipe)

class RecipeIngredientRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = RecipeIngredientSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        recipe_id = self.kwargs.get('recipe_pk')
        return RecipeIngredient.objects.filter(recipe__id=recipe_id, recipe__menu_item__restaurant=self.request.user.restaurant)
