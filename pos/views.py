from rest_framework import generics, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from rest_framework.views import APIView
from .models import Table, Order, OrderItem
from .serializers import TableSerializer, OrderSerializer, OrderItemSerializer
from accounts.permissions import IsAdminOrSuperAdmin, IsAdminOrManager
from menu.models import MenuItem
from django.shortcuts import get_object_or_404

class TableListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TableSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return Table.objects.filter(restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class TableRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TableSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Table.objects.filter(restaurant=self.request.user.restaurant)

class OrderListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return Order.objects.filter(restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        # Calculate total_amount based on order items if provided
        items_data = self.request.data.pop('items', [])
        order = serializer.save(restaurant=self.request.user.restaurant, ordered_by=self.request.user)

        total_amount = 0
        for item_data in items_data:
            menu_item = get_object_or_404(MenuItem, id=item_data['menu_item'], restaurant=self.request.user.restaurant)
            quantity = item_data['quantity']
            unit_price = menu_item.price
            total_price = quantity * unit_price
            OrderItem.objects.create(
                order=order,
                menu_item=menu_item,
                quantity=quantity,
                unit_price=unit_price,
                total_price=total_price,
                notes=item_data.get('notes')
            )
            total_amount += total_price
        order.total_amount = total_amount
        order.save()

class OrderRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Order.objects.filter(restaurant=self.request.user.restaurant)

    def perform_update(self, serializer):
        # Handle updating order items separately if needed
        order = serializer.save()
        # You might want to implement more complex logic here for updating order items
        # For simplicity, this example just updates the main order fields.
        # If items are sent, you'd delete existing and re-create, or intelligently diff.

class OrderStatusUpdateAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def put(self, request, pk, format=None):
        order = get_object_or_404(Order, pk=pk, restaurant=request.user.restaurant)
        new_status = request.data.get('status')

        if not new_status or new_status not in [choice[0] for choice in Order.ORDER_STATUS_CHOICES]:
            return Response({'detail': 'Invalid status provided.'}, status=status.HTTP_400_BAD_REQUEST)

        order.status = new_status
        order.save()
        return Response(OrderSerializer(order).data, status=status.HTTP_200_OK)

class OrderItemListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = OrderItemSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        order_pk = self.kwargs.get('order_pk')
        return OrderItem.objects.filter(order__pk=order_pk, order__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        order_pk = self.kwargs.get('order_pk')
        order = get_object_or_404(Order, pk=order_pk, restaurant=self.request.user.restaurant)
        menu_item = get_object_or_404(MenuItem, id=self.request.data['menu_item'], restaurant=self.request.user.restaurant)
        quantity = self.request.data['quantity']
        unit_price = menu_item.price # Use menu item's current price
        total_price = quantity * unit_price

        order_item = serializer.save(
            order=order,
            menu_item=menu_item,
            unit_price=unit_price,
            total_price=total_price
        )
        # Update total amount of the parent order
        order.total_amount += total_price
        order.save()

class OrderItemRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = OrderItemSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        order_pk = self.kwargs.get('order_pk')
        return OrderItem.objects.filter(order__pk=order_pk, order__restaurant=self.request.user.restaurant)

    def perform_update(self, serializer):
        old_order_item = self.get_object()
        old_total_price = old_order_item.total_price

        order_item = serializer.save()
        
        # Update parent order's total amount
        order = order_item.order
        order.total_amount = order.total_amount - old_total_price + order_item.total_price
        order.save()

    def perform_destroy(self, instance):
        # Deduct item price from parent order's total_amount before deleting
        order = instance.order
        order.total_amount -= instance.total_price
        order.save()
        instance.delete()
