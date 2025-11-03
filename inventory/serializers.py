from rest_framework import serializers
from .models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem, StockAdjustment
from accounts.serializers import CustomUserSerializer
import re


class InventoryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryItem
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')
    
    def validate_sku(self, value):
        """Validate SKU format"""
        if value and not re.match(r'^[A-Z0-9\-]{3,50}$', value):
            raise serializers.ValidationError("SKU must be 3-50 characters, uppercase letters, numbers, and hyphens only.")
        return value
    
    def validate_item_name(self, value):
        """Validate item name length"""
        if not value or len(value) > 200:
            raise serializers.ValidationError("Item name must be between 1 and 200 characters.")
        return value
    
    def validate_unit_cost(self, value):
        """Validate unit cost is non-negative"""
        if value < 0:
            raise serializers.ValidationError("Unit cost cannot be negative.")
        return value
    
    def validate_current_quantity(self, value):
        """Validate current quantity is non-negative"""
        if value < 0:
            raise serializers.ValidationError("Current quantity cannot be negative.")
        return value
    
    def validate_reorder_level(self, value):
        """Validate reorder level is positive"""
        if value <= 0:
            raise serializers.ValidationError("Reorder level must be greater than zero.")
        return value
    
    def validate_reorder_quantity(self, value):
        """Validate reorder quantity is positive"""
        if value and value <= 0:
            raise serializers.ValidationError("Reorder quantity must be greater than zero.")
        return value


class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')
    
    def validate_supplier_name(self, value):
        """Validate supplier name length"""
        if not value or len(value) > 200:
            raise serializers.ValidationError("Supplier name must be between 1 and 200 characters.")
        return value
    
    def validate_contact_person(self, value):
        """Validate contact person name length"""
        if value and len(value) > 100:
            raise serializers.ValidationError("Contact person name cannot exceed 100 characters.")
        return value
    
    def validate_phone(self, value):
        """Validate phone number format"""
        if value:
            cleaned = re.sub(r'[\s\-\(\)\.]+', '', value)
            if not re.match(r'^\+?1?\d{10,15}$', cleaned):
                raise serializers.ValidationError("Invalid phone number format.")
        return value
    
    def validate_email(self, value):
        """Validate email format"""
        if value:
            email_regex = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
            if not re.match(email_regex, value):
                raise serializers.ValidationError("Invalid email format.")
        return value
    
    def validate_address(self, value):
        """Validate address length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Address cannot exceed 500 characters.")
        return value
    
    def validate_payment_terms(self, value):
        """Validate payment terms"""
        if value and value not in ['NET30', 'NET60', 'COD', 'PREPAID']:
            raise serializers.ValidationError("Invalid payment terms. Must be NET30, NET60, COD, or PREPAID.")
        return value


class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    inventory_item_info = InventoryItemSerializer(source='inventory_item', read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = '__all__'
        read_only_fields = ('purchase_order', 'total_price')
    
    def validate_quantity(self, value):
        """Validate quantity is positive"""
        if value <= 0:
            raise serializers.ValidationError("Quantity must be greater than zero.")
        return value
    
    def validate_unit_price(self, value):
        """Validate unit price is non-negative"""
        if value < 0:
            raise serializers.ValidationError("Unit price cannot be negative.")
        return value


class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    supplier_info = SupplierSerializer(source='supplier', read_only=True)
    created_by_info = CustomUserSerializer(source='created_by', read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = '__all__'
        read_only_fields = ('restaurant', 'order_date', 'total_amount', 'status', 'created_by', 'created_at', 'updated_at')
    
    def validate_expected_delivery_date(self, value):
        """Validate expected delivery date is in the future"""
        from datetime import datetime, timezone
        if value and value <= datetime.now(timezone.utc):
            raise serializers.ValidationError("Expected delivery date must be in the future.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 1000:
            raise serializers.ValidationError("Notes cannot exceed 1000 characters.")
        return value


class StockAdjustmentSerializer(serializers.ModelSerializer):
    inventory_item_info = InventoryItemSerializer(source='inventory_item', read_only=True)
    adjusted_by_info = CustomUserSerializer(source='adjusted_by', read_only=True)

    class Meta:
        model = StockAdjustment
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at')
    
    def validate_quantity_change(self, value):
        """Validate quantity change is not zero"""
        if value == 0:
            raise serializers.ValidationError("Quantity change cannot be zero.")
        return value
    
    def validate_reason(self, value):
        """Validate reason length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Reason cannot exceed 500 characters.")
        return value
