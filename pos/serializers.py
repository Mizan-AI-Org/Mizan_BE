from rest_framework import serializers
from .models import Table, Order, OrderItem
from accounts.serializers import CustomUserSerializer # Assuming CustomUserSerializer is in accounts app
from menu.serializers import MenuItemSerializer # Assuming MenuItemSerializer is in menu app

class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = '__all__'
        read_only_fields = ('id', 'created_at', 'updated_at', 'restaurant')

class OrderItemSerializer(serializers.ModelSerializer):
    menu_item_info = MenuItemSerializer(source='menu_item', read_only=True)

    class Meta:
        model = OrderItem
        fields = '__all__'
        read_only_fields = ('id', 'order', 'created_at', 'updated_at')

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    ordered_by_info = CustomUserSerializer(source='ordered_by', read_only=True)
    table_info = TableSerializer(source='table', read_only=True)

    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ('id', 'restaurant', 'order_time', 'total_amount', 'is_paid', 'created_at', 'updated_at') 