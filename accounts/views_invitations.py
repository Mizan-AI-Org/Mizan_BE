"""
User Management and Invitation API Views
"""
from rest_framework import viewsets, status, pagination
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from rest_framework_simplejwt.tokens import RefreshToken
from django.db.models import Q
from django.utils import timezone
from django.conf import settings
from datetime import timedelta

from .models import CustomUser, UserInvitation
from .serializers import (
    UserSerializer, StaffInvitationSerializer,
    BulkInviteSerializer, AcceptInvitationSerializer,
    UpdateUserRoleSerializer
)
from .tasks import send_whatsapp_invitation_task
from .services import UserManagementService
from core.permissions import IsRestaurantOwnerOrManager, IsOwnerOrSuperAdmin
import logging

logger = logging.getLogger(__name__)


def normalize_phone(phone):
    """
    Normalize phone number to digits only (no +, spaces, or dashes).
    Format: 2203736808 (country code + local number, no +)
    """
    if not phone:
        return ""
    return ''.join(filter(str.isdigit, str(phone)))


class StandardResultsSetPagination(pagination.PageNumberPagination):
    page_size = 10
    page_size_query_param = 'page_size'
    max_page_size = 500


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
    pagination_class = StandardResultsSetPagination
    
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
    
    def _only_owner_or_super_admin(self, request):
        if request.user.role not in ['OWNER', 'SUPER_ADMIN']:
            return Response(
                {'detail': 'Only the restaurant Owner or Super Admin can deactivate or delete staff.'},
                status=status.HTTP_403_FORBIDDEN
            )
        return None

    def destroy(self, request, *args, **kwargs):
        """Permanently delete a staff member. Only Owner and Super Admin."""
        if self._only_owner_or_super_admin(request):
            return self._only_owner_or_super_admin(request)
        user = self.get_object()
        success, message = UserManagementService.delete_user(user=user, deleted_by=request.user)
        if success:
            return Response(status=status.HTTP_204_NO_CONTENT)
        return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=True, methods=['post'])
    def deactivate(self, request, pk=None):
        """Deactivate user (set is_active=False). Only Owner and Super Admin."""
        if self._only_owner_or_super_admin(request):
            return self._only_owner_or_super_admin(request)
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
            return UserInvitation.objects.none()
        
        # Filter by restaurant (tenant isolation)
        queryset = UserInvitation.objects.filter(restaurant=user.restaurant)
        
        # Filter by status
        is_accepted = self.request.query_params.get('is_accepted')
        if is_accepted is not None:
            queryset = queryset.filter(is_accepted=is_accepted.lower() == 'true')
        
        # Filter by expired
        show_expired = self.request.query_params.get('show_expired', 'false')
        if show_expired.lower() == 'false':
            queryset = queryset.filter(expires_at__gt=timezone.now())
        
        return queryset.order_by('-sent_at')
    
    def create(self, request, *args, **kwargs):
        """Create single invitation and send email/prepare WhatsApp link, returning JSON even on errors."""
        try:
            serializer = self.get_serializer(data=request.data)
            serializer.is_valid(raise_exception=True)

            # Validate restaurant context
            user = request.user
            restaurant = getattr(user, 'restaurant', None)
            if not restaurant:
                return Response(
                    {'detail': 'No restaurant context for current user'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Extract contact information
            email = serializer.validated_data.get('email')
            phone = request.data.get('phone_number') or request.data.get('phone')
            send_whatsapp = request.data.get('send_whatsapp', False)
            
            # Validate at least one contact method
            if not email and not phone:
                return Response(
                    {'detail': 'Either email or phone number must be provided'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            # Prevent duplicate pending invitations (check email if provided)
            if email:
                existing = UserInvitation.objects.filter(
                    restaurant=restaurant,
                    email=email,
                    is_accepted=False,
                    expires_at__gt=timezone.now()
                ).first()
                if existing:
                    return Response(
                        {'detail': f'Invitation already pending for {email}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

            # Generate secure token and expiry
            import secrets
            token = secrets.token_urlsafe(32)
            expires_in_days = int(request.data.get('expires_in_days', 7))
            expires_at = timezone.now() + timedelta(days=expires_in_days)

            # Normalize extra_data from request for convenience
            extra_data = serializer.validated_data.get('extra_data') or {}
            # Allow top-level fields to be merged into extra_data if provided
            for key in ('first_name', 'last_name', 'department', 'phone', 'phone_number'):
                if key in request.data and request.data.get(key) is not None:
                    extra_data[key] = request.data.get(key)

            # Save with server-side fields
            invitation = serializer.save(
                restaurant=restaurant,
                invited_by=user,
                invitation_token=token,
                expires_at=expires_at,
                extra_data=extra_data,
            )

            # Send invitation email only if email is provided and not WhatsApp-only
            email_ok = False
            if email and not send_whatsapp:
                email_ok = UserManagementService._send_invitation_email(invitation)
            
            headers = self.get_success_headers(StaffInvitationSerializer(invitation).data)
            response_data = StaffInvitationSerializer(invitation).data
            
            if send_whatsapp or (phone and not email):
                # WhatsApp invitation - return token for frontend to generate link
                # AND trigger the background task to send the message
                if phone:
                    # Normalize phone: digits only, no + or spaces (e.g., "2203736808")
                    clean_phone = normalize_phone(phone)
                    
                    invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={token}"
                    send_whatsapp_invitation_task.delay(
                        invitation_id=str(invitation.id),
                        phone=clean_phone,
                        first_name=invitation.first_name,
                        restaurant_name=restaurant.name,
                        invite_link=invite_link,
                        support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
                    )
                    
                    # Create the log entry as PENDING
                    from .models import InvitationDeliveryLog
                    InvitationDeliveryLog.objects.create(
                        invitation=invitation,
                        channel='whatsapp',
                        recipient_address=clean_phone,
                        status='PENDING'
                    )

                return Response(
                    response_data,
                    status=status.HTTP_201_CREATED,
                    headers=headers
                )
            elif email_ok:
                return Response(
                    response_data,
                    status=status.HTTP_201_CREATED,
                    headers=headers
                )
            else:
                # Invitation is created even if email fails; return 201 with a message
                return Response(
                    {
                        'detail': 'Invitation created successfully, but email failed to send',
                        'invitation': response_data,
                    },
                    status=status.HTTP_201_CREATED,
                    headers=headers
                )
        except Exception as e:
            logger.error(f"Invitation creation error: {str(e)}")
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    @action(detail=False, methods=['post'])
    def bulk(self, request):
        """
        Bulk invite users from CSV or JSON
        
        CSV Format: email,role,first_name,last_name
        JSON Format: [{"email": "user@example.com", "role": "WAITER"}, ...]
        """
        import traceback

        try:
            serializer = BulkInviteSerializer(data=request.data)
            
            if not serializer.is_valid():
                logger.warning("Bulk invite serializer errors: %s", serializer.errors)
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

        except Exception as e:
            logger.error("Bulk invite error: %s", e, exc_info=True)
            return Response(
                {"detail": str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )

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
            refresh = RefreshToken.for_user(user)
            return Response({
                'detail': 'Account created successfully',
                'user': UserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                },
            }, status=status.HTTP_201_CREATED)
        else:
            payload = {'detail': error}
            if error == 'already_accepted':
                payload['code'] = 'already_accepted'
                payload['detail'] = "You've already accepted this invitation. Please log in."
            return Response(payload, status=status.HTTP_400_BAD_REQUEST)

    @action(detail=False, methods=['get'], permission_classes=[AllowAny], url_path='by-token')
    def by_token(self, request):
        """
        Lightweight lookup for invitation metadata by token.

        Used by the public Accept Invitation page to decide whether the flow
        should use a password (admins/managers) or a PIN (frontline staff),
        without requiring authentication.
        """
        token = request.query_params.get('token')
        if not token:
            return Response(
                {'detail': 'token query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            invitation = UserInvitation.objects.get(invitation_token=token)
        except UserInvitation.DoesNotExist:
            return Response(
                {'detail': 'Invitation not found'},
                status=status.HTTP_404_NOT_FOUND,
            )

        # Basic expiry check – front-end can still show a nicer message
        if invitation.expires_at <= timezone.now():
            return Response(
                {
                    'detail': 'Invitation has expired',
                    'status': invitation.status,
                    'is_expired': True,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        extra = invitation.extra_data or {}
        phone = extra.get('phone') or extra.get('phone_number')

        return Response(
            {
                'id': str(invitation.id),
                'email': invitation.email,
                'role': invitation.role,
                'first_name': invitation.first_name,
                'last_name': invitation.last_name,
                'status': invitation.status,
                'is_accepted': invitation.is_accepted,
                'has_phone': bool(phone),
                'restaurant_name': invitation.restaurant.name if invitation.restaurant_id else None,
            }
        )
    
    @action(detail=True, methods=['post'])
    def resend(self, request, pk=None):
        """Resend invitation via email and/or WhatsApp"""
        try:
            invitation = self.get_object()
            
            if invitation.is_accepted:
                return Response(
                    {'detail': 'Invitation already accepted'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            if invitation.expires_at < timezone.now():
                invitation.expires_at = timezone.now() + timedelta(days=7)
                invitation.save()
            
            email_success = False
            whatsapp_success = False
            if invitation.email:
                email_success = UserManagementService._send_invitation_email(invitation)

            # Send WhatsApp if phone number exists
            raw_phone = (invitation.extra_data or {}).get('phone') or (invitation.extra_data or {}).get('phone_number')
            if raw_phone:
                # Normalize phone: digits only, no + or spaces (e.g., "2203736808")
                from .tasks import normalize_phone
                clean_phone = normalize_phone(raw_phone)
                invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={invitation.invitation_token}"
                try:
                    # Update or create log first (filter-then-update avoids MultipleObjectsReturned when duplicates exist)
                    from .models import InvitationDeliveryLog
                    log = InvitationDeliveryLog.objects.filter(
                        invitation=invitation, channel='whatsapp'
                    ).order_by('-sent_at').first()
                    if log:
                        log.recipient_address = clean_phone
                        log.status = 'PENDING'
                        log.error_message = None
                        log.save(update_fields=['recipient_address', 'status', 'error_message'])
                    else:
                        InvitationDeliveryLog.objects.create(
                            invitation=invitation,
                            channel='whatsapp',
                            recipient_address=clean_phone,
                            status='PENDING',
                        )
                    # Queue the task (with CELERY_TASK_ALWAYS_EAGER=True, this runs synchronously)
                    send_whatsapp_invitation_task.delay(
                        invitation_id=str(invitation.id),
                        phone=clean_phone,
                        first_name=invitation.first_name or "Staff",
                        restaurant_name=invitation.restaurant.name,
                        invite_link=invite_link,
                        support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
                    )
                    whatsapp_success = True
                except Exception as e:
                    logger.error(f"[Resend] Error sending WhatsApp: {str(e)}", exc_info=True)
                    # Update existing log to FAILED if present
                    from .models import InvitationDeliveryLog
                    log = InvitationDeliveryLog.objects.filter(
                        invitation=invitation, channel='whatsapp'
                    ).order_by('-sent_at').first()
                    if log:
                        log.recipient_address = clean_phone
                        log.status = 'FAILED'
                        log.error_message = str(e)
                        log.save(update_fields=['recipient_address', 'status', 'error_message'])
                    else:
                        InvitationDeliveryLog.objects.create(
                            invitation=invitation,
                            channel='whatsapp',
                            recipient_address=clean_phone,
                            status='FAILED',
                            error_message=str(e)
                        )
                    whatsapp_success = True  # Mark as attempted
            
            if email_success or whatsapp_success:
                channels = []
                if email_success:
                    channels.append('email')
                if whatsapp_success:
                    channels.append('WhatsApp')
                return Response({'detail': f'Invitation sent via {", ".join(channels)}'})
            else:
                return Response(
                    {'detail': 'Failed to send invitation via any channel'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            logger.error(f"Resend invitation error: {str(e)}")
            import traceback
            logger.error(traceback.format_exc())
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def destroy(self, request, *args, **kwargs):
        """Cancel/Delete invitation"""
        try:
            instance = self.get_object()
            logger.info(f"Cancelling invitation {instance.id} for {instance.email or instance.extra_data.get('phone')}")
            self.perform_destroy(instance)
            return Response(status=status.HTTP_204_NO_CONTENT)
        except Exception as e:
            logger.error(f"Delete invitation error: {str(e)}")
            return Response({'detail': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    @action(detail=False, methods=['get'])
    def stats(self, request):
        try:
            from .models import InvitationDeliveryLog
            restaurant = request.user.restaurant
            invites = UserInvitation.objects.filter(restaurant=restaurant)
            logs = InvitationDeliveryLog.objects.filter(invitation__in=invites)
            total = invites.count()
            email_sent = logs.filter(channel='email', status='SENT').count()
            whatsapp_sent = logs.filter(channel='whatsapp', status='SENT').count()
            whatsapp_failed = logs.filter(channel='whatsapp', status='FAILED').count()
            pending_whatsapp = logs.filter(channel='whatsapp', status='PENDING').count()
            return Response({
                'total_invitations': total,
                'email_sent': email_sent,
                'whatsapp_sent': whatsapp_sent,
                'whatsapp_failed': whatsapp_failed,
                'whatsapp_pending': pending_whatsapp,
            })
        except Exception as e:
            return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
