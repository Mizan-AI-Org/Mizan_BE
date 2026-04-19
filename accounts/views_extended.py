from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.utils import timezone
from datetime import timedelta
import requests
import secrets
from django.conf import settings
from django.utils.http import urlencode
from django.utils.dateparse import parse_datetime, parse_date
from django.shortcuts import redirect
from django.core.signing import TimestampSigner, BadSignature, SignatureExpired
import base64
import json
from .models import EatNowReservation, POSIntegration, AIAssistantConfig, Restaurant, StaffProfile, CustomUser
from .custom_staff_roles import normalize_custom_staff_roles_payload
from .eatnow_client import discover as eatnow_discover, list_reservations as eatnow_list_reservations, test_connection as eatnow_test
from .eatnow_reservation_import import upsert_from_concierge_flat
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

            # Expose Custom API config (URL only, not key) for frontend pre-fill
            if restaurant.pos_provider == 'CUSTOM':
                root = restaurant.get_pos_oauth() or {}
                custom_cfg = root.get('custom') or {}
                data['pos_custom_api_url'] = custom_cfg.get('api_url', '')
                data['pos_custom_api_key_set'] = bool(custom_cfg.get('api_key'))

            if restaurant.pos_provider == 'LIGHTSPEED':
                root = restaurant.get_pos_oauth() or {}
                ls = root.get('lightspeed') or {}
                data['lightspeed_line'] = ls.get('line') or 'RESTAURANT_K'
                data['lightspeed_domain_prefix'] = ls.get('domain_prefix') or ''

            gs = restaurant.general_settings or {}
            rsv = gs.get('reservation') or {}
            data['reservation_provider'] = rsv.get('provider') or 'NONE'
            data['reservation_widget_url'] = rsv.get('widget_url') or ''
            data['reservation_display_name'] = rsv.get('display_name') or ''
            data['eatnow_group_id'] = rsv.get('eatnow_group_id') or ''
            data['eatnow_restaurant_id'] = rsv.get('eatnow_restaurant_id') or ''
            data['eatnow_api_base'] = rsv.get('eatnow_api_base') or ''
            sec = restaurant.get_reservation_oauth() or {}
            data['eatnow_api_key_set'] = bool((sec.get('eatnow') or {}).get('api_key'))
            data['eatnow_webhook_secret_set'] = bool((sec.get('eatnow') or {}).get('webhook_secret'))
            data['eatnow_webhook_url'] = request.build_absolute_uri('/api/webhooks/eatnow/')
            data['incident_category_assignees'] = (gs.get('incident_category_assignees') or {})
            data['business_vertical'] = (gs.get('business_vertical') or 'RESTAURANT')
            data['custom_staff_roles'] = gs.get('custom_staff_roles') or []

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
        if any(
            k in payload
            for k in (
                'pos_provider',
                'pos_merchant_id',
                'pos_api_key',
                'pos_custom_api_url',
                'pos_location_id',
                'lightspeed_line',
                'lightspeed_domain_prefix',
            )
        ):
            new_provider = payload.get('pos_provider', restaurant.pos_provider)
            restaurant.pos_provider = new_provider
            restaurant.pos_merchant_id = payload.get('pos_merchant_id', restaurant.pos_merchant_id)
            restaurant.pos_api_key = payload.get('pos_api_key', restaurant.pos_api_key)
            if 'pos_location_id' in payload:
                pl = payload.get('pos_location_id')
                restaurant.pos_location_id = (pl or '').strip() or None

            if new_provider == 'CUSTOM':
                custom_url = payload.get('pos_custom_api_url', '').strip()
                custom_key = payload.get('pos_custom_api_key', '').strip()
                root = restaurant.get_pos_oauth() or {}
                custom_cfg = root.get('custom') or {}
                if custom_url:
                    custom_cfg['api_url'] = custom_url
                if custom_key:
                    custom_cfg['api_key'] = custom_key
                root['custom'] = custom_cfg
                restaurant.set_pos_oauth(root)
                restaurant.pos_is_connected = False
            elif new_provider == 'LIGHTSPEED':
                root = restaurant.get_pos_oauth() or {}
                ls = dict(root.get('lightspeed') or {})
                if 'lightspeed_line' in payload:
                    v = (payload.get('lightspeed_line') or 'RESTAURANT_K').strip().upper()
                    ls['line'] = v if v in ('RESTAURANT_K', 'RETAIL_X') else 'RESTAURANT_K'
                if 'lightspeed_domain_prefix' in payload:
                    ls['domain_prefix'] = (payload.get('lightspeed_domain_prefix') or '').strip()
                root['lightspeed'] = ls
                restaurant.set_pos_oauth(root)
                restaurant.pos_is_connected = False

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

        # Incident routing: category -> user id (CustomUser in this restaurant), in general_settings JSON
        if 'incident_category_assignees' in payload:
            raw = payload.get('incident_category_assignees')
            if raw is not None and not isinstance(raw, dict):
                return Response(
                    {'detail': 'incident_category_assignees must be an object'},
                    status=status.HTTP_400_BAD_REQUEST,
                )
            inst = serializer.instance
            gs = dict(inst.general_settings or {})
            if raw is None:
                gs.pop('incident_category_assignees', None)
            else:
                cleaned = {}
                for cat, uid in raw.items():
                    if not isinstance(cat, str) or len(cat) > 100:
                        continue
                    if uid is None or uid == '':
                        continue
                    try:
                        u = CustomUser.objects.get(id=uid, restaurant=restaurant)
                    except (CustomUser.DoesNotExist, ValueError, TypeError):
                        return Response(
                            {'detail': f'Invalid assignee for category "{cat}"'},
                            status=status.HTTP_400_BAD_REQUEST,
                        )
                    cleaned[cat] = str(u.id)
                gs['incident_category_assignees'] = cleaned
            inst.general_settings = gs
            inst.save(update_fields=['general_settings'])

        # Business vertical (restaurant vs retail, etc.) — drives staff invite role groupings on the frontend
        if 'business_vertical' in payload:
            bv_raw = payload.get('business_vertical')
            inst = serializer.instance
            gs = dict(inst.general_settings or {})
            if bv_raw is None or bv_raw == '':
                gs.pop('business_vertical', None)
            else:
                bv = str(bv_raw).strip().upper()
                from .business_vertical import ALLOWED_BUSINESS_VERTICALS

                if bv not in ALLOWED_BUSINESS_VERTICALS:
                    allowed = ', '.join(sorted(ALLOWED_BUSINESS_VERTICALS))
                    return Response(
                        {'detail': f'business_vertical must be one of: {allowed}'},
                        status=status.HTTP_400_BAD_REQUEST,
                    )
                gs['business_vertical'] = bv
            inst.general_settings = gs
            inst.save(update_fields=['general_settings'])

        # Custom staff role titles (any vertical) — list of { id, name }
        if 'custom_staff_roles' in payload:
            raw = payload.get('custom_staff_roles')
            inst = serializer.instance
            gs = dict(inst.general_settings or {})
            if raw is None:
                gs.pop('custom_staff_roles', None)
            else:
                try:
                    gs['custom_staff_roles'] = normalize_custom_staff_roles_payload(raw)
                except ValueError as e:
                    return Response({'detail': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            inst.general_settings = gs
            inst.save(update_fields=['general_settings'])

        # Reservation (Eat Now / Eat App) — stored in general_settings + encrypted reservation_oauth_data
        res_keys = (
            'reservation_provider',
            'reservation_widget_url',
            'reservation_display_name',
            'eatnow_group_id',
            'eatnow_restaurant_id',
            'eatnow_api_base',
            'eatnow_api_key',
            'eatnow_webhook_secret',
        )
        if payload.get('reservation_disconnect') is True:
            inst = serializer.instance
            gs = dict(inst.general_settings or {})
            res_cfg = dict(gs.get('reservation') or {})
            res_cfg['provider'] = 'NONE'
            res_cfg['eatnow_group_id'] = ''
            res_cfg['eatnow_restaurant_id'] = ''
            res_cfg['eatnow_api_base'] = ''
            gs['reservation'] = res_cfg
            inst.general_settings = gs
            sec = dict(inst.get_reservation_oauth() or {})
            sec.pop('eatnow', None)
            inst.set_reservation_oauth(sec)
            inst.save(update_fields=['general_settings', 'reservation_oauth_data'])
        elif any(k in payload for k in res_keys):
            inst = serializer.instance
            gs = dict(inst.general_settings or {})
            res_cfg = dict(gs.get('reservation') or {})
            if 'reservation_provider' in payload:
                res_cfg['provider'] = (payload.get('reservation_provider') or 'NONE').strip().upper()
            if 'reservation_widget_url' in payload:
                res_cfg['widget_url'] = (payload.get('reservation_widget_url') or '').strip()
            if 'reservation_display_name' in payload:
                res_cfg['display_name'] = (payload.get('reservation_display_name') or '').strip()
            if 'eatnow_group_id' in payload:
                res_cfg['eatnow_group_id'] = (payload.get('eatnow_group_id') or '').strip()
            if 'eatnow_restaurant_id' in payload:
                res_cfg['eatnow_restaurant_id'] = (payload.get('eatnow_restaurant_id') or '').strip()
            if 'eatnow_api_base' in payload:
                res_cfg['eatnow_api_base'] = (payload.get('eatnow_api_base') or '').strip()
            gs['reservation'] = res_cfg
            inst.general_settings = gs
            update_fields = ['general_settings']
            if 'eatnow_api_key' in payload and (payload.get('eatnow_api_key') or '').strip():
                sec = dict(inst.get_reservation_oauth() or {})
                en = dict(sec.get('eatnow') or {})
                en['api_key'] = payload['eatnow_api_key'].strip()
                sec['eatnow'] = en
                inst.set_reservation_oauth(sec)
                update_fields.append('reservation_oauth_data')
            if 'eatnow_webhook_secret' in payload and (payload.get('eatnow_webhook_secret') or '').strip():
                sec = dict(inst.get_reservation_oauth() or {})
                en = dict(sec.get('eatnow') or {})
                en['webhook_secret'] = str(payload['eatnow_webhook_secret']).strip()
                sec['eatnow'] = en
                inst.set_reservation_oauth(sec)
                if 'reservation_oauth_data' not in update_fields:
                    update_fields.append('reservation_oauth_data')
            inst.save(update_fields=update_fields)

        if payload.get('pos_disconnect') is True:
            inst = serializer.instance
            if inst.pos_provider == 'SQUARE':
                sq = inst.get_square_oauth() or {}
                token = sq.get('access_token') or inst.pos_api_key or ''
                base = (
                    'https://connect.squareup.com'
                    if settings.SQUARE_ENV == 'production'
                    else 'https://connect.squareupsandbox.com'
                )
                try:
                    if token and getattr(settings, 'SQUARE_APPLICATION_ID', None):
                        requests.post(
                            f'{base}/oauth2/revoke',
                            json={
                                'client_id': settings.SQUARE_APPLICATION_ID,
                                'access_token': token,
                            },
                            timeout=10,
                        )
                except Exception:
                    pass
            inst.pos_provider = 'NONE'
            inst.pos_merchant_id = ''
            inst.pos_api_key = ''
            inst.pos_location_id = None
            inst.pos_is_connected = False
            inst.pos_token_expires_at = None
            inst.set_pos_oauth({})
            inst.save()
            try:
                pos_integration, _ = POSIntegration.objects.get_or_create(restaurant=inst)
                pos_integration.sync_status = 'DISCONNECTED'
                pos_integration.save(update_fields=['sync_status'])
            except Exception:
                pass

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

        inst = serializer.instance
        gs = inst.general_settings or {}
        rsv = gs.get('reservation') or {}
        out['reservation_provider'] = rsv.get('provider') or 'NONE'
        out['reservation_widget_url'] = rsv.get('widget_url') or ''
        out['reservation_display_name'] = rsv.get('display_name') or ''
        out['eatnow_group_id'] = rsv.get('eatnow_group_id') or ''
        out['eatnow_restaurant_id'] = rsv.get('eatnow_restaurant_id') or ''
        out['eatnow_api_base'] = rsv.get('eatnow_api_base') or ''
        sec = inst.get_reservation_oauth() or {}
        out['eatnow_api_key_set'] = bool((sec.get('eatnow') or {}).get('api_key'))
        out['eatnow_webhook_secret_set'] = bool((sec.get('eatnow') or {}).get('webhook_secret'))
        out['eatnow_webhook_url'] = request.build_absolute_uri('/api/webhooks/eatnow/')
        out['incident_category_assignees'] = (gs.get('incident_category_assignees') or {})
        out['business_vertical'] = (gs.get('business_vertical') or 'RESTAURANT')

        if inst.pos_provider == 'CUSTOM':
            root = inst.get_pos_oauth() or {}
            custom_cfg = root.get('custom') or {}
            out['pos_custom_api_url'] = custom_cfg.get('api_url', '')
            out['pos_custom_api_key_set'] = bool(custom_cfg.get('api_key'))
        if inst.pos_provider == 'LIGHTSPEED':
            root = inst.get_pos_oauth() or {}
            ls = root.get('lightspeed') or {}
            out['lightspeed_line'] = ls.get('line') or 'RESTAURANT_K'
            out['lightspeed_domain_prefix'] = ls.get('domain_prefix') or ''

        return Response(out)

    def _eatnow_credentials(self, restaurant):
        gs = restaurant.general_settings or {}
        rsv = gs.get('reservation') or {}
        sec = restaurant.get_reservation_oauth() or {}
        en = sec.get('eatnow') or {}
        return {
            'api_key': (en.get('api_key') or '').strip(),
            'restaurant_id': (rsv.get('eatnow_restaurant_id') or '').strip(),
            'api_base': (rsv.get('eatnow_api_base') or '').strip() or None,
        }

    @staticmethod
    def _serialize_eatnow_reservation_row(r: EatNowReservation) -> dict:
        start_time = None
        if r.reservation_date:
            start_time = r.reservation_date.isoformat()
            if r.reservation_time:
                start_time = f"{start_time}T{r.reservation_time}"
        return {
            'id': r.external_id,
            'start_time': start_time,
            'covers': r.group_size,
            'status': r.status,
            'guest_name': r.guest_name or None,
            'phone': r.phone or None,
            'email': r.email or None,
            'notes': r.notes or None,
        }

    @action(detail=False, methods=['get'], url_path='reservations/eatnow')
    def reservations_eatnow(self, request):
        """List reservations synced from Eat Now webhooks for a date range (local DB)."""
        if not request.user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        restaurant = request.user.restaurant
        gs = restaurant.general_settings or {}
        rsv = gs.get('reservation') or {}
        if (rsv.get('provider') or '').upper() != 'EATAPP':
            return Response(
                {'success': False, 'error': 'Reservation provider is not Eat Now (EATAPP). Configure it in Settings.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cred = self._eatnow_credentials(restaurant)
        if not cred['restaurant_id']:
            return Response(
                {
                    'success': False,
                    'error': 'Eat Now restaurant ID is required. Paste it from Eat Now (same value as in webhook payload restaurant_id).',
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        start_s = request.query_params.get('start_date')
        end_s = request.query_params.get('end_date')
        today = timezone.now().date()
        start_d = parse_date(start_s) if start_s else today
        end_d = parse_date(end_s) if end_s else today + timedelta(days=14)
        if not start_d:
            start_d = today
        if not end_d:
            end_d = start_d
        qs = (
            EatNowReservation.objects.filter(
                restaurant=restaurant,
                is_deleted=False,
                reservation_date__gte=start_d,
                reservation_date__lte=end_d,
            )
            .order_by('reservation_date', 'reservation_time', 'guest_name')
        )
        rows = [self._serialize_eatnow_reservation_row(r) for r in qs]
        return Response({'success': True, 'reservations': rows, 'count': len(rows)})

    @action(detail=False, methods=['post'], url_path='reservations/eatnow/sync')
    def reservations_eatnow_sync(self, request):
        """
        Backfill / merge from Eat App Concierge API into EatNowReservation.
        Webhooks do not send historical data; this uses the partner API when an API key is saved.
        """
        if not request.user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        restaurant = request.user.restaurant
        gs = restaurant.general_settings or {}
        rsv = gs.get('reservation') or {}
        if (rsv.get('provider') or '').upper() != 'EATAPP':
            return Response(
                {'success': False, 'error': 'Reservation provider is not Eat Now (EATAPP).'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        cred = self._eatnow_credentials(restaurant)
        if not cred['restaurant_id']:
            return Response(
                {'success': False, 'error': 'Eat Now restaurant ID is required.'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not cred['api_key']:
            return Response(
                {
                    'success': False,
                    'error': (
                        'Save an Eat App Concierge API key in Settings → Integrations (optional legacy section) '
                        'to import past reservations. New bookings still arrive via webhooks.'
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        body = request.data or {}
        start_s = body.get('start_date') or request.query_params.get('start_date')
        end_s = body.get('end_date') or request.query_params.get('end_date')
        today = timezone.now().date()
        start_d = parse_date(start_s) if start_s else today - timedelta(days=365)
        end_d = parse_date(end_s) if end_s else today + timedelta(days=120)
        if not start_d:
            start_d = today - timedelta(days=365)
        if not end_d:
            end_d = start_d
        if end_d < start_d:
            start_d, end_d = end_d, start_d
        span = (end_d - start_d).days
        if span > 400:
            return Response(
                {'success': False, 'error': 'Date range too large (max 400 days). Narrow start_date and end_date.'},
                status=status.HTTP_400_BAD_REQUEST,
            )

        result = eatnow_list_reservations(
            cred['api_key'],
            cred['restaurant_id'],
            start_d,
            end_d,
            api_base=cred['api_base'],
        )
        if not result.get('success'):
            return Response(result, status=status.HTTP_502_BAD_GATEWAY)

        imported = 0
        for flat in result.get('reservations') or []:
            if upsert_from_concierge_flat(restaurant, flat):
                imported += 1
        return Response(
            {
                'success': True,
                'imported': imported,
                'api_count': result.get('count', imported),
                'start_date': start_d.isoformat(),
                'end_date': end_d.isoformat(),
            }
        )

    @action(detail=False, methods=['post'], url_path='reservations/eatnow/discover')
    def reservations_eatnow_discover(self, request):
        """Return groups and restaurants for the Concierge API (bootstrap IDs)."""
        if not request.user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        body = request.data or {}
        api_key = (body.get('api_key') or '').strip()
        if not api_key:
            cred = self._eatnow_credentials(request.user.restaurant)
            api_key = cred['api_key']
        if not api_key:
            return Response({'success': False, 'error': 'Provide api_key in the body or save it in Settings first.'}, status=status.HTTP_400_BAD_REQUEST)
        api_base = (body.get('eatnow_api_base') or '').strip() or None
        if not api_base:
            api_base = self._eatnow_credentials(request.user.restaurant).get('api_base')
        out = eatnow_discover(api_key, api_base=api_base)
        return Response(out, status=status.HTTP_200_OK if out.get('success') else status.HTTP_502_BAD_GATEWAY)

    @action(detail=False, methods=['post'], url_path='reservations/eatnow/test')
    def reservations_eatnow_test(self, request):
        """Verify Eat Now API key + restaurant ID (fetches today's reservations)."""
        if not request.user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        if not request.user.is_admin_role():
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        restaurant = request.user.restaurant
        body = request.data or {}
        cred = self._eatnow_credentials(restaurant)
        api_key = (body.get('api_key') or '').strip() or cred['api_key']
        restaurant_id = (body.get('eatnow_restaurant_id') or '').strip() or cred['restaurant_id']
        api_base = (body.get('eatnow_api_base') or '').strip() or cred['api_base']
        if not api_key or not restaurant_id:
            return Response(
                {'success': False, 'connected': False, 'error': 'API key and Restaurant ID required'},
                status=status.HTTP_400_BAD_REQUEST,
            )
        result = eatnow_test(api_key, restaurant_id, api_base=api_base)
        ok = bool(result.get('success'))
        return Response(
            {'success': ok, 'connected': ok, 'message': result.get('error') or 'OK', 'sample_count': result.get('count', 0)},
            status=status.HTTP_200_OK if ok else status.HTTP_502_BAD_GATEWAY,
        )
    
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
            # Legacy single-location endpoint — forwards writes to the tenant's
            # PRIMARY BusinessLocation. The location model's save() mirrors the
            # fields back onto Restaurant.* so old readers (reports, agent tools)
            # still see current values.
            latitude = request.data.get('latitude')
            longitude = request.data.get('longitude')
            radius = request.data.get('radius', 100)
            geofence_enabled = request.data.get('geofence_enabled', True)
            geofence_polygon = request.data.get('geofence_polygon', [])

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

            from .models import BusinessLocation

            primary = BusinessLocation.objects.filter(
                restaurant=restaurant, is_primary=True
            ).first()
            if primary is None:
                # First-ever configuration for this tenant (migration missed
                # them, or a freshly created workspace): create the primary
                # row from the payload.
                primary = BusinessLocation.objects.create(
                    restaurant=restaurant,
                    name=(restaurant.name or 'Main') + ' - Main',
                    address=restaurant.address or '',
                    latitude=latitude,
                    longitude=longitude,
                    radius=radius,
                    geofence_enabled=geofence_enabled,
                    geofence_polygon=geofence_polygon or [],
                    is_primary=True,
                    is_active=True,
                )
            else:
                primary.latitude = latitude
                primary.longitude = longitude
                primary.radius = radius
                primary.geofence_enabled = geofence_enabled
                primary.geofence_polygon = geofence_polygon or []
                primary.save()

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
        data = request.data or {}
        pos_api_key = data.get('pos_api_key')
        pos_merchant_id = data.get('pos_merchant_id')
        
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

            elif restaurant.pos_provider == 'CUSTOM':
                root = restaurant.get_pos_oauth() or {}
                custom_cfg = root.get('custom') or {}
                api_url = (custom_cfg.get('api_url') or '').strip()
                api_key = (custom_cfg.get('api_key') or '').strip()
                if not api_url:
                    return Response({
                        'connected': False,
                        'provider': 'CUSTOM',
                        'message': 'Custom API URL is not configured. Save it first.',
                    })
                headers = {}
                if api_key:
                    headers['Authorization'] = f'Bearer {api_key}'
                test_resp = requests.get(api_url, headers=headers, timeout=10)
                connected = test_resp.status_code == 200

            elif restaurant.pos_provider in ('TOAST', 'CLOVER'):
                return Response({
                    'connected': False,
                    'provider': restaurant.pos_provider,
                    'message': 'Coming soon'
                })
            elif restaurant.pos_provider == 'LIGHTSPEED':
                api_key = (pos_api_key or restaurant.pos_api_key or '').strip()
                bl_id = (pos_merchant_id or restaurant.pos_merchant_id or '').strip()
                root = restaurant.get_pos_oauth() or {}
                ls = root.get('lightspeed') or {}
                line = (ls.get('line') or 'RESTAURANT_K').strip().upper()
                if line not in ('RESTAURANT_K', 'RETAIL_X'):
                    line = 'RESTAURANT_K'
                domain = (ls.get('domain_prefix') or '').strip()
                if not api_key:
                    return Response({
                        'connected': False,
                        'provider': 'LIGHTSPEED',
                        'message': 'Lightspeed API key is not configured. Save your access token in Settings.',
                    })
                if line == 'RETAIL_X':
                    if not domain:
                        return Response({
                            'connected': False,
                            'provider': 'LIGHTSPEED',
                            'message': 'Retail X-Series: enter your domain prefix (the part before .retail.lightspeed.app).',
                        })
                    try:
                        from datetime import timedelta
                        from django.utils import timezone as dj_tz

                        version = getattr(settings, 'LIGHTSPEED_RETAIL_API_VERSION', '2026-01')
                        url = f'https://{domain}.retail.lightspeed.app/api/{version}/search'
                        headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
                        day = (dj_tz.now().date() - timedelta(days=1)).isoformat()
                        params = {
                            'type': 'sales',
                            'date_from': day,
                            'date_to': day,
                            'page_size': 1,
                            'offset': 0,
                        }
                        outlet = (restaurant.pos_location_id or '').strip()
                        if outlet:
                            params['outlet_id'] = outlet
                        resp = requests.get(url, headers=headers, params=params, timeout=15)
                        connected = resp.status_code in (200, 204)
                        if resp.status_code == 401:
                            connected = False
                        restaurant.pos_is_connected = connected
                        if api_key:
                            restaurant.pos_api_key = api_key
                        restaurant.save(update_fields=['pos_is_connected', 'pos_api_key'])
                        msg = 'Lightspeed Retail (X-Series) connected'
                        if not connected:
                            try:
                                body = resp.json() if resp.content else {}
                                msg = body.get('error') or body.get('message') or f'API HTTP {resp.status_code}'
                            except Exception:
                                msg = f'API HTTP {resp.status_code}'
                        return Response({
                            'connected': connected,
                            'provider': 'LIGHTSPEED',
                            'lightspeed_line': 'RETAIL_X',
                            'status_code': resp.status_code,
                            'message': msg,
                        })
                    except Exception as exc:
                        return Response({
                            'connected': False,
                            'provider': 'LIGHTSPEED',
                            'message': str(exc),
                        }, status=status.HTTP_400_BAD_REQUEST)

                # Restaurant K-Series (default)
                api_base = getattr(settings, 'LIGHTSPEED_API_BASE', '').rstrip('/') or 'https://api.trial.lsk.lightspeed.app'
                if not bl_id:
                    return Response({
                        'connected': False,
                        'provider': 'LIGHTSPEED',
                        'message': 'Business Location ID is required for Restaurant (K-Series). Enter it in Settings.',
                    })
                try:
                    headers = {'Authorization': f'Bearer {api_key}', 'Accept': 'application/json'}
                    from datetime import timedelta
                    from django.utils import timezone as dj_tz
                    today = (dj_tz.now().date() - timedelta(days=1)).isoformat()
                    from_str = f'{today}T00:00:00Z'
                    to_str = f'{today}T23:59:59Z'
                    url = f'{api_base}/f/v2/business-location/{bl_id}/sales'
                    resp = requests.get(url, headers=headers, params={'from': from_str, 'to': to_str, 'include': 'payments'}, timeout=15)
                    connected = resp.status_code in (200, 204)
                    if resp.status_code == 401:
                        connected = False
                    restaurant.pos_is_connected = connected
                    if api_key and api_key != getattr(restaurant, 'pos_api_key', ''):
                        restaurant.pos_api_key = api_key
                    if bl_id:
                        restaurant.pos_merchant_id = bl_id
                    restaurant.save(update_fields=['pos_is_connected', 'pos_api_key', 'pos_merchant_id'])
                    return Response({
                        'connected': connected,
                        'provider': 'LIGHTSPEED',
                        'lightspeed_line': 'RESTAURANT_K',
                        'status_code': resp.status_code,
                        'message': 'Lightspeed connected' if connected else (resp.json().get('error', 'API error') if resp.content else 'Lightspeed API not reachable'),
                    })
                except Exception as exc:
                    return Response({
                        'connected': False,
                        'provider': 'LIGHTSPEED',
                        'message': str(exc),
                    }, status=status.HTTP_400_BAD_REQUEST)
            
            else:
                connected = False
            
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
            return Response(
                {'error': 'Square OAuth is not configured on this server. Please add SQUARE_APPLICATION_ID and SQUARE_REDIRECT_URI to your environment.',
                 'detail': 'Square OAuth credentials are not configured. Contact your administrator.'},
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

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
