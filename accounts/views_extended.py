from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
import requests
from .models import POSIntegration, AIAssistantConfig, Restaurant, StaffProfile
from .serializers_extended import (
    POSIntegrationSerializer,
    AIAssistantConfigSerializer,
    RestaurantSettingsSerializer,
    RestaurantGeolocationSerializer,
    StaffProfileExtendedSerializer
)


class RestaurantSettingsViewSet(viewsets.ViewSet):
    """
    Complete restaurant settings management
    - Geolocation with perimeter
    - POS Integration
    - AI Assistant configuration
    - Notifications
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def my_restaurant(self, request):
        """Get current restaurant settings"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        serializer = RestaurantSettingsSerializer(request.user.restaurant)
        return Response(serializer.data)
    
    @action(detail=False, methods=['put'])
    def update_my_restaurant(self, request):
        """Update restaurant settings"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        restaurant = request.user.restaurant
        serializer = RestaurantSettingsSerializer(restaurant, data=request.data, partial=True)
        
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get', 'post'])
    def geolocation(self, request):
        """Get/update geolocation settings"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        restaurant = request.user.restaurant
        
        if request.method == 'GET':
            serializer = RestaurantGeolocationSerializer(restaurant)
            return Response(serializer.data)
        
        elif request.method == 'POST':
            # Update geolocation
            latitude = request.data.get('latitude')
            longitude = request.data.get('longitude')
            radius = request.data.get('radius', 100)  # Default to 100m (max in 5-100m range)
            geofence_enabled = request.data.get('geofence_enabled', True)
            geofence_polygon = request.data.get('geofence_polygon', [])

            # Permanent lock: once coordinates are set, only SUPER_ADMIN can change them
            if (
                restaurant.latitude is not None and restaurant.longitude is not None
                and request.user.role != 'SUPER_ADMIN'
            ):
                return Response(
                    {
                        'error': 'Location locked',
                        'message': 'Restaurant coordinates are locked. Contact a SUPER_ADMIN to update.'
                    },
                    status=status.HTTP_403_FORBIDDEN
                )

            restaurant.latitude = latitude
            restaurant.longitude = longitude
            restaurant.radius = radius
            restaurant.geofence_enabled = geofence_enabled
            restaurant.geofence_polygon = geofence_polygon
            restaurant.save()

            serializer = RestaurantGeolocationSerializer(restaurant)
            return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def validate_geolocation(self, request):
        """Validate if staff is within geofence"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        restaurant = request.user.restaurant
        staff_latitude = request.data.get('latitude')
        staff_longitude = request.data.get('longitude')
        
        if not all([staff_latitude, staff_longitude, restaurant.latitude, restaurant.longitude]):
            return Response(
                {'error': 'Missing coordinates'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Calculate distance using Haversine formula
        from math import radians, cos, sin, asin, sqrt
        
        lon1, lat1, lon2, lat2 = map(radians, [
            float(restaurant.longitude), float(restaurant.latitude),
            float(staff_longitude), float(staff_latitude)
        ])
        
        dlon = lon2 - lon1
        dlat = lat2 - lat1
        a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
        c = 2 * asin(sqrt(a))
        r = 6371  # km
        distance_km = c * r
        distance_m = distance_km * 1000
        
        is_within = distance_m <= float(restaurant.radius)
        
        return Response({
            'distance_meters': distance_m,
            'radius_meters': float(restaurant.radius),
            'is_within_geofence': is_within,
            'message': 'Staff is within geofence' if is_within else 'Staff is outside geofence'
        })
    
    @action(detail=False, methods=['get', 'post'])
    def pos_integration(self, request):
        """Get/update POS integration settings"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        restaurant = request.user.restaurant
        
        if request.method == 'GET':
            try:
                pos_integration = POSIntegration.objects.get(restaurant=restaurant)
            except POSIntegration.DoesNotExist:
                pos_integration = POSIntegration.objects.create(restaurant=restaurant)
            
            serializer = POSIntegrationSerializer(pos_integration)
            return Response(serializer.data)
        
        elif request.method == 'POST':
            # Update POS settings
            pos_provider = request.data.get('pos_provider')
            pos_merchant_id = request.data.get('pos_merchant_id')
            pos_api_key = request.data.get('pos_api_key')
            
            restaurant.pos_provider = pos_provider
            restaurant.pos_merchant_id = pos_merchant_id
            restaurant.pos_api_key = pos_api_key
            restaurant.save()
            
            return Response({
                'status': 'POS configuration updated',
                'provider': pos_provider,
                'merchant_id': pos_merchant_id
            })
    
    @action(detail=False, methods=['post'])
    def test_pos_connection(self, request):
        """Test POS connection"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        restaurant = request.user.restaurant
        
        if restaurant.pos_provider == 'NONE':
            return Response({
                'connected': False,
                'message': 'POS provider not configured'
            })
        
        # Test connection based on provider
        try:
            if restaurant.pos_provider == 'STRIPE':
                # Test Stripe connection
                headers = {'Authorization': f'Bearer {restaurant.pos_api_key}'}
                response = requests.get('https://api.stripe.com/v1/account', headers=headers)
                connected = response.status_code == 200
            
            elif restaurant.pos_provider == 'SQUARE':
                # Test Square connection
                headers = {'Authorization': f'Bearer {restaurant.pos_api_key}'}
                response = requests.get('https://connect.squareupsandbox.com/v2/locations', headers=headers)
                connected = response.status_code == 200
            
            else:
                # Custom API test
                response = requests.get(restaurant.pos_api_key, timeout=5)
                connected = response.status_code == 200
            
            if connected:
                restaurant.pos_is_connected = True
                restaurant.save()
            
            return Response({
                'connected': connected,
                'provider': restaurant.pos_provider,
                'message': 'Connection successful' if connected else 'Connection failed'
            })
        
        except Exception as e:
            return Response({
                'connected': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get', 'post'])
    def ai_assistant_config(self, request):
        """Get/update AI Assistant configuration"""
        if not request.user.restaurant:
            return Response(
                {'error': 'No restaurant associated'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        restaurant = request.user.restaurant
        
        if request.method == 'GET':
            try:
                ai_config = AIAssistantConfig.objects.get(restaurant=restaurant)
            except AIAssistantConfig.DoesNotExist:
                ai_config = AIAssistantConfig.objects.create(restaurant=restaurant)
            
            serializer = AIAssistantConfigSerializer(ai_config)
            return Response(serializer.data)
        
        elif request.method == 'POST':
            # Update AI config
            enabled = request.data.get('enabled', True)
            ai_provider = request.data.get('ai_provider', 'GROQ')
            features_enabled = request.data.get('features_enabled', {})
            
            ai_config, _ = AIAssistantConfig.objects.get_or_create(restaurant=restaurant)
            ai_config.enabled = enabled
            ai_config.ai_provider = ai_provider
            ai_config.features_enabled = features_enabled
            ai_config.save()
            
            serializer = AIAssistantConfigSerializer(ai_config)
            return Response(serializer.data)


class StaffLocationViewSet(viewsets.ViewSet):
    """Staff location tracking and geofence management"""
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['post'])
    def update_location(self, request):
        """Update staff location"""
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        
        if not latitude or not longitude:
            return Response(
                {'error': 'latitude and longitude required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            profile = request.user.profile
        except:
            profile = StaffProfile.objects.create(user=request.user)
        
        profile.last_location_latitude = latitude
        profile.last_location_longitude = longitude
        profile.last_location_timestamp = timezone.now()
        profile.save()
        
        # Check if within geofence
        restaurant = request.user.restaurant
        if restaurant and restaurant.geofence_enabled:
            from math import radians, cos, sin, asin, sqrt
            
            lon1, lat1, lon2, lat2 = map(radians, [
                float(restaurant.longitude), float(restaurant.latitude),
                float(longitude), float(latitude)
            ])
            
            dlon = lon2 - lon1
            dlat = lat2 - lat1
            a = sin(dlat/2)**2 + cos(lat1) * cos(lat2) * sin(dlon/2)**2
            c = 2 * asin(sqrt(a))
            r = 6371
            distance_m = c * r * 1000
            
            within_geofence = distance_m <= float(restaurant.radius)
            
            return Response({
                'status': 'Location updated',
                'within_geofence': within_geofence,
                'distance_meters': distance_m
            })
        
        return Response({'status': 'Location updated'})
    
    @action(detail=False, methods=['get'])
    def get_location(self, request):
        """Get current user location"""
        try:
            profile = request.user.profile
            return Response({
                'latitude': profile.last_location_latitude,
                'longitude': profile.last_location_longitude,
                'timestamp': profile.last_location_timestamp
            })
        except:
            return Response(
                {'error': 'No location data'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    @action(detail=False, methods=['get'])
    def all_staff_locations(self, request):
        """Get all staff locations in restaurant (admin only)"""
        if request.user.role not in ['ADMIN', 'SUPER_ADMIN']:
            return Response(
                {'error': 'Permission denied'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({'error': 'No restaurant'}, status=status.HTTP_400_BAD_REQUEST)
        
        staff_profiles = StaffProfile.objects.filter(
            user__restaurant=restaurant
        ).exclude(
            last_location_latitude__isnull=True
        )
        
        serializer = StaffProfileExtendedSerializer(staff_profiles, many=True)
        return Response(serializer.data)