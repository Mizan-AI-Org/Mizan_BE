from rest_framework import serializers
from .models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem, StockAdjustment
from accounts.serializers import CustomUserSerializer

class InventoryItemSerializer(serializers.ModelSerializer):
    class Meta:
        model = InventoryItem
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class SupplierSerializer(serializers.ModelSerializer):
    class Meta:
        model = Supplier
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class PurchaseOrderItemSerializer(serializers.ModelSerializer):
    inventory_item_info = InventoryItemSerializer(source='inventory_item', read_only=True)

    class Meta:
        model = PurchaseOrderItem
        fields = '__all__'
        read_only_fields = ('purchase_order', 'total_price')

class PurchaseOrderSerializer(serializers.ModelSerializer):
    items = PurchaseOrderItemSerializer(many=True, read_only=True)
    supplier_info = SupplierSerializer(source='supplier', read_only=True)
    created_by_info = CustomUserSerializer(source='created_by', read_only=True)

    class Meta:
        model = PurchaseOrder
        fields = '__all__'
        read_only_fields = ('restaurant', 'order_date', 'total_amount', 'status', 'created_by', 'created_at', 'updated_at')

class StockAdjustmentSerializer(serializers.ModelSerializer):
    inventory_item_info = InventoryItemSerializer(source='inventory_item', read_only=True)
    adjusted_by_info = CustomUserSerializer(source='adjusted_by', read_only=True)

    class Meta:
        model = StockAdjustment
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at')
