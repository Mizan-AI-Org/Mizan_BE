from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView, TokenRefreshView # Import JWT views
from django.shortcuts import get_object_or_404
from .serializers import CustomUserSerializer, RestaurantSerializer, StaffInvitationSerializer, PinLoginSerializer, StaffProfileSerializer # Removed UserSerializer
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, StaffInvitation, StaffProfile # Added StaffProfile
from django.utils import timezone
from django.core.files.base import ContentFile
import base64
import os
from django.conf import settings
from django.contrib.auth.tokens import default_token_generator
from django.utils.encoding import force_bytes
from django.utils.http import urlsafe_base64_encode
from django.core.mail import EmailMultiAlternatives
from django.template.loader import render_to_string

# New imports for invitation
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.utils.html import strip_tags
from rest_framework import generics
from .models import CustomUser
from .serializers import CustomUserSerializer


class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'ADMIN'

class IsSuperAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role == 'SUPER_ADMIN'

class IsManagerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.is_authenticated and request.user.role in ['ADMIN', 'SUPER_ADMIN']

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def pin_login(request):
    serializer = PinLoginSerializer(data=request.data)
    
    if serializer.is_valid():
        user = serializer.validated_data['user']
        image_data = serializer.validated_data.get('image_data')
        
        if image_data:
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
            
        # Generate JWT tokens
        refresh = RefreshToken.for_user(user)
        
        return Response({
            'refresh': str(refresh),
            'access': str(refresh.access_token),
            'user': CustomUserSerializer(user).data # Changed UserSerializer to CustomUserSerializer
        })
    
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

        user = authenticate(email=email, password=password)

        if user:
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': CustomUserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            })

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

class AcceptInvitationView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get('token')
        password = request.data.get('password')
        first_name = request.data.get('first_name')
        last_name = request.data.get('last_name')
        pin_code = request.data.get('pin_code')

        if not all([token, password, first_name, last_name]):
            return Response({'error': 'Missing required fields.'}, status=status.HTTP_400_BAD_REQUEST)

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

            user = CustomUser.objects.create_user(
                email=invitation.email,
                first_name=first_name,
                last_name=last_name,
                role=invitation.role,
                restaurant=invitation.restaurant,
                password=password,
                is_verified=True
            )
            if pin_code:
                user.set_pin(pin_code)
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

        invite_link = f"http://localhost:5173/accept-invitation?token={token}"
        print(f"Staff Invitation Link for {email}: {invite_link}")

        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Email Config - Backend: {settings.EMAIL_BACKEND}")
        logger.info(f"Email Config - Host: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        logger.info(f"Email Config - TLS: {settings.EMAIL_USE_TLS}")
        logger.info(f"Email Config - From: {settings.DEFAULT_FROM_EMAIL}")

        try:
            subject = "You're invited to join Mizan AI"
            message = f"Hi,\n\nYou have been invited to join {request.user.restaurant.name} on Mizan AI.\n\nPlease follow the link below to accept the invitation and set up your account:\n\n{invite_link}\n\nThis invitation expires in 7 days.\n\nBest regards,\nMizan AI Team"
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            print(f"Invitation email sent successfully to {email}")
        except Exception as e:
            print(f"Error sending invitation email to {email}: {str(e)}")
            return Response({'error': f'Failed to send invitation email: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'message': 'Invitation sent successfully', 'token': token}, status=status.HTTP_201_CREATED)

class StaffListView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get(self, request):
        staff = CustomUser.objects.filter(restaurant=request.user.restaurant).exclude(role='SUPER_ADMIN')
        serializer = CustomUserSerializer(staff, many=True)
        return Response(serializer.data)

class StaffDetailView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get(self, request, pk):
        staff_member = get_object_or_404(CustomUser, pk=pk, restaurant=request.user.restaurant)
        serializer = CustomUserSerializer(staff_member)
        return Response(serializer.data)

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

    def get(self, request, pk):
        restaurant = get_object_or_404(Restaurant, pk=pk)
        serializer = RestaurantSerializer(restaurant)
        return Response(serializer.data)

class StaffListAPIView(generics.ListAPIView):
    queryset = CustomUser.objects.filter(is_staff=True).order_by('first_name')
    serializer_class = CustomUserSerializer

class CustomTokenObtainPairView(TokenObtainPairView):
    pass

class CustomTokenRefreshView(TokenRefreshView):
    pass

class RegisterView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        user_serializer = CustomUserSerializer(data=request.data)
        if user_serializer.is_valid():
            user = user_serializer.save()
            user.set_password(request.data['password'])
            user.save()
            # Send verification email, etc.
            return Response(user_serializer.data, status=status.HTTP_201_CREATED)
        return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class VerifyEmailView(APIView):
    permission_classes = [permissions.AllowAny]

    def get(self, request, uidb64, token):
        # Logic to verify email using uidb64 and token
        return Response({'message': 'Email verified successfully'})

class ResendVerificationEmailView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        # Logic to resend verification email
        return Response({'message': 'Verification email sent'})

class PasswordResetRequestView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        email = request.data.get('email')
        user = CustomUser.objects.filter(email=email).first()
        if user:
            token = default_token_generator.make_token(user)
            uid = urlsafe_base64_encode(force_bytes(user.pk))
            reset_link = f"http://localhost:5173/reset-password/{uid}/{token}"
            # Send email with reset_link
            html_message = render_to_string('emails/password_reset.html', {'reset_link': reset_link, 'year': timezone.now().year})
            plain_message = strip_tags(html_message)
            email_subject = "Password Reset Request"
            to_email = email
            email_message = EmailMultiAlternatives(email_subject, plain_message, settings.DEFAULT_FROM_EMAIL, [to_email])
            email_message.attach_alternative(html_message, "text/html")
            email_message.send()
        return Response({'message': 'Password reset email sent if user exists.'})

class PasswordResetConfirmView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request, uidb64, token):
        # Logic to confirm password reset
        return Response({'message': 'Password has been reset.'})

class RestaurantUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def put(self, request, pk):
        restaurant = get_object_or_404(Restaurant, pk=pk)
        self.check_object_permissions(request, restaurant)
        serializer = RestaurantSerializer(restaurant, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class StaffInvitationCreateView(APIView):
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

        invite_link = f"http://localhost:5173/accept-invitation?token={token}"
        print(f"Staff Invitation Link for {email}: {invite_link}")

        import logging
        logger = logging.getLogger(__name__)
        logger.info(f"Email Config - Backend: {settings.EMAIL_BACKEND}")
        logger.info(f"Email Config - Host: {settings.EMAIL_HOST}:{settings.EMAIL_PORT}")
        logger.info(f"Email Config - TLS: {settings.EMAIL_USE_TLS}")
        logger.info(f"Email Config - From: {settings.DEFAULT_FROM_EMAIL}")

        try:
            subject = "You're invited to join Mizan AI"
            message = f"Hi,\n\nYou have been invited to join {request.user.restaurant.name} on Mizan AI.\n\nPlease follow the link below to accept the invitation and set up your account:\n\n{invite_link}\n\nThis invitation expires in 7 days.\n\nBest regards,\nMizan AI Team"
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [email],
                fail_silently=False,
            )
            print(f"Invitation email sent successfully to {email}")
        except Exception as e:
            print(f"Error sending invitation email to {email}: {str(e)}")
            return Response({'error': f'Failed to send invitation email: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        return Response({'message': 'Invitation sent successfully', 'token': token}, status=status.HTTP_201_CREATED)


class StaffInvitationAcceptView(APIView):
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        token = request.data.get('token')
        password = request.data.get('password')
        first_name = request.data.get('first_name')
        last_name = request.data.get('last_name')
        pin_code = request.data.get('pin_code')

        if not all([token, password, first_name, last_name]):
            return Response({'error': 'Missing required fields.'}, status=status.HTTP_400_BAD_REQUEST)

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

            user = CustomUser.objects.create_user(
                email=invitation.email,
                first_name=first_name,
                last_name=last_name,
                role=invitation.role,
                restaurant=invitation.restaurant,
                password=password,
                is_verified=True
            )
            if pin_code:
                user.set_pin(pin_code)
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
            

class StaffInvitationListView(generics.ListAPIView):
    serializer_class = StaffInvitationSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return StaffInvitation.objects.filter(restaurant=self.request.user.restaurant).order_by('-created_at')

class StaffProfileUpdateView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, pk):
        staff_profile = get_object_or_404(StaffProfile, user__pk=pk)
        self.check_object_permissions(request, staff_profile)

        serializer = StaffProfileSerializer(staff_profile, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)