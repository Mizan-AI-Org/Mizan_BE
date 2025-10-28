from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import get_object_or_404
from .serializers import CustomUserSerializer, RestaurantSerializer, StaffInvitationSerializer
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, StaffInvitation
from django.utils import timezone
from django.core.files.base import ContentFile
import base64
import os
from django.conf import settings
from django.contrib.auth.models import UserManager
# New imports for invitation
from django.utils.crypto import get_random_string
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

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
            'user': UserSerializer(user).data
        })
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def user_profile(request):
    serializer = UserSerializer(request.user)
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

        invite_link = f"http://localhost:8081/accept-invitation?token={token}"
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
        errors = {}
        if not token:
            errors['token'] = 'This field is required.'
        if not first_name:
            errors['first_name'] = 'This field is required.'
        if not last_name:
            errors['last_name'] = 'This field is required.'
        if not pin_code:
            errors['pin_code'] = 'This field is required.' # <-- Changed from 'password'

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