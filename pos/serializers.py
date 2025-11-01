from rest_framework import serializers
from .models import Table, Order, OrderLineItem, Payment, POSTransaction, ReceiptSetting, Discount, OrderModifier
from menu.models import MenuItem
from menu.serializers import MenuItemSerializer


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = ['id', 'table_number', 'capacity', 'status', 'section', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class OrderLineItemSerializer(serializers.ModelSerializer):
    menu_item = MenuItemSerializer(read_only=True)
    menu_item_id = serializers.PrimaryKeyRelatedField(
        queryset=MenuItem.objects.all(),
        write_only=True,
        source='menu_item'
    )
    
    class Meta:
        model = OrderLineItem
        fields = ['id', 'menu_item', 'menu_item_id', 'quantity', 'unit_price', 'total_price', 
                  'special_instructions', 'status', 'created_at', 'updated_at']
        read_only_fields = ['id', 'total_price', 'created_at', 'updated_at']


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id', 'payment_method', 'amount', 'status', 'transaction_id', 
                  'amount_paid', 'change_given', 'tip_amount', 'refund_amount', 
                  'refund_reason', 'payment_time', 'created_at', 'updated_at']
        read_only_fields = ['id', 'payment_time', 'created_at', 'updated_at']


class OrderSerializer(serializers.ModelSerializer):
    line_items = OrderLineItemSerializer(many=True, read_only=True)
    payment = PaymentSerializer(read_only=True)
    table_number = serializers.SerializerMethodField()
    server_name = serializers.SerializerMethodField()
    
    class Meta:
        model = Order
        fields = ['id', 'order_number', 'order_type', 'status', 'table', 'table_number',
                  'server', 'server_name', 'subtotal', 'tax_amount', 'discount_amount',
                  'discount_reason', 'total_amount', 'customer_name', 'customer_phone',
                  'customer_email', 'delivery_address', 'delivery_instructions',
                  'guest_count', 'notes', 'is_priority', 'order_time', 'ready_time',
                  'completion_time', 'line_items', 'payment', 'created_at', 'updated_at']
        read_only_fields = ['id', 'order_number', 'order_time', 'ready_time',
                           'completion_time', 'created_at', 'updated_at']
    
    def get_table_number(self, obj):
        return obj.table.table_number if obj.table else None
    
    def get_server_name(self, obj):
        return obj.server.get_full_name() if obj.server else None


class OrderCreateSerializer(serializers.ModelSerializer):
    """Simplified serializer for order creation"""
    line_items = OrderLineItemSerializer(many=True, required=False)
    
    class Meta:
        model = Order
        fields = ['order_type', 'table', 'customer_name', 'customer_phone', 
                  'customer_email', 'delivery_address', 'delivery_instructions',
                  'guest_count', 'notes', 'is_priority', 'line_items']


class POSTransactionSerializer(serializers.ModelSerializer):
    user_name = serializers.SerializerMethodField()
    order_number = serializers.SerializerMethodField()
    
    class Meta:
        model = POSTransaction
        fields = ['id', 'transaction_type', 'user', 'user_name', 'order', 'order_number',
                  'description', 'previous_value', 'new_value', 'amount_involved',
                  'created_at']
        read_only_fields = ['id', 'created_at']
    
    def get_user_name(self, obj):
        return obj.user.get_full_name() if obj.user else None
    
    def get_order_number(self, obj):
        return obj.order.order_number if obj.order else None


class ReceiptSettingSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReceiptSetting
        fields = ['id', 'header_text', 'footer_text', 'show_item_codes',
                  'show_item_descriptions', 'show_unit_prices', 'show_discount_details',
                  'show_tax_breakdown', 'paper_width', 'font_size_items',
                  'font_size_total', 'logo', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']


class DiscountSerializer(serializers.ModelSerializer):
    """Serializer for discount codes and promotions"""
    is_valid_now = serializers.SerializerMethodField()
    
    class Meta:
        model = Discount
        fields = ['id', 'discount_code', 'description', 'discount_type', 'discount_value',
                  'is_active', 'min_order_amount', 'max_discount_amount', 'max_usage',
                  'usage_count', 'valid_from', 'valid_until', 'is_valid_now', 'created_at', 'updated_at']
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']
    
    def get_is_valid_now(self, obj):
        return obj.is_valid()


class OrderModifierSerializer(serializers.ModelSerializer):
    """Serializer for order item modifiers"""
    class Meta:
        model = OrderModifier
        fields = ['id', 'line_item', 'modifier_name', 'modifier_price', 'created_at']
        read_only_fields = ['id', 'created_at']


class OrderLineItemDetailedSerializer(OrderLineItemSerializer):
    """Extended line item serializer with modifiers"""
    modifiers = OrderModifierSerializer(many=True, read_only=True)
    
    class Meta(OrderLineItemSerializer.Meta):
        fields = OrderLineItemSerializer.Meta.fields + ['modifiers']