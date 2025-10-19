from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework_simplejwt.tokens import RefreshToken
from django.shortcuts import get_object_or_404
from .serializers import UserSerializer, PinLoginSerializer, RestaurantSerializer
from rest_framework.views import APIView
from django.contrib.auth import authenticate
from .models import CustomUser, Restaurant, StaffInvitation
from .serializers import UserSerializer, RestaurantSerializer
from django.utils import timezone

class IsAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role == 'admin'

class IsManagerOrAdmin(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user.role in ['admin', 'manager']

@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def pin_login(request):
    serializer = PinLoginSerializer(data=request.data)
    
    if serializer.is_valid():
        user = serializer.validated_data['user']
        
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
        'geo_fence_radius': restaurant.geo_fence_radius,
        'address': restaurant.address
    })


class RestaurantOwnerSignupView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        # Create restaurant first
        restaurant_serializer = RestaurantSerializer(data=request.data.get('restaurant'))
        if restaurant_serializer.is_valid():
            restaurant = restaurant_serializer.save()
            
            # Create super admin user
            user_data = request.data.get('user')
            user_data['restaurant'] = restaurant.id
            user_data['role'] = 'SUPER_ADMIN'
            user_data['is_verified'] = True # Ensure is_verified is passed
            user_data['password'] = request.data.get('user', {}).get('password') # Pass password directly
            
            user_serializer = UserSerializer(data=user_data)
            if user_serializer.is_valid():
                user = user_serializer.save(restaurant=restaurant)
                
                # Generate tokens
                refresh = RefreshToken.for_user(user)
                
                return Response({
                    'user': user_serializer.data,
                    'restaurant': restaurant_serializer.data,
                    'tokens': {
                        'refresh': str(refresh),
                        'access': str(refresh.access_token),
                    }
                }, status=status.HTTP_201_CREATED)
            
            # If user creation fails, delete the restaurant
            restaurant.delete()
            return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        return Response(restaurant_serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class LoginView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        email = request.data.get('email')
        password = request.data.get('password')
        
        user = authenticate(email=email, password=password)
        
        if user:
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': UserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            })
        
        return Response(
            {'error': 'Invalid credentials'}, 
            status=status.HTTP_401_UNAUTHORIZED
        )

class AcceptInvitationView(APIView):
    permission_classes = [permissions.AllowAny]
    
    def post(self, request):
        token = request.data.get('token')
        
        try:
            invitation = StaffInvitation.objects.get(
                token=token,
                is_accepted=False,
                expires_at__gt=timezone.now()
            )
            
            # Check if user already exists with this email
            if CustomUser.objects.filter(email=invitation.email).exists():
                return Response(
                    {'error': 'User with this email already exists'}, 
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Create user account
            user_data = request.data.get('user')
            user_data.update({
                'email': invitation.email,
                'role': invitation.role,
                'restaurant': invitation.restaurant.id,
                'is_verified': True
            })
            
            user_serializer = UserSerializer(data=user_data)
            if user_serializer.is_valid():
                user = user_serializer.save()
                invitation.is_accepted = True
                invitation.save()
                
                # Generate tokens
                refresh = RefreshToken.for_user(user)
                
                return Response({
                    'user': user_serializer.data,
                    'tokens': {
                        'refresh': str(refresh),
                        'access': str(refresh.access_token),
                    }
                })
            
            return Response(user_serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        except StaffInvitation.DoesNotExist:
            return Response(
                {'error': 'Invalid or expired invitation'}, 
                status=status.HTTP_400_BAD_REQUEST
            )