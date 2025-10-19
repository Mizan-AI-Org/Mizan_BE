from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model
from accounts.models import StaffInvitation
from accounts.serializers import UserSerializer, StaffInvitationSerializer
from django.shortcuts import get_object_or_404
from django.utils import timezone
from datetime import timedelta
import uuid

User = get_user_model()

# Permissions
class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role == 'admin'

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
        staff = User.objects.filter(restaurant=request.user.restaurant)
        serializer = UserSerializer(staff, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        serializer = UserSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(restaurant=request.user.restaurant, password=request.data.get('password'))
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET', 'PUT', 'DELETE'])
@permission_classes([IsManagerOrAdmin])
def staff_detail(request, user_id):
    staff = get_object_or_404(User, id=user_id, restaurant=request.user.restaurant)
    
    if request.method == 'GET':
        serializer = UserSerializer(staff)
        return Response(serializer.data)
    
    elif request.method == 'PUT':
        serializer = UserSerializer(staff, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    elif request.method == 'DELETE':
        staff.is_active = False
        staff.save()
        return Response(status=status.HTTP_204_NO_CONTENT)

@api_view(['GET'])
@permission_classes([IsAuthenticated])
def staff_dashboard(request):
    total_staff = User.objects.filter(restaurant=request.user.restaurant).count()
    
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
        'totalStaff': 12,
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
        staff_user = User.objects.get(id=staff_id, restaurant=request.user.restaurant)
        staff_user.delete()
        return Response({'message': 'Staff member removed successfully'})
    except User.DoesNotExist:
        return Response({'error': 'Staff member not found'}, status=404)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_staff_role(request, staff_id):
    if request.user.role != 'SUPER_ADMIN':
        return Response({'error': 'Only super admin can update roles'}, status=403)
    
    try:
        staff_user = User.objects.get(id=staff_id, restaurant=request.user.restaurant)
        new_role = request.data.get('role')
        
        if new_role in dict(User.ROLE_CHOICES):
            staff_user.role = new_role
            staff_user.save()
            return Response({'message': f'Role updated to {new_role}'})
        else:
            return Response({'error': 'Invalid role'}, status=400)
            
    except User.DoesNotExist:
        return Response({'error': 'Staff member not found'}, status=404)