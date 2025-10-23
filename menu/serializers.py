from rest_framework import serializers
from .models import MenuCategory, MenuItem, Ingredient, Recipe, RecipeIngredient

class MenuCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = MenuCategory
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class MenuItemSerializer(serializers.ModelSerializer):
    category_info = MenuCategorySerializer(source='category', read_only=True)

    class Meta:
        model = MenuItem
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class IngredientSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ingredient
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class RecipeIngredientSerializer(serializers.ModelSerializer):
    ingredient_info = IngredientSerializer(source='ingredient', read_only=True)

    class Meta:
        model = RecipeIngredient
        fields = '__all__'
        read_only_fields = ('recipe',)

class RecipeSerializer(serializers.ModelSerializer):
    ingredients = RecipeIngredientSerializer(many=True, read_only=True)

    class Meta:
        model = Recipe
        fields = '__all__'
        read_only_fields = ('menu_item', 'created_at', 'updated_at')
