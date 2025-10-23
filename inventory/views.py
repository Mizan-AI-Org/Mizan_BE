from rest_framework import generics, permissions, status
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db import transaction
from django.db.models import F
from django.utils import timezone
from rest_framework import serializers

from .models import InventoryItem, Supplier, PurchaseOrder, PurchaseOrderItem, StockAdjustment
from .serializers import (
    InventoryItemSerializer,
    SupplierSerializer,
    PurchaseOrderSerializer,
    PurchaseOrderItemSerializer,
    StockAdjustmentSerializer,
)
from accounts.views import IsAdmin, IsManagerOrAdmin


class InventoryItemListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = InventoryItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return InventoryItem.objects.filter(restaurant=self.request.user.restaurant, is_active=True).order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class InventoryItemRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = InventoryItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return InventoryItem.objects.filter(restaurant=self.request.user.restaurant)

class SupplierListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return Supplier.objects.filter(restaurant=self.request.user.restaurant).order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class SupplierRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = SupplierSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Supplier.objects.filter(restaurant=self.request.user.restaurant)

class PurchaseOrderListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return PurchaseOrder.objects.filter(restaurant=self.request.user.restaurant).order_by('-order_date')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant, created_by=self.request.user)

class PurchaseOrderRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PurchaseOrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return PurchaseOrder.objects.filter(restaurant=self.request.user.restaurant)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        status_change = request.data.get('status')

        if status_change == 'RECEIVED' and instance.status != 'RECEIVED':
            with transaction.atomic():
                instance.status = 'RECEIVED'
                instance.delivery_date = timezone.now().date()
                instance.save()

                # Update inventory for each item in the purchase order
                for order_item in instance.items.all():
                    item = order_item.inventory_item
                    item.current_stock = F('current_stock') + order_item.quantity
                    item.last_restock_date = timezone.now().date()
                    item.save()
                    # Reload the item to get the updated stock value
                    item.refresh_from_db()

                serializer = self.get_serializer(instance)
                return Response(serializer.data)
        
        return super().update(request, *args, **kwargs)

class PurchaseOrderItemListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = PurchaseOrderItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        po_id = self.kwargs.get('purchase_order_pk')
        return PurchaseOrderItem.objects.filter(purchase_order__id=po_id, purchase_order__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        po_id = self.kwargs.get('purchase_order_pk')
        purchase_order = PurchaseOrder.objects.get(id=po_id, restaurant=self.request.user.restaurant)
        item = serializer.validated_data['inventory_item']
        quantity = serializer.validated_data['quantity']

        total_price = item.cost_per_unit * quantity
        serializer.save(purchase_order=purchase_order, unit_price=item.cost_per_unit, total_price=total_price)
        
        # Update total amount of the purchase order
        purchase_order.total_amount = F('total_amount') + total_price
        purchase_order.save()
        purchase_order.refresh_from_db() # Refresh to get latest total_amount

class PurchaseOrderItemRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = PurchaseOrderItemSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        po_id = self.kwargs.get('purchase_order_pk')
        return PurchaseOrderItem.objects.filter(purchase_order__id=po_id, purchase_order__restaurant=self.request.user.restaurant)

    def perform_destroy(self, instance):
        # Subtract item's total price from purchase order's total amount before deleting
        instance.purchase_order.total_amount = F('purchase_order__total_amount') - instance.total_price
        instance.purchase_order.save()
        instance.purchase_order.refresh_from_db()
        super().perform_destroy(instance)

class StockAdjustmentListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = StockAdjustmentSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return StockAdjustment.objects.filter(restaurant=self.request.user.restaurant).order_by('-created_at')

    def perform_create(self, serializer):
        adjustment_type = serializer.validated_data['adjustment_type']
        inventory_item = serializer.validated_data['inventory_item']
        quantity_changed = serializer.validated_data['quantity_changed']

        with transaction.atomic():
            if adjustment_type == 'ADD':
                inventory_item.current_stock = F('current_stock') + quantity_changed
            elif adjustment_type == 'REMOVE' or adjustment_type == 'WASTE':
                if inventory_item.current_stock < quantity_changed:
                    raise serializers.ValidationError("Not enough stock to remove.")
                inventory_item.current_stock = F('current_stock') - quantity_changed
            # For 'TRANSFER', more complex logic would be needed for multi-location
            inventory_item.save()
            inventory_item.refresh_from_db() # Reload to get updated stock
            serializer.save(restaurant=self.request.user.restaurant, adjusted_by=self.request.user)

class StockAdjustmentRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = StockAdjustmentSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return StockAdjustment.objects.filter(restaurant=self.request.user.restaurant)
