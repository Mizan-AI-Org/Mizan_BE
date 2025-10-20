from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from accounts.models import StaffInvitation, CustomUser
from accounts.serializers import StaffInvitationSerializer
from .models import Staff, Category, Product, Order, Table
from .serializers import StaffSerializer, CategorySerializer, ProductSerializer, OrderSerializer, OrderCreateSerializer, TableSerializer
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
import uuid
from rest_framework import generics
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync

# Permissions
class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ['ADMIN', 'SUPER_ADMIN']

class IsManagerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ['admin', 'manager']

class InviteStaffView(APIView):
    def post(self, request):
        if request.user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {'error': 'Permission denied'}, 
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = StaffInvitationSerializer(data=request.data)
        if serializer.is_valid():
            invitation = serializer.save(
                restaurant=request.user.restaurant,
                invited_by=request.user,
                token=str(uuid.uuid4()),
                expires_at=timezone.now() + timedelta(days=7)
            )
            # TODO: Send email invitation
            # send_invitation_email(invitation)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'POST'])
@permission_classes([IsAdmin])
def staff_list(request):
    if request.method == 'GET':
        staff_members = Staff.objects.filter(user__restaurant=request.user.restaurant)
        serializer = StaffSerializer(staff_members, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        # This endpoint is primarily for listing. Create new staff via /staff/create/
        return Response({'detail': 'Staff creation not directly supported via this endpoint.'}, status=status.HTTP_405_METHOD_NOT_ALLOWED)

class StaffCreateView(generics.CreateAPIView):
    queryset = Staff.objects.all()
    serializer_class = StaffSerializer
    permission_classes = [IsAdmin]

    def perform_create(self, serializer):
        user_data = self.request.data.get('user', {})
        user_data['restaurant'] = self.request.user.restaurant.id # Set restaurant from requesting user
        user_data['is_active'] = True # New staff are active by default
        user_data['is_verified'] = False # New staff are not verified by default
        user_data['password'] = user_data.get('password') # Get password from user data
        
        # If a PIN is provided, hash it before passing to serializer
        pin_code = user_data.get('pin_code')
        if pin_code:
            from django.contrib.auth.hashers import make_password
            user_data['pin_code'] = make_password(pin_code)
            
        # Pass updated user_data to the serializer for user creation
        serializer.context['user_data'] = user_data
        serializer.save()

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsManagerOrAdmin])
def staff_detail(request, user_id):
    staff_member = get_object_or_404(Staff, user__id=user_id, user__restaurant=request.user.restaurant)
    
    if request.method == 'GET':
        serializer = StaffSerializer(staff_member)
        return Response(serializer.data)
    
    elif request.method == 'PUT':
        serializer = StaffSerializer(staff_member, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'DELETE':
        # Deactivating the staff member (setting is_active to False on the CustomUser)
        staff_member.user.is_active = False
        staff_member.user.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def staff_dashboard(request):
    total_staff = Staff.objects.filter(user__restaurant=request.user.restaurant, user__is_active=True).count()
    
    return Response({
        'totalStaff': total_staff,
        'activeShifts': 3,  # Mock data
        'pendingOrders': 8,  # Mock data
        'revenueToday': 1250  # Mock data
    })

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def staff_stats(request):
    return Response({
        'totalStaff': Staff.objects.filter(user__restaurant=request.user.restaurant, user__is_active=True).count(),
        'activeShifts': 3,
        'pendingOrders': 8,
        'revenueToday': 1250
    })

@api_view(['DELETE'])
@permission_classes([IsAuthenticated])
def remove_staff(request, staff_id):
    if request.user.role != 'SUPER_ADMIN':
        return Response({'error': 'Only super admin can remove staff'}, status=403)
    
    try:
        staff_user = Staff.objects.get(user__id=staff_id, user__restaurant=request.user.restaurant)
        staff_user.user.delete()
        return Response({'message': 'Staff member removed successfully'})
    except Staff.DoesNotExist:
        return Response({'error': 'Staff member not found'}, status=404)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_staff_role(request, staff_id):
    if request.user.role != 'SUPER_ADMIN':
        return Response({'error': 'Only super admin can update roles'}, status=403)
    
    try:
        staff_user = Staff.objects.get(user__id=staff_id, user__restaurant=request.user.restaurant)
        new_role = request.data.get('role')
        
        if new_role in dict(CustomUser.ROLE_CHOICES):
            staff_user.user.role = new_role
            staff_user.user.save()
            return Response({'message': f'Role updated to {new_role}'})
        else:
            return Response({'error': 'Invalid role'}, status=400)
            
    except Staff.DoesNotExist:
        return Response({'error': 'Staff member not found'}, status=404)

class CategoryListAPIView(generics.ListCreateAPIView):
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return Category.objects.filter(restaurant=self.request.user.restaurant).order_by('display_order')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class CategoryDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Category.objects.filter(restaurant=self.request.user.restaurant)

class ProductListAPIView(generics.ListCreateAPIView):
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        queryset = Product.objects.filter(restaurant=self.request.user.restaurant, is_active=True)
        category_id = self.request.query_params.get('category_id')
        if category_id:
            queryset = queryset.filter(category__id=category_id)
        return queryset.order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class ProductDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = Product.objects.all()
    serializer_class = ProductSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Product.objects.filter(restaurant=self.request.user.restaurant)

class OrderCreateAPIView(generics.CreateAPIView):
    queryset = Order.objects.all()
    serializer_class = OrderCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        order = serializer.save()

        channel_layer = get_channel_layer()
        restaurant_id = str(order.restaurant.id)
        group_name = f'kitchen_orders_{restaurant_id}'
        
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'type': 'send_order_update',
                'order': OrderSerializer(order).data
            }
        )

class OrderDetailAPIView(generics.RetrieveUpdateAPIView):
    queryset = Order.objects.all()
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Order.objects.filter(restaurant=self.request.user.restaurant)

    def perform_update(self, serializer):
        order = serializer.save()
        channel_layer = get_channel_layer()
        restaurant_id = str(order.restaurant.id)
        group_name = f'kitchen_orders_{restaurant_id}'
        
        async_to_sync(channel_layer.group_send)(
            group_name,
            {
                'type': 'send_order_update',
                'order': OrderSerializer(order).data
            }
        )

class StaffOrderListAPIView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Order.objects.filter(staff=self.request.user, restaurant=self.request.user.restaurant).order_by('-created_at')

class RestaurantOrderListAPIView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        # List all orders for the restaurant, excluding COMPLETED and CANCELLED orders
        return Order.objects.filter(
            restaurant=self.request.user.restaurant
        ).exclude(status__in=['COMPLETED', 'CANCELLED']).order_by('-created_at')

class TableListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TableSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return Table.objects.filter(restaurant=self.request.user.restaurant).order_by('number')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class TableDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TableSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Table.objects.filter(restaurant=self.request.user.restaurant)

class TableAssignOrderAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        table = get_object_or_404(Table, pk=pk, restaurant=request.user.restaurant)
        order_id = request.data.get('order_id')

        if not order_id:
            return Response({'error': 'order_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            order = Order.objects.get(id=order_id, restaurant=request.user.restaurant)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)

        if table.current_order:
            return Response({'error': 'Table is already assigned to an order.'}, status=status.HTTP_400_BAD_REQUEST)
        
        table.current_order = order
        table.status = 'OCCUPIED'
        table.save()

        return Response(TableSerializer(table).data, status=status.HTTP_200_OK)

class TableClearOrderAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def post(self, request, pk):
        table = get_object_or_404(Table, pk=pk, restaurant=request.user.restaurant)

        if not table.current_order:
            return Response({'error': 'Table has no active order.'}, status=status.HTTP_400_BAD_REQUEST)
        
        table.current_order = None
        table.status = 'NEEDS_CLEANING'
        table.save()

        return Response(TableSerializer(table).data, status=status.HTTP_200_OK)

class TablesNeedingCleaningListAPIView(generics.ListAPIView):
    serializer_class = TableSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Only retrieve tables that are marked as 'NEEDS_CLEANING'
        return Table.objects.filter(restaurant=self.request.user.restaurant, status='NEEDS_CLEANING').order_by('number')

class MarkTableCleanAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        table = get_object_or_404(Table, pk=pk, restaurant=request.user.restaurant)

        if table.status != 'NEEDS_CLEANING':
            return Response({'error': 'Table does not need cleaning.'}, status=status.HTTP_400_BAD_REQUEST)
        
        table.status = 'AVAILABLE'
        table.save()

        return Response(TableSerializer(table).data, status=status.HTTP_200_OK)