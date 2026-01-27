from rest_framework import status, permissions, generics, viewsets
import logging

logger = logging.getLogger(__name__)

from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.shortcuts import get_object_or_404
from .serializers import (
    CustomUserSerializer, RestaurantSerializer, StaffInvitationSerializer,
    PinLoginSerializer, StaffProfileSerializer, StaffSerializer, UserSerializer
)
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, UserInvitation, StaffProfile, AuditLog
from django.utils import timezone
from django.core.files.base import ContentFile
import base64, os, sys
from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import UserManager
from django.contrib.auth.models import UserManager
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.utils.html import strip_tags
from django.template.loader import render_to_string
from django.conf import settings
from notifications.services import notification_service
from .models import InvitationDeliveryLog
from .services import sync_user_to_lua_agent

class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'ADMIN'

class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'SUPER_ADMIN'

class IsManagerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['ADMIN', 'SUPER_ADMIN', 'MANAGER']

def get_client_ip(request):
    """Get client IP address from request."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0]
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def pin_login(request):
    serializer = PinLoginSerializer(data=request.data)
    ip_address = get_client_ip(request)
    user_agent = request.META.get('HTTP_USER_AGENT', '')
    
    if serializer.is_valid():
        user = serializer.validated_data['user']
        image_data = request.data.get('image_data')  # Get from request.data instead of validated_data
        
        # Log successful PIN login
        AuditLog.create_log(
            restaurant=user.restaurant,
            user=user,
            action_type='LOGIN_PIN',
            entity_type='USER',
            entity_id=user.id,
            description=f'Successful PIN login for user {user.email}',
            ip_address=ip_address,
            user_agent=user_agent
        )
        
        if image_data:
            try:
                # Decode base64 image data
                format, imgstr = image_data.split(';base64,') # format looks like: data:image/png
                ext = format.split('/')[-1]
                
                data = ContentFile(base64.b64decode(imgstr), name=f'{user.id}_clock_in.{ext}')
                
                # Save the image (you might want to link this to a ClockEvent or UserProfile)
                # For now, let's just save it to a temporary location or a user-specific folder
                image_path = os.path.join(settings.MEDIA_ROOT, 'clock_in_photos', data.name)
                os.makedirs(os.path.dirname(image_path), exist_ok=True)
                with open(image_path, 'wb+') as destination:
                    destination.write(data.read())
                
                # You can also save the image path to the user's profile or a clock event model
                # For example: user.profile.clock_in_photo = image_path
                # user.profile.save()
            except Exception as e:
                # Log image processing error but don't fail the login
                AuditLog.create_log(
                    restaurant=user.restaurant,
                    user=user,
                    action_type='ERROR',
                    entity_type='USER',
                    entity_id=user.id,
                    description=f'Failed to process clock-in image: {str(e)}',
                    ip_address=ip_address,
                    user_agent=user_agent
                )
            
        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)
        access_token = str(refresh.access_token)
        
        # Sync user context to Lua AI agent (non-blocking)
        try:
            sync_user_to_lua_agent(user, access_token)
        except Exception:
            pass  # Don't fail login if Lua sync fails
        
        return Response({
            'refresh': str(refresh),
            'access': access_token,
            'user': CustomUserSerializer(user).data
        })
    else:
        # Log failed PIN login attempt
        pin_code = request.data.get('pin_code', '')
        email = request.data.get('email', '')
        
        # Try to find user for logging (without revealing if user exists)
        user_for_log = None
        if email:
            try:
                user_for_log = CustomUser.objects.get(email=email)
            except CustomUser.DoesNotExist:
                pass
        
        # Log failed attempt
        AuditLog.create_log(
            restaurant=user_for_log.restaurant if user_for_log else None,
            user=user_for_log,
            action_type='LOGIN_PIN_FAILED',
            entity_type='USER',
            entity_id=user_for_log.id if user_for_log else None,
            description=f'Failed PIN login attempt for {"email: " + email if email else "PIN only login"}',
            ip_address=ip_address,
            user_agent=user_agent
        )
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def user_profile(request):
    serializer = CustomUserSerializer(request.user) # Changed UserSerializer to CustomUserSerializer
    return Response(serializer.data)


@api_view(['PUT'])
@permission_classes([IsAdmin])
def update_restaurant_location(request):
    restaurant = request.user.restaurant
    serializer = RestaurantSerializer(restaurant, data=request.data, partial=True)
    
    if serializer.is_valid():
        serializer.save()
        return Response(serializer.data)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def restaurant_location(request):
    restaurant = request.user.restaurant
    return Response({
        'latitude': restaurant.latitude,
        'longitude': restaurant.longitude,
        'radius': restaurant.radius,
        'address': restaurant.address,
        'name': restaurant.name,
        'phone': restaurant.phone,
        'email': restaurant.email,
        'timezone': restaurant.timezone,
        'currency': restaurant.currency,
        'language': restaurant.language,
    })


class RestaurantOwnerSignupView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        restaurant_serializer = RestaurantSerializer(data=request.data.get('restaurant'))
        if not restaurant_serializer.is_valid():
            return Response(restaurant_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        restaurant = restaurant_serializer.save()

        user_data = request.data.get('user')
        user_data['restaurant'] = restaurant.id
        user_data['role'] = 'SUPER_ADMIN'
        user_data['is_verified'] = True
        password = user_data.pop('password', None)
        pin_code = user_data.pop('pin_code', None)

        user_serializer = CustomUserSerializer(data=user_data)
        if not user_serializer.is_valid():
            restaurant.delete()
            return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        user = user_serializer.save()
        user.set_password(password)
        if pin_code:
            user.set_pin(pin_code)
        user.save()

        refresh = RefreshToken.for_user(user)
        return Response({
            'user': UserSerializer(user).data,
            'restaurant': restaurant_serializer.data,
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        }, status=status.HTTP_201_CREATED)

class LoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')

        if not email or not password:
            return Response(
                {'error': 'Email and password are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user = CustomUser.objects.get(email=email, is_active=True)
            
            # Check if account is locked
            if user.is_account_locked():
                AuditLog.create_log(
                    restaurant=user.restaurant,
                    user=user,
                    action_type='LOGIN_FAILED',
                    entity_type='USER',
                    entity_id=user.id,
                    description=f'Login attempt on locked account for {email}',
                    ip_address=ip_address,
                    user_agent=user_agent
                )
                return Response(
                    {'error': 'Account is temporarily locked due to multiple failed attempts. Please try again later.'},
                    status=status.HTTP_423_LOCKED
                )
            
            # Check if user is admin (should use password, not PIN)
            if not user.is_admin_role():
                AuditLog.create_log(
                    restaurant=user.restaurant,
                    user=user,
                    action_type='LOGIN_FAILED',
                    entity_type='USER',
                    entity_id=user.id,
                    description=f'Password login attempted for non-admin user {email}',
                    ip_address=ip_address,
                    user_agent=user_agent
                )
                return Response(
                    {'error': 'Password authentication is only available for admin users. Staff should use PIN login.'},
                    status=status.HTTP_403_FORBIDDEN
                )
            
        except CustomUser.DoesNotExist:
            # Log failed attempt for non-existent user
            AuditLog.create_log(
                restaurant=None,
                user=None,
                action_type='LOGIN_FAILED',
                entity_type='USER',
                description=f'Login attempt for non-existent user {email}',
                ip_address=ip_address,
                user_agent=user_agent
            )
            return Response(
                {'error': 'Invalid credentials'},
                status=status.HTTP_401_UNAUTHORIZED
            )

        # Authenticate user (pass request for backend compatibility)
        authenticated_user = authenticate(request=request, email=email, password=password)

        if authenticated_user:
            # Reset failed attempts on successful login
            authenticated_user.reset_failed_attempts()
            
            # Log successful login
            AuditLog.create_log(
                restaurant=authenticated_user.restaurant,
                user=authenticated_user,
                action_type='LOGIN',
                entity_type='USER',
                entity_id=authenticated_user.id,
                description=f'Successful password login for user {email}',
                ip_address=ip_address,
                user_agent=user_agent
            )
            
            refresh = RefreshToken.for_user(authenticated_user)
            access_token = str(refresh.access_token)
            
            # Sync user context to Lua AI agent (non-blocking)
            try:
                sync_user_to_lua_agent(authenticated_user, access_token)
            except Exception:
                pass  # Don't fail login if Lua sync fails
            
            return Response({
                'user': UserSerializer(authenticated_user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': access_token,
                }
            })
        else:
            # Increment failed attempts and log
            user.increment_failed_attempts()
            
            AuditLog.create_log(
                restaurant=user.restaurant,
                user=user,
                action_type='LOGIN_FAILED',
                entity_type='USER',
                entity_id=user.id,
                description=f'Failed password login for user {email}',
                ip_address=ip_address,
                user_agent=user_agent
            )

        return Response(
            {'error': 'Invalid credentials'},
            status=status.HTTP_401_UNAUTHORIZED
        )

class LogoutView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        try:
            refresh_token = request.data["refresh_token"]
            token = RefreshToken(refresh_token)
            token.blacklist()
            return Response(status=status.HTTP_205_RESET_CONTENT)
        except Exception as e:
            return Response(status=status.HTTP_400_BAD_REQUEST)

class MeView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)
        
    def patch(self, request):
        user = request.user
        profile_data = request.data.pop('profile', None)
        
        # Update user data
        user_serializer = UserSerializer(user, data=request.data, partial=True)
        if not user_serializer.is_valid():
            return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        user_serializer.save()
        
        # Update or create profile data if provided
        if profile_data:
            profile = getattr(user, 'profile', None)
            if not profile:
                # Create profile if it doesn't exist
                profile = StaffProfile.objects.create(user=user)
            
            profile_serializer = StaffProfileSerializer(profile, data=profile_data, partial=True)
            if profile_serializer.is_valid():
                profile_serializer.save()
            else:
                return Response(profile_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        # Return updated user data (refetched for consistency)
        user.refresh_from_db()
        serializer = UserSerializer(user)
        return Response(serializer.data)

class InviteStaffView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def post(self, request):
        email = request.data.get('email')
        role = request.data.get('role')
        phone_number = request.data.get('phone_number')
        send_whatsapp = bool(request.data.get('send_whatsapp', False))

        if not role:
            return Response({'error': 'Role is required.'}, status=status.HTTP_400_BAD_REQUEST)
        if not email and not (send_whatsapp and phone_number):
            return Response({'error': 'Email is required unless sending via WhatsApp with a phone number.'}, status=status.HTTP_400_BAD_REQUEST)
        
        if email and CustomUser.objects.filter(email=email).exists():
            return Response({'error': 'User with this email already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        if email and UserInvitation.objects.filter(email=email, is_accepted=False, expires_at__gt=timezone.now()).exists():
            return Response({'error': 'A pending invitation already exists for this email.'}, status=status.HTTP_400_BAD_REQUEST)

        token = get_random_string(64)
        expires_at = timezone.now() + timezone.timedelta(days=7)

        invitation = UserInvitation.objects.create(
            email=email or '',
            role=role,
            invited_by=request.user,
            restaurant=request.user.restaurant,
            invitation_token=token,
            expires_at=expires_at
        )

        first_name = (request.data.get('first_name') or '').strip() or None
        last_name = (request.data.get('last_name') or '').strip() or None
        if first_name:
            invitation.first_name = first_name
        if last_name:
            invitation.last_name = last_name
        invitation.extra_data = {
            'phone_number': phone_number,
            'phone': phone_number,
            'first_name': first_name,
            'last_name': last_name,
            'department': request.data.get('department')
        }
        invitation.save(update_fields=['first_name', 'last_name', 'extra_data'])

        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={token}"
        print(f"Staff Invitation Link: {invite_link}")

        if email:
            html_message = render_to_string('emails/staff_invite.html', {
                'invite_link': invite_link,
                'restaurant_name': request.user.restaurant.name,
                'year': timezone.now().year
            })
            plain_message = strip_tags(html_message)

        try:
            if email:
                send_mail(
                    'You\'ve been invited to join Mizan AI!',
                    plain_message,
                    settings.DEFAULT_FROM_EMAIL,
                    [email],
                    html_message=html_message,
                    fail_silently=False,
                )
                email_log = InvitationDeliveryLog(
                    invitation=invitation,
                    channel='email',
                    recipient_address=email,
                    status='SENT'
                )
                email_log.delivered_at = timezone.now()
                email_log.save()
                try:
                    AuditLog.create_log(
                        restaurant=request.user.restaurant,
                        user=request.user,
                        action_type='CREATE',
                        entity_type='INVITATION',
                        entity_id=str(invitation.id),
                        description='Staff invitation created and email sent',
                        old_values={},
                        new_values={'email': email, 'role': role}
                    )
                except Exception:
                    pass
            if send_whatsapp and phone_number:
                from .tasks import send_whatsapp_invitation_task
                # Always use background task for reliability and better UX (no hang)
                send_whatsapp_invitation_task.delay(
                    invitation_id=str(invitation.id),
                    phone=phone_number,
                    first_name=first_name or "Staff",
                    restaurant_name=request.user.restaurant.name,
                    invite_link=invite_link,
                    support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
                )
                
                # Mock a successful log entry as PENDING
                InvitationDeliveryLog.objects.create(
                    invitation=invitation,
                    channel='whatsapp',
                    recipient_address=phone_number,
                    status='PENDING'
                )
                
                try:
                    AuditLog.create_log(
                        restaurant=request.user.restaurant,
                        user=request.user,
                        action_type='CREATE',
                        entity_type='INVITATION',
                        entity_id=str(invitation.id),
                        description='WhatsApp invitation scheduled via Lua Agent',
                        old_values={},
                        new_values={'email': email, 'phone': phone_number}
                    )
                except Exception:
                    pass

            return Response({'message': 'Invitation processed successfully', 'token': token}, status=status.HTTP_201_CREATED)
        except Exception as e:
            logger.error(f"InviteStaffView error: {str(e)}")
            # In development, surface the underlying error to speed up debugging.
            if getattr(settings, 'DEBUG', False):
                return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            return Response(
                {'error': 'An unexpected error occurred while processing the invitation.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

class AcceptInvitationView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        data = request.data
        token = data.get('token')
        first_name = data.get('first_name')
        last_name = data.get('last_name')
        pin_code = data.get('pin_code')
        provided_email = data.get('email')

        # Build an error dictionary
        print(data, file=sys.stderr)
        errors = {}
        if not token:
            errors['token'] = 'This field is required.'
        if not first_name:
            errors['first_name'] = 'This field is required.'
        if not last_name:
            errors['last_name'] = 'This field is required.'
        if not pin_code:
            errors['pin_code'] = 'This field is required.' # <-- Changed from 'password'
        if pin_code and (len(pin_code) < 4 or len(pin_code) > 8 or not pin_code.isdigit()):
            errors['pin_code'] = 'PIN code must be 4 to 8 digits long.'
        if errors:
            return Response(errors, status=status.HTTP_400_BAD_REQUEST)

        try:
            invitation = UserInvitation.objects.get(
                invitation_token=token,
                is_accepted=False,
                expires_at__gt=timezone.now()
            )
            final_email = invitation.email
            if not final_email:
                if not provided_email:
                    return Response({'email': 'Email is required to complete setup.'}, status=status.HTTP_400_BAD_REQUEST)
                # Basic validation and uniqueness check
                if CustomUser.objects.filter(email=provided_email).exists():
                    return Response({'email': 'User with this email already exists'}, status=status.HTTP_400_BAD_REQUEST)
                final_email = provided_email
            else:
                if CustomUser.objects.filter(email=final_email).exists():
                    return Response({'error': 'User with this email already exists'}, status=status.HTTP_400_BAD_REQUEST)
            
            # 1. Generate a secure, random password the user will never see
           # Creates a random 12-character string to use as the password
            random_password = get_random_string(12)

            # 2. Create the user with the random password
            user = CustomUser.objects.create_user(
                email=final_email,
                password=random_password,  # <-- Use the random password
                first_name=first_name,
                last_name=last_name,
                role=invitation.role,
                restaurant=invitation.restaurant,
                is_verified=True
            )
            
            # 3. Set the PIN
            user.set_pin(pin_code)  # We know pin_code exists because we checked it
            user.save()

            from django.utils import timezone as dj_tz
            invitation.is_accepted = True
            invitation.status = 'ACCEPTED'
            invitation.accepted_at = dj_tz.now()
            if not invitation.email:
                invitation.email = final_email
            invitation.save(update_fields=['is_accepted', 'status', 'accepted_at'])

            # Close any other pending invitations for the same email within this restaurant
            UserInvitation.objects.filter(
                restaurant=invitation.restaurant,
                email=invitation.email,
                is_accepted=False,
            ).update(status='EXPIRED', expires_at=dj_tz.now())

            refresh = RefreshToken.for_user(user)

            return Response({
                'user': UserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_201_CREATED)

        except UserInvitation.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired invitation'},
                status=status.HTTP_400_BAD_REQUEST
            )

class StaffListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return UserInvitation.objects.filter(restaurant=self.request.user.restaurant).order_by('-created_at')

class StaffProfileUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        staff_member = get_object_or_404(CustomUser, pk=pk, restaurant=request.user.restaurant)
        
        # Extract profile data to handle it separately if needed, 
        # or rely on the serializer's update method.
        serializer = CustomUserSerializer(staff_member, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        staff_member = get_object_or_404(CustomUser, pk=pk, restaurant=request.user.restaurant)
        staff_member.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)

class RestaurantDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get(self, request):
        restaurant = request.user.restaurant
        serializer = RestaurantSerializer(restaurant)
        return Response(serializer.data)

    def put(self, request):
        restaurant = request.user.restaurant
        serializer = RestaurantSerializer(restaurant, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class CustomTokenObtainPairView(TokenObtainPairView):
    pass


class CustomTokenRefreshView(TokenRefreshView):
    pass


class RegisterView(RestaurantOwnerSignupView):
    pass


class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        return Response({'message': 'Not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)


class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        email = request.data.get('email')
        
        if not email:
            return Response(
                {'error': 'Email is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Always return success to prevent email enumeration
        success_response = Response({
            'message': 'If an account exists with this email, you will receive a password reset link shortly.'
        })
        
        try:
            user = CustomUser.objects.get(email=email, is_active=True)
            
            # Only allow password reset for admin/manager users
            if not user.is_admin_role():
                logger.info(f"Password reset skipped for non-admin user: {email}")
                return success_response
            
            # Generate reset token
            token = user.generate_password_reset_token()
            
            # Build reset link
            reset_link = f"{settings.FRONTEND_URL}/reset-password?token={token}"
            
            # Render email template
            html_message = render_to_string('emails/password_reset.html', {
                'reset_link': reset_link,
                'user_name': user.first_name or 'User',
                'year': timezone.now().year
            })
            plain_message = strip_tags(html_message)
            
            # Send email
            send_mail(
                'Reset Your Mizan AI Password',
                plain_message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                html_message=html_message,
                fail_silently=False,
            )
            
            # Log the password reset request
            AuditLog.create_log(
                restaurant=user.restaurant,
                user=user,
                action_type='OTHER',
                entity_type='USER',
                entity_id=str(user.id),
                description='Password reset requested',
                ip_address=get_client_ip(request),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            
        except CustomUser.DoesNotExist:
            pass  # Don't reveal that email doesn't exist
        except Exception as e:
            # Log error but don't reveal to user
            print(f"Password reset error: {e}")
        
        return success_response


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        token = request.data.get('token')
        new_password = request.data.get('new_password')
        
        if not token:
            return Response(
                {'error': 'Reset token is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not new_password:
            return Response(
                {'error': 'New password is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Find user by token
        try:
            user = CustomUser.objects.get(
                password_reset_token=token,
                is_active=True
            )
        except CustomUser.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired reset token'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate token hasn't expired
        if not user.validate_password_reset_token(token):
            return Response(
                {'error': 'Reset token has expired. Please request a new password reset.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate password complexity
        try:
            user.validate_password_complexity(new_password)
        except ValidationError as e:
            return Response(
                {'error': str(e.message)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Update password
        user.set_password(new_password)
        user.clear_password_reset_token()
        user.save()
        
        # Log the password change
        AuditLog.create_log(
            restaurant=user.restaurant,
            user=user,
            action_type='PASSWORD_CHANGED',
            entity_type='USER',
            entity_id=str(user.id),
            description='Password reset via email link',
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )
        
        return Response({
            'message': 'Password has been reset successfully. You can now log in with your new password.'
        })


class RestaurantUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    
    def put(self, request):
        restaurant = request.user.restaurant
        old_name = restaurant.name
        serializer = RestaurantSerializer(restaurant, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()

            # If name changed, log audit and broadcast update
            new_name = serializer.instance.name
            updated_fields = list(request.data.keys())
            if 'name' in updated_fields and new_name != old_name:
                try:
                    ip_address = get_client_ip(request)
                    user_agent = request.META.get('HTTP_USER_AGENT', '')
                    AuditLog.create_log(
                        restaurant=restaurant,
                        user=request.user,
                        action_type='UPDATE',
                        entity_type='RESTAURANT',
                        entity_id=str(restaurant.id),
                        description='Restaurant name updated',
                        old_values={'name': old_name},
                        new_values={'name': new_name},
                        ip_address=ip_address,
                        user_agent=user_agent,
                    )

                    # Broadcast settings update to restaurant group
                    from channels.layers import get_channel_layer
                    from asgiref.sync import async_to_sync
                    from django.utils import timezone

                    channel_layer = get_channel_layer()
                    group_name = f'restaurant_settings_{str(restaurant.id)}'
                    event = {
                        'type': 'settings_update',
                        'payload': {
                            'restaurant_id': str(restaurant.id),
                            'updated_fields': updated_fields,
                            'restaurant': {
                                'id': str(restaurant.id),
                                'name': new_name,
                            },
                            'timestamp': timezone.now().isoformat(),
                        }
                    }
                    async_to_sync(channel_layer.group_send)(group_name, event)
                except Exception:
                    # Avoid breaking API flow on broadcast/audit errors
                    pass

            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StaffInvitationCreateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    
    def post(self, request):
        return InviteStaffView().post(request)


class StaffInvitationAcceptView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        return AcceptInvitationView().post(request)


class StaffInvitationListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    
    def get(self, request):
        invitations = UserInvitation.objects.filter(restaurant=request.user.restaurant)
        serializer = StaffInvitationSerializer(invitations, many=True)
        return Response(serializer.data)


class ResendVerificationEmailView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        return Response({'message': 'Not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)

class StaffListAPIView(generics.ListAPIView):
    """
    Lists all *active* staff members for the manager's restaurant.
    """
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    serializer_class = StaffSerializer

    def get_queryset(self):
        """
        This method is automatically called to get the list of objects.
        """
        user = self.request.user
        
        return CustomUser.objects.filter(
            restaurant=user.restaurant,
            is_active=True,
            ).exclude(role='SUPER_ADMIN').order_by('first_name', 'last_name')
            
    
class StaffPinLoginView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        pin_code = request.data.get('pin_code')

        if not email or not pin_code:
            return Response(
                {'error': 'Email and PIN code are required.'}, 
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            # 1. Find the user by email
            user = CustomUser.objects.get(email=email)
        except CustomUser.DoesNotExist:
            return Response(
                {'error': 'Invalid credentials.'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )

        # 2. Check the PIN
        if not user.check_pin(pin_code):
            return Response(
                {'error': 'Invalid credentials.'}, 
                status=status.HTTP_401_UNAUTHORIZED
            )

        # 3. Generate tokens
        refresh = RefreshToken.for_user(user)

        # 4. Return user data and tokens
        return Response({
            'user': CustomUserSerializer(user).data,
            'tokens': {
                'refresh': str(refresh),
                'access': str(refresh.access_token),
            }
        }, status=status.HTTP_200_OK)
    
class StaffPasswordResetView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def post(self, request, pk):
        staff_member = get_object_or_404(CustomUser, pk=pk, restaurant=request.user.restaurant)
        new_password = request.data.get('password')
        
        if not new_password:
            return Response({'error': 'Password is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Basic validation for staff PINS/passwords
        if len(new_password) < 4:
            return Response({'error': 'Password must be at least 4 characters'}, status=status.HTTP_400_BAD_REQUEST)
            
        staff_member.set_password(new_password) # For staff who have login access
        if len(new_password) == 4 and new_password.isdigit():
            staff_member.set_pin(new_password) # Also update PIN if it looks like one
            
        staff_member.save()
        
        AuditLog.create_log(
            restaurant=request.user.restaurant,
            user=request.user,
            action_type='PASSWORD_CHANGED',
            entity_type='USER',
            entity_id=str(staff_member.id),
            description=f'Password reset for {staff_member.email} by manager'
        )
        
        return Response({'message': 'Password reset successfully'})
