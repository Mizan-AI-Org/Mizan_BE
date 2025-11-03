from rest_framework import serializers
from .models import Table, Order, OrderLineItem, Payment, POSTransaction, ReceiptSetting, Discount, OrderModifier
from menu.models import MenuItem
from menu.serializers import MenuItemSerializer
import re


class TableSerializer(serializers.ModelSerializer):
    class Meta:
        model = Table
        fields = ['id', 'table_number', 'capacity', 'status', 'section', 'is_active', 'created_at', 'updated_at']
        read_only_fields = ['id', 'created_at', 'updated_at']
    
    def validate_table_number(self, value):
        """Validate table number is positive and reasonable"""
        if value < 1 or value > 500:
            raise serializers.ValidationError("Table number must be between 1 and 500.")
        return value
    
    def validate_capacity(self, value):
        """Validate table capacity is reasonable"""
        if value < 1 or value > 20:
            raise serializers.ValidationError("Table capacity must be between 1 and 20.")
        return value


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
    
    def validate_quantity(self, value):
        """Validate quantity is positive"""
        if value < 1 or value > 1000:
            raise serializers.ValidationError("Quantity must be between 1 and 1000.")
        return value
    
    def validate_unit_price(self, value):
        """Validate unit price is non-negative"""
        if value < 0:
            raise serializers.ValidationError("Unit price cannot be negative.")
        return value
    
    def validate_special_instructions(self, value):
        """Validate special instructions length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Special instructions cannot exceed 500 characters.")
        return value


class PaymentSerializer(serializers.ModelSerializer):
    class Meta:
        model = Payment
        fields = ['id', 'payment_method', 'amount', 'status', 'transaction_id', 
                  'amount_paid', 'change_given', 'tip_amount', 'refund_amount', 
                  'refund_reason', 'payment_time', 'created_at', 'updated_at']
        read_only_fields = ['id', 'payment_time', 'created_at', 'updated_at']
    
    def validate_amount(self, value):
        """Validate payment amount is positive"""
        if value <= 0:
            raise serializers.ValidationError("Payment amount must be greater than zero.")
        return value
    
    def validate_amount_paid(self, value):
        """Validate amount paid is non-negative"""
        if value is not None and value < 0:
            raise serializers.ValidationError("Amount paid cannot be negative.")
        return value
    
    def validate_tip_amount(self, value):
        """Validate tip amount is non-negative"""
        if value is not None and value < 0:
            raise serializers.ValidationError("Tip amount cannot be negative.")
        return value
    
    def validate_transaction_id(self, value):
        """Validate transaction ID format if provided"""
        if value and (not isinstance(value, str) or len(value) > 100):
            raise serializers.ValidationError("Transaction ID must be a string with max 100 characters.")
        return value


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
    
    def validate_customer_phone(self, value):
        """Validate phone number format"""
        if value:
            # Remove common formatting characters
            cleaned = re.sub(r'[\s\-\(\)\.]+', '', value)
            if not re.match(r'^\+?1?\d{10,15}$', cleaned):
                raise serializers.ValidationError("Invalid phone number format.")
        return value
    
    def validate_customer_email(self, value):
        """Validate email format"""
        if value:
            email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_regex, value):
                raise serializers.ValidationError("Invalid email format.")
        return value
    
    def validate_guest_count(self, value):
        """Validate guest count is positive"""
        if value and (value < 1 or value > 500):
            raise serializers.ValidationError("Guest count must be between 1 and 500.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 1000:
            raise serializers.ValidationError("Notes cannot exceed 1000 characters.")
        return value
    
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
    
    def validate_customer_phone(self, value):
        """Validate phone number format"""
        if value:
            cleaned = re.sub(r'[\s\-\(\)\.]+', '', value)
            if not re.match(r'^\+?1?\d{10,15}$', cleaned):
                raise serializers.ValidationError("Invalid phone number format.")
        return value
    
    def validate_customer_email(self, value):
        """Validate email format"""
        if value:
            email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_regex, value):
                raise serializers.ValidationError("Invalid email format.")
        return value
    
    def validate_guest_count(self, value):
        """Validate guest count is positive"""
        if value and (value < 1 or value > 500):
            raise serializers.ValidationError("Guest count must be between 1 and 500.")
        return value


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
    
    def validate_header_text(self, value):
        """Validate header text length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Header text cannot exceed 500 characters.")
        return value
    
    def validate_footer_text(self, value):
        """Validate footer text length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Footer text cannot exceed 500 characters.")
        return value
    
    def validate_paper_width(self, value):
        """Validate paper width is reasonable"""
        if value and (value < 1 or value > 100):
            raise serializers.ValidationError("Paper width must be between 1 and 100.")
        return value
    
    def validate_font_size_items(self, value):
        """Validate font size for items"""
        if value and (value < 6 or value > 24):
            raise serializers.ValidationError("Font size for items must be between 6 and 24.")
        return value
    
    def validate_font_size_total(self, value):
        """Validate font size for total"""
        if value and (value < 6 or value > 32):
            raise serializers.ValidationError("Font size for total must be between 6 and 32.")
        return value


class DiscountSerializer(serializers.ModelSerializer):
    """Serializer for discount codes and promotions"""
    is_valid_now = serializers.SerializerMethodField()
    
    class Meta:
        model = Discount
        fields = ['id', 'discount_code', 'description', 'discount_type', 'discount_value',
                  'is_active', 'min_order_amount', 'max_discount_amount', 'max_usage',
                  'usage_count', 'valid_from', 'valid_until', 'is_valid_now', 'created_at', 'updated_at']
        read_only_fields = ['id', 'usage_count', 'created_at', 'updated_at']
    
    def validate_discount_code(self, value):
        """Validate discount code format"""
        if not re.match(r'^[A-Z0-9\-]{3,20}$', value):
            raise serializers.ValidationError("Discount code must be 3-20 characters, uppercase letters, numbers, and hyphens only.")
        return value
    
    def validate_discount_value(self, value):
        """Validate discount value is positive"""
        if value <= 0:
            raise serializers.ValidationError("Discount value must be greater than zero.")
        return value
    
    def validate_min_order_amount(self, value):
        """Validate minimum order amount"""
        if value is not None and value < 0:
            raise serializers.ValidationError("Minimum order amount cannot be negative.")
        return value
    
    def validate_max_discount_amount(self, value):
        """Validate maximum discount amount"""
        if value is not None and value <= 0:
            raise serializers.ValidationError("Maximum discount amount must be greater than zero.")
        return value
    
    def validate_max_usage(self, value):
        """Validate maximum usage count"""
        if value is not None and value < 1:
            raise serializers.ValidationError("Maximum usage must be at least 1.")
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        if data.get('valid_from') and data.get('valid_until'):
            if data['valid_from'] >= data['valid_until']:
                raise serializers.ValidationError("valid_until must be after valid_from.")
        
        if data.get('max_discount_amount') and data.get('min_order_amount'):
            if data['max_discount_amount'] > data['min_order_amount'] * 0.5:
                raise serializers.ValidationError("max_discount_amount should not exceed 50% of min_order_amount.")
        
        return data
    
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