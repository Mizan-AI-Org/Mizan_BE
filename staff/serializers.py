from rest_framework import serializers
from .models import Category, Product, Order, OrderItem, Table
from accounts.serializers import CustomUserSerializer, RestaurantSerializer
import decimal

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class ProductSerializer(serializers.ModelSerializer):
    category_info = CategorySerializer(source='category', read_only=True)

    class Meta:
        model = Product
        fields = '__all__'

class OrderItemSerializer(serializers.ModelSerializer):
    product_info = ProductSerializer(source='product', read_only=True)

    class Meta:
        model = OrderItem
        fields = '__all__'
        read_only_fields = ('total_price',)

class OrderSerializer(serializers.ModelSerializer):
    items = OrderItemSerializer(many=True, read_only=True)
    staff_info = CustomUserSerializer(source='staff', read_only=True)

    class Meta:
        model = Order
        fields = '__all__'
        read_only_fields = ('subtotal', 'tax_amount', 'total_amount', 'created_at', 'updated_at')

class TableSerializer(serializers.ModelSerializer):
    current_order_info = OrderSerializer(source='current_order', read_only=True)

    class Meta:
        model = Table
        fields = '__all__'
        read_only_fields = ('current_order_info', 'created_at', 'updated_at')

class OrderCreateSerializer(serializers.ModelSerializer):
    items = serializers.ListField(
        child=serializers.DictField(
            child=serializers.CharField() # Or more specific validation if needed
        ),
        write_only=True
    )

    class Meta:
        model = Order
        fields = ('order_type', 'table_number', 'customer_name', 'customer_phone', 'items')

    def create(self, validated_data):
        items_data = validated_data.pop('items')
        restaurant = self.context['request'].user.restaurant
        staff = self.context['request'].user

        # Calculate subtotal, tax, and total based on items
        subtotal = 0
        order_items_for_creation = []

        for item_data in items_data:
            product = Product.objects.get(id=item_data['product_id'], restaurant=restaurant)
            quantity = int(item_data['quantity'])
            unit_price = product.base_price
            total_price = unit_price * quantity
            subtotal += total_price
            order_items_for_creation.append({
                'product': product,
                'quantity': quantity,
                'unit_price': unit_price,
                'total_price': total_price,
            })

        tax_rate = 0.1 # Example tax rate
        tax_amount = subtotal * decimal.Decimal(str(tax_rate))
        total_amount = subtotal + tax_amount

        order = Order.objects.create(
            restaurant=restaurant,
            staff=staff,
            subtotal=subtotal,
            tax_amount=tax_amount,
            total_amount=total_amount,
            **validated_data
        )

        for item_data in order_items_for_creation:
            OrderItem.objects.create(order=order, **item_data)

        return order
