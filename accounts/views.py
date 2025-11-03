from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView
from django.shortcuts import get_object_or_404
from .serializers import CustomUserSerializer, RestaurantSerializer, StaffInvitationSerializer, PinLoginSerializer, StaffProfileSerializer, UserSerializer
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, StaffInvitation, StaffProfile, AuditLog
from django.utils import timezone
from django.core.files.base import ContentFile
import base64, os, sys
from django.conf import settings
from django.contrib.auth.models import UserManager
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.utils.html import strip_tags
from django.template.loader import render_to_string

class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'ADMIN'

class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'SUPER_ADMIN'

class IsManagerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['ADMIN', 'SUPER_ADMIN']

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
        
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
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
            'user': CustomUserSerializer(user).data,
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

        # Authenticate user
        authenticated_user = authenticate(email=email, password=password)

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
            return Response({
                'user': CustomUserSerializer(authenticated_user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
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
        serializer = CustomUserSerializer(request.user)
        return Response(serializer.data)
        
    def patch(self, request):
        user = request.user
        profile_data = request.data.pop('profile', None)
        
        # Update user data
        user_serializer = CustomUserSerializer(user, data=request.data, partial=True)
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
        
        # Return updated user data
        serializer = CustomUserSerializer(user)
        return Response(serializer.data)
    
    def patch(self, request):
        user = request.user
        profile_data = request.data.pop('profile', None)
        
        # Update user data
        user_serializer = CustomUserSerializer(user, data=request.data, partial=True)
        if user_serializer.is_valid():
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
            
            # Re-fetch user to get updated data
            updated_user = CustomUser.objects.get(pk=user.pk)
            response_serializer = CustomUserSerializer(updated_user)
            return Response(response_serializer.data)
        
        return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class InviteStaffView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def post(self, request):
        email = request.data.get('email')
        role = request.data.get('role')

        if not all([email, role]):
            return Response({'error': 'Email and role are required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        if CustomUser.objects.filter(email=email).exists():
            return Response({'error': 'User with this email already exists.'}, status=status.HTTP_400_BAD_REQUEST)

        if StaffInvitation.objects.filter(email=email, is_accepted=False, expires_at__gt=timezone.now()).exists():
            return Response({'error': 'A pending invitation already exists for this email.'}, status=status.HTTP_400_BAD_REQUEST)

        token = get_random_string(64)
        expires_at = timezone.now() + timezone.timedelta(days=7)

        invitation = StaffInvitation.objects.create(
            email=email,
            role=role,
            invited_by=request.user,
            restaurant=request.user.restaurant,
            token=token,
            expires_at=expires_at
        )

        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={token}"
        print(f"Staff Invitation Link for {email}: {invite_link}")

        # Uncomment and configure email settings in production
        html_message = render_to_string('emails/staff_invite.html', {'invite_link': invite_link, 'restaurant_name': request.user.restaurant.name, 'year': timezone.now().year})
        plain_message = strip_tags(html_message)
        send_mail(
            'You\'ve been invited to join Mizan AI!',
            plain_message,
            settings.DEFAULT_FROM_EMAIL,
            [email],
            html_message=html_message,
            fail_silently=False,
        )

        return Response({'message': 'Invitation sent successfully', 'token': token}, status=status.HTTP_201_CREATED)

class AcceptInvitationView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        data = request.data
        token = data.get('token')
        first_name = data.get('first_name')
        last_name = data.get('last_name')
        pin_code = data.get('pin_code')  # <-- This is now the required field

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
            invitation = StaffInvitation.objects.get(
                token=token,
                is_accepted=False,
                expires_at__gt=timezone.now()
            )

            if CustomUser.objects.filter(email=invitation.email).exists():
                return Response(
                    {'error': 'User with this email already exists'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # 1. Generate a secure, random password the user will never see
           # Creates a random 12-character string to use as the password
            random_password = get_random_string(12)

            # 2. Create the user with the random password
            user = CustomUser.objects.create_user(
                email=invitation.email,
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

            invitation.is_accepted = True
            invitation.save()

            refresh = RefreshToken.for_user(user)

            return Response({
                'user': CustomUserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_201_CREATED)

        except StaffInvitation.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired invitation'},
                status=status.HTTP_400_BAD_REQUEST
            )

class StaffListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return StaffInvitation.objects.filter(restaurant=self.request.user.restaurant).order_by('-created_at')

class StaffProfileUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        staff_member = get_object_or_404(CustomUser, pk=pk, restaurant=request.user.restaurant)
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
        return Response({'message': 'Not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)


class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        return Response({'message': 'Not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)


class RestaurantUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    
    def put(self, request):
        restaurant = request.user.restaurant
        serializer = RestaurantSerializer(restaurant, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
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
        invitations = StaffInvitation.objects.filter(restaurant=request.user.restaurant)
        serializer = StaffInvitationSerializer(invitations, many=True)
        return Response(serializer.data)


class ResendVerificationEmailView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        return Response({'message': 'Not implemented'}, status=status.HTTP_501_NOT_IMPLEMENTED)


class StaffListAPIView(StaffListView):
    pass

    
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
    
