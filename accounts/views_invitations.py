"""
User Management and Invitation API Views
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import Q
from django.utils import timezone

from .models import CustomUser, StaffInvitation
from .serializers import (
    UserSerializer, StaffInvitationSerializer,
    BulkInviteSerializer, AcceptInvitationSerializer,
    UpdateUserRoleSerializer
)
from .services import UserManagementService
from core.permissions import IsRestaurantOwnerOrManager
import logging

logger = logging.getLogger(__name__)


class UserManagementViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing users in a restaurant
    
    Endpoints:
    - GET /api/users/ - List all users
    - POST /api/users/ - Create user (admin only)
    - GET /api/users/{id}/ - Get user details
    - PUT /api/users/{id}/ - Update user
    - DELETE /api/users/{id}/ - Deactivate user
    - PUT /api/users/{id}/update_role/ - Update user role
    - POST /api/users/{id}/deactivate/ - Deactivate user
    - POST /api/users/{id}/reactivate/ - Reactivate user
    """
    serializer_class = UserSerializer
    permission_classes = [IsAuthenticated, IsRestaurantOwnerOrManager]
    
    def get_queryset(self):
        user = self.request.user
        if not hasattr(user, 'restaurant') or not user.restaurant:
            return CustomUser.objects.none()
        
        # Filter by restaurant (tenant isolation)
        queryset = CustomUser.objects.filter(restaurant=user.restaurant)
        
        # Filter by role if provided
        role = self.request.query_params.get('role')
        if role:
            queryset = queryset.filter(role=role)
        
        # Filter by active status
        is_active = self.request.query_params.get('is_active')
        if is_active is not None:
            queryset = queryset.filter(is_active=is_active.lower() == 'true')
        
        # Search by name or email
        search = self.request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                Q(first_name__icontains=search) |
                Q(last_name__icontains=search) |
                Q(email__icontains=search)
            )
        
        return queryset.order_by('first_name', 'last_name')
    
    @action(detail=True, methods=['put'])
    def update_role(self, request, pk=None):
        """Update user role"""
        user = self.get_object()
        serializer = UpdateUserRoleSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        new_role = serializer.validated_data['role']
        success, message = UserManagementService.update_user_role(
            user=user,
            new_role=new_role,
            updated_by=request.user
        )
        
        if success:
            return Response({
                'detail': message,
                'user': UserSerializer(user).data
            })
        else:
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """Deactivate user"""
        user = self.get_object()
        success, message = UserManagementService.deactivate_user(
            user=user,
            deactivated_by=request.user
        )
        
        if success:
            return Response({'detail': message})
        else:
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def reactivate(self, request, pk=None):
        """Reactivate user"""
        user = self.get_object()
        
        if request.user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {'detail': 'Only admins can reactivate users'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        user.is_active = True
        user.save()
        
        return Response({
            'detail': 'User reactivated successfully',
            'user': UserSerializer(user).data
        })


class InvitationViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing staff invitations
    
    Endpoints:
    - GET /api/invitations/ - List invitations
    - POST /api/invitations/ - Create single invitation
    - POST /api/invitations/bulk/ - Bulk invite from CSV or JSON
    - POST /api/invitations/accept/ - Accept invitation (public)
    - DELETE /api/invitations/{id}/ - Cancel invitation
    - POST /api/invitations/{id}/resend/ - Resend invitation email
    """
    serializer_class = StaffInvitationSerializer
    permission_classes = [IsAuthenticated, IsRestaurantOwnerOrManager]
    
    def get_queryset(self):
        user = self.request.user
        if not hasattr(user, 'restaurant') or not user.restaurant:
            return StaffInvitation.objects.none()
        
        # Filter by restaurant (tenant isolation)
        queryset = StaffInvitation.objects.filter(restaurant=user.restaurant)
        
        # Filter by status
        is_accepted = self.request.query_params.get('is_accepted')
        if is_accepted is not None:
            queryset = queryset.filter(is_accepted=is_accepted.lower() == 'true')
        
        # Filter by expired
        show_expired = self.request.query_params.get('show_expired', 'false')
        if show_expired.lower() == 'false':
            queryset = queryset.filter(expires_at__gt=timezone.now())
        
        return queryset.order_by('-created_at')
    
    def perform_create(self, serializer):
        """Create single invitation"""
        invitation = serializer.save(
            restaurant=self.request.user.restaurant,
            invited_by=self.request.user
        )
        
        # Send invitation email
        UserManagementService._send_invitation_email(invitation)
    
    @action(detail=False, methods=['post'])
    def bulk(self, request):
        """
        Bulk invite users from CSV or JSON
        
        CSV Format: email,role,first_name,last_name
        JSON Format: [{"email": "user@example.com", "role": "WAITER"}, ...]
        """
        serializer = BulkInviteSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        invite_type = serializer.validated_data['type']
        
        if invite_type == 'csv':
            csv_content = serializer.validated_data['csv_content']
            results = UserManagementService.bulk_invite_from_csv(
                csv_content=csv_content,
                restaurant=request.user.restaurant,
                invited_by=request.user
            )
        else:  # json
            invitations = serializer.validated_data['invitations']
            results = UserManagementService.bulk_invite_from_list(
                invitations=invitations,
                restaurant=request.user.restaurant,
                invited_by=request.user
            )
        
        return Response({
            'detail': f"Processed {results['success'] + results['failed']} invitations",
            'success': results['success'],
            'failed': results['failed'],
            'errors': results['errors'],
            'invitations': [
                StaffInvitationSerializer(inv).data
                for inv in results['invitations']
            ]
        }, status=status.HTTP_201_CREATED if results['success'] > 0 else status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['post'], permission_classes=[AllowAny])
    def accept(self, request):
        """
        Accept invitation and create user account (public endpoint)
        """
        serializer = AcceptInvitationSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        token = serializer.validated_data['token']
        password = serializer.validated_data['password']
        first_name = serializer.validated_data['first_name']
        last_name = serializer.validated_data['last_name']
        
        user, error = UserManagementService.accept_invitation(
            token=token,
            password=password,
            first_name=first_name,
            last_name=last_name
        )
        
        if user:
            return Response({
                'detail': 'Account created successfully',
                'user': UserSerializer(user).data
            }, status=status.HTTP_201_CREATED)
        else:
            return Response({'detail': error}, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['post'])
    def resend(self, request, pk=None):
        """Resend invitation email"""
        invitation = self.get_object()
        
        if invitation.is_accepted:
            return Response(
                {'detail': 'Invitation already accepted'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if invitation.expires_at < timezone.now():
            return Response(
                {'detail': 'Invitation expired'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Attempt to send invitation email; returns None, log handled inside
        try:
            UserManagementService._send_invitation_email(invitation)
            success = True
        except Exception:
            success = False
        
        if success:
            return Response({'detail': 'Invitation email sent'})
        else:
            return Response(
                {'detail': 'Failed to send email'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )