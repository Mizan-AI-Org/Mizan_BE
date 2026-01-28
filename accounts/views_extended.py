from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
import requests
import secrets
from django.conf import settings
from django.utils.http import urlencode
from django.utils.dateparse import parse_datetime
from django.shortcuts import redirect
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
import base64
import json
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

    @action(detail=False, methods=['get', 'put'])
    def unified(self, request):
        """Unified settings endpoint (GET/PUT) for admins/managers only"""
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)

        # Restrict to admin roles only
        if not user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        restaurant = user.restaurant

        if request.method == 'GET':
            serializer = RestaurantSettingsSerializer(restaurant, context={'request': request})
            data = serializer.data
            # Include AI config nested (already present) and schema version for optimistic locking
            # Use updated_at timestamp as a simple version marker
            try:
                version = int(restaurant.updated_at.timestamp()) if restaurant.updated_at else 0
            except Exception:
                version = 0
            data['settings_schema_version'] = version
            # Provide legacy alias if frontend expects it
            data['settingsVersion'] = version
            # Provide phone_restaurant alias for compatibility
            data['phone_restaurant'] = data.get('phone')
            return Response(data)

        # PUT
        payload = request.data or {}
        # Optimistic locking: require matching schema version
        client_version = payload.get('settings_schema_version', payload.get('settingsVersion'))
        try:
            current_version = int(restaurant.updated_at.timestamp()) if restaurant.updated_at else 0
        except Exception:
            current_version = 0
        if client_version is not None and int(client_version) != current_version:
            return Response({'detail': 'Settings version conflict'}, status=status.HTTP_409_CONFLICT)

        # Update general settings
        general_fields = {
            'name': payload.get('name', restaurant.name),
            'address': payload.get('address', restaurant.address),
            # Frontend may send phone_restaurant
            'phone': payload.get('phone', payload.get('phone_restaurant', restaurant.phone)),
            'email': payload.get('email', restaurant.email),
            'timezone': payload.get('timezone', restaurant.timezone),
            'currency': payload.get('currency', restaurant.currency),
            'language': payload.get('language', restaurant.language),
            'operating_hours': payload.get('operating_hours', restaurant.operating_hours),
            'automatic_clock_out': payload.get('automatic_clock_out', restaurant.automatic_clock_out),
            'break_duration': payload.get('break_duration', restaurant.break_duration),
            'email_notifications': payload.get('email_notifications', restaurant.email_notifications),
            'push_notifications': payload.get('push_notifications', restaurant.push_notifications),
        }

        old_name = restaurant.name

        # Update POS settings if provided
        if 'pos_provider' in payload or 'pos_merchant_id' in payload or 'pos_api_key' in payload:
            restaurant.pos_provider = payload.get('pos_provider', restaurant.pos_provider)
            restaurant.pos_merchant_id = payload.get('pos_merchant_id', restaurant.pos_merchant_id)
            # Store API key without exposing it in GET response
            restaurant.pos_api_key = payload.get('pos_api_key', restaurant.pos_api_key)

        # Update AI settings if provided
        ai_enabled = payload.get('ai_enabled')
        ai_provider = payload.get('ai_provider')
        ai_features_enabled = payload.get('ai_features_enabled')
        if any(v is not None for v in [ai_enabled, ai_provider, ai_features_enabled]):
            try:
                ai_config = AIAssistantConfig.objects.get(restaurant=restaurant)
            except AIAssistantConfig.DoesNotExist:
                ai_config = AIAssistantConfig.objects.create(restaurant=restaurant)
            if ai_enabled is not None:
                ai_config.enabled = bool(ai_enabled)
            if ai_provider is not None:
                ai_config.ai_provider = ai_provider
            if ai_features_enabled is not None:
                ai_config.features_enabled = ai_features_enabled
            ai_config.save()

        # Save general fields via serializer to enforce validations (e.g., radius rules)
        serializer = RestaurantSettingsSerializer(restaurant, data=general_fields, partial=True, context={'request': request})
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        serializer.save()

        # If name changed, log audit and broadcast like update_my_restaurant
        new_name = serializer.instance.name
        updated_fields = list(payload.keys())
        if 'name' in updated_fields and new_name != old_name:
            try:
                from .models import AuditLog
                from .views import get_client_ip
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

                # Broadcast WS update to restaurant group
                from channels.layers import get_channel_layer
                from asgiref.sync import async_to_sync
                from django.utils import timezone as dj_tz
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
                        'timestamp': dj_tz.now().isoformat(),
                    }
                }
                async_to_sync(channel_layer.group_send)(group_name, event)
            except Exception:
                pass

        # Respond with updated settings including new version
        out = RestaurantSettingsSerializer(serializer.instance, context={'request': request}).data
        try:
            version = int(serializer.instance.updated_at.timestamp()) if serializer.instance.updated_at else 0
        except Exception:
            version = 0
        out['settings_schema_version'] = version
        out['settingsVersion'] = version
        out['phone_restaurant'] = out.get('phone')
        return Response(out)
    
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
        old_name = restaurant.name
        serializer = RestaurantSettingsSerializer(restaurant, data=request.data, partial=True)
        
        if serializer.is_valid():
            serializer.save()

            # If name changed, log audit and broadcast
            new_name = serializer.instance.name
            updated_fields = list(request.data.keys())
            if 'name' in updated_fields and new_name != old_name:
                try:
                    from .models import AuditLog
                    from .views import get_client_ip
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

                    # Broadcast WS update to restaurant group
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
                    pass
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
                return Response({
                    'connected': False,
                    'provider': restaurant.pos_provider,
                    'message': 'Coming soon'
                })
            
            elif restaurant.pos_provider == 'SQUARE':
                # Test Square connection
                token = restaurant.get_square_access_token()
                headers = {'Authorization': f'Bearer {token}'}
                base = 'https://connect.squareup.com' if getattr(settings, 'SQUARE_ENV', 'production') == 'production' else 'https://connect.squareupsandbox.com'
                response = requests.get(f'{base}/v2/locations', headers=headers, timeout=10)
                connected = response.status_code == 200
                if connected:
                    try:
                        data = response.json() or {}
                        locs = data.get('locations') or []
                        if locs and not restaurant.pos_location_id:
                            restaurant.pos_location_id = locs[0].get('id')
                            restaurant.save(update_fields=['pos_location_id'])
                    except Exception:
                        pass

            elif restaurant.pos_provider in ('TOAST', 'LIGHTSPEED', 'CLOVER', 'CUSTOM'):
                return Response({
                    'connected': False,
                    'provider': restaurant.pos_provider,
                    'message': 'Coming soon'
                })
            
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

    # ---------------------------------------------------------------------
    # Square OAuth (production-ready, reusable across tenants)
    # ---------------------------------------------------------------------
    @action(detail=False, methods=['get'], url_path='square/oauth/authorize')
    def square_oauth_authorize(self, request):
        """Return Square OAuth authorization URL for the current restaurant."""
        if not request.user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        if not settings.SQUARE_APPLICATION_ID or not settings.SQUARE_REDIRECT_URI:
            return Response({'error': 'Square OAuth is not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        restaurant = request.user.restaurant
        nonce = secrets.token_urlsafe(24)
        state_payload = {
            "restaurant_id": str(restaurant.id),
            "user_id": str(request.user.id),
            "nonce": nonce,
        }
        signer = TimestampSigner()
        packed = base64.urlsafe_b64encode(json.dumps(state_payload, separators=(",", ":")).encode("utf-8")).decode("utf-8")
        state = signer.sign(packed)
        base = 'https://connect.squareup.com' if settings.SQUARE_ENV == 'production' else 'https://connect.squareupsandbox.com'
        scopes = [s.strip() for s in (settings.SQUARE_SCOPES or '').split(',') if s.strip()]
        params = {
            'client_id': settings.SQUARE_APPLICATION_ID,
            'scope': ' '.join(scopes),
            'session': 'false',
            'state': state,
            'redirect_uri': settings.SQUARE_REDIRECT_URI,
        }
        return Response({'authorization_url': f"{base}/oauth2/authorize?{urlencode(params)}"})

    @action(detail=False, methods=['get'], url_path='square/oauth/callback', permission_classes=[AllowAny])
    def square_oauth_callback(self, request):
        """Handle Square OAuth callback; stores encrypted tokens server-side and redirects to frontend."""
        code = request.query_params.get('code')
        state = request.query_params.get('state')
        error = request.query_params.get('error')
        error_description = request.query_params.get('error_description')

        frontend_base = getattr(settings, 'FRONTEND_URL', '').rstrip('/')
        frontend_target = f"{frontend_base}/dashboard/settings?tab=integrations"

        if error:
            qs = urlencode({'square': 'error', 'message': error_description or error})
            return redirect(f"{frontend_target}&{qs}")

        if not code or not state:
            qs = urlencode({'square': 'error', 'message': 'Missing code/state'})
            return redirect(f"{frontend_target}&{qs}")

        signer = TimestampSigner()
        try:
            packed = signer.unsign(state, max_age=10 * 60)
        except SignatureExpired:
            qs = urlencode({'square': 'error', 'message': 'OAuth state expired'})
            return redirect(f"{frontend_target}&{qs}")
        except BadSignature:
            qs = urlencode({'square': 'error', 'message': 'Invalid OAuth state'})
            return redirect(f"{frontend_target}&{qs}")

        try:
            st = json.loads(base64.urlsafe_b64decode(packed.encode("utf-8")).decode("utf-8"))
        except Exception:
            qs = urlencode({'square': 'error', 'message': 'Invalid OAuth state payload'})
            return redirect(f"{frontend_target}&{qs}")

        restaurant_id = st.get('restaurant_id')
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            qs = urlencode({'square': 'error', 'message': 'Restaurant not found'})
            return redirect(f"{frontend_target}&{qs}")

        base = 'https://connect.squareup.com' if settings.SQUARE_ENV == 'production' else 'https://connect.squareupsandbox.com'
        token_url = f"{base}/oauth2/token"
        payload = {
            'client_id': settings.SQUARE_APPLICATION_ID,
            'client_secret': settings.SQUARE_APPLICATION_SECRET,
            'code': code,
            'grant_type': 'authorization_code',
            'redirect_uri': settings.SQUARE_REDIRECT_URI,
        }
        try:
            resp = requests.post(token_url, json=payload, timeout=15)
            data = resp.json() if resp.content else {}
            if resp.status_code >= 400:
                msg = data.get('message') or data.get('error_description') or 'Token exchange failed'
                qs = urlencode({'square': 'error', 'message': msg})
                return redirect(f"{frontend_target}&{qs}")
        except Exception as e:
            qs = urlencode({'square': 'error', 'message': str(e)})
            return redirect(f"{frontend_target}&{qs}")

        access_token = data.get('access_token') or ''
        refresh_token = data.get('refresh_token') or ''
        merchant_id = data.get('merchant_id') or ''
        expires_at = data.get('expires_at') or None
        expires_dt = parse_datetime(expires_at) if isinstance(expires_at, str) else None

        # Fetch locations to pick a default location_id
        location_id = None
        try:
            loc_resp = requests.get(
                f"{base}/v2/locations",
                headers={'Authorization': f'Bearer {access_token}'},
                timeout=10,
            )
            if loc_resp.status_code == 200:
                locs = (loc_resp.json() or {}).get('locations') or []
                if locs:
                    location_id = locs[0].get('id')
        except Exception:
            location_id = None

        # Store encrypted OAuth payload under pos_oauth_data
        square_payload = {
            'access_token': access_token,
            'refresh_token': refresh_token,
            'merchant_id': merchant_id,
            'expires_at': expires_at,
            'environment': settings.SQUARE_ENV,
            'scopes': (settings.SQUARE_SCOPES or ''),
        }
        restaurant.set_square_oauth(square_payload)
        restaurant.pos_provider = 'SQUARE'
        if merchant_id:
            restaurant.pos_merchant_id = merchant_id
        if location_id:
            restaurant.pos_location_id = location_id
        restaurant.pos_token_expires_at = expires_dt
        restaurant.pos_is_connected = True
        restaurant.save()

        # Ensure POSIntegration exists and mark connected
        try:
            pos_integration, _ = POSIntegration.objects.get_or_create(restaurant=restaurant)
            pos_integration.sync_status = 'CONNECTED'
            pos_integration.last_sync_time = timezone.now()
            pos_integration.save(update_fields=['sync_status', 'last_sync_time'])
        except Exception:
            pass

        qs = urlencode({'square': 'connected'})
        return redirect(f"{frontend_target}&{qs}")

    @action(detail=False, methods=['post'], url_path='square/oauth/disconnect')
    def square_oauth_disconnect(self, request):
        """Revoke Square token (best-effort) and clear stored credentials."""
        if not request.user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        restaurant = request.user.restaurant
        sq = restaurant.get_square_oauth() or {}
        token = sq.get('access_token') or restaurant.pos_api_key or ''
        base = 'https://connect.squareup.com' if settings.SQUARE_ENV == 'production' else 'https://connect.squareupsandbox.com'
        try:
            if token and settings.SQUARE_APPLICATION_ID:
                requests.post(
                    f"{base}/oauth2/revoke",
                    json={'client_id': settings.SQUARE_APPLICATION_ID, 'access_token': token},
                    timeout=10,
                )
        except Exception:
            pass

        restaurant.set_square_oauth({})
        restaurant.pos_is_connected = False
        restaurant.pos_location_id = None
        restaurant.pos_token_expires_at = None
        restaurant.save(update_fields=['pos_oauth_data', 'pos_is_connected', 'pos_location_id', 'pos_token_expires_at'])

        try:
            pos_integration, _ = POSIntegration.objects.get_or_create(restaurant=restaurant)
            pos_integration.sync_status = 'DISCONNECTED'
            pos_integration.save(update_fields=['sync_status'])
        except Exception:
            pass

        return Response({'success': True})
    
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
