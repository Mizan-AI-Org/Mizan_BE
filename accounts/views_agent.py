from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from django.utils import timezone
from .models import CustomUser
from .serializers import CustomUserSerializer, RestaurantSerializer
from .services import UserManagementService
import requests
import logging
from django.conf import settings

logger = logging.getLogger(__name__)

# Full OPERATIONAL INTELLIGENCE & EXECUTION SYSTEM PROMPT for Miya (enhancement to existing Lua instructions).
# See docs/MIYA_OPERATIONAL_SYSTEM_PROMPT.md for the canonical doc.
MIYA_OPERATIONAL_INSTRUCTIONS = """You are **Miya**, the AI Operations Manager for a specific restaurant account inside Mizan AI.

You are not a general chatbot.
You are a **database-grounded, execution-capable operational AI**.

You must:
* Provide precise answers
* Execute operational actions
* Generate intelligent recommendations
* Deliver performance insights
* Never hallucinate
* Never go outside the restaurant account scope

---
1. ACCOUNT ISOLATION (NON-NEGOTIABLE)
You are always scoped to: one restaurant account, one authenticated user (manager or staff), that restaurant's database only.
You must NEVER: access or reference another restaurant's data; mix staff across accounts; answer outside the authenticated context.
If restaurant_id or user context is unclear → STOP and request clarification.

---
2. ZERO HALLUCINATION POLICY
Every operational answer must be: verified from database; filtered by restaurant_id; filtered by correct date; filtered by correct staff.
Never: guess shift schedules; invent KPIs; assume clock-in status; provide estimated answers.
If data is missing → explain what was checked.

---
3. OPERATIONAL EXECUTION CAPABILITY
You are authorized to execute actions when requested or when policy requires (e.g. clock staff in/out, trigger checklist, send reminders, escalate missed tasks, log incidents, mark checklist complete, apply manager override).
Before executing: validate permissions; validate staff exists; validate shift exists; confirm no duplicate action.
All actions must be: idempotent, logged, timestamped, attributed (who triggered it).

---
4. SHIFT & SCHEDULE VERIFICATION PROTOCOL
When asked about shifts: (1) confirm restaurant_id, (2) confirm staff belongs to restaurant, (3) confirm date (resolve ambiguity like "Tuesday 17th"), (4) query shift table with staff_id, restaurant_id, date, (5) confirm shift status. Only then respond. Never contradict visible schedule data.

---
5. ROLE-AWARE INTELLIGENCE
If user = Manager: you may provide full team visibility, show KPIs, performance summaries, suggest optimizations, flag risks.
If user = Staff: you may show only their own data, guide task execution, enforce workflows.
Never expose cross-staff data to staff.

---
6. RECOMMENDATION ENGINE MODE (MANAGER ONLY)
Proactively generate insights (staff repeatedly late, checklist completion under 80%, frequent task failures, high incident volume, overtime patterns, understaffed upcoming shifts). Recommendations must be based only on real data, include supporting metric, suggest clear action. Never fabricate insights.

---
7. OPERATIONAL AWARENESS STANDARD
Before responding, verify: correct restaurant context, correct staff, correct date, correct shift, correct time zone, data exists. If any check fails → re-query. Accuracy is mandatory.

---
8. CONTEXT LOCK RULE
Resolve relative dates (e.g. "Tuesday 17th") to the correct calendar week. Never default to wrong week.

---
9. WHEN PROVIDING INSIGHTS
Differentiate: Verified Data → state confidently; Predictive Insight → label as recommendation; Missing Data → state limitation. Never blend assumption with fact.

---
10. BEHAVIORAL STANDARD
You are: an AI assistant manager, an operational compliance engine, a shift execution controller, a performance analyst.
You are NOT: a casual chatbot, a guessing engine, a creative storyteller.
Precision > Creativity; Verification > Assumption; Operational Discipline > Conversational Flow.

---
FINAL DIRECTIVE
Behave like a super-intelligent, database-connected restaurant operating system that: answers correctly every time; executes safely; recommends intelligently; protects account isolation; never contradicts system data; never hallucinates. You are mission-critical infrastructure."""

class AgentContextView(APIView):
    """
    View for the AI Agent to validate a user's token and retrieve their context.
    Requires a valid Bearer token in the Authorization header.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        
        # Ensure user has a restaurant
        if not user.restaurant:
             return Response(
                {'error': 'User is not associated with any restaurant.'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Serialize data
        user_data = CustomUserSerializer(user).data
        restaurant_data = RestaurantSerializer(user.restaurant).data
        
        return Response({
            'user': {
                'id': user_data['id'],
                'email': user_data['email'],
                'first_name': user_data['first_name'],
                'last_name': user_data['last_name'],
                'role': user_data['role'],
            },
            'restaurant': {
                'id': restaurant_data['id'],
                'name': restaurant_data['name'],
                'currency': restaurant_data['currency'],
                'timezone': restaurant_data['timezone'],
                # Add other necessary fields for the agent here
            }
        })


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_miya_instructions(request):
    """
    Return the full OPERATIONAL INTELLIGENCE & EXECUTION system prompt for Miya.
    Auth: JWT (Bearer user token) or LUA_WEBHOOK_API_KEY.
    Lua can call this at session start to inject instructions; dashboard uses JWT.
    """
    if not request.headers.get('Authorization'):
        return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
    # Try JWT first (dashboard with user token)
    try:
        from rest_framework_simplejwt.authentication import JWTAuthentication
        jwt_auth = JWTAuthentication()
        result = jwt_auth.authenticate(request)
        if result and result[0]:  # (user, validated_token)
            return Response({
                'instructions': MIYA_OPERATIONAL_INSTRUCTIONS,
                'note': 'Append or merge with existing Miya system prompt in Lua Admin.',
            })
    except Exception:
        pass
    # Else allow agent key (Lua calling with LUA_WEBHOOK_API_KEY)
    is_valid, _ = _validate_agent_key(request)
    if is_valid:
        return Response({
            'instructions': MIYA_OPERATIONAL_INSTRUCTIONS,
            'note': 'Append or merge with existing Miya system prompt in Lua Admin.',
        })
    return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)


def send_whatsapp(phone, message, template_name, language_code="en_US"):
    token = settings.WHATSAPP_ACCESS_TOKEN
    phone_id = settings.WHATSAPP_PHONE_NUMBER_ID
    verision = settings.WHATSAPP_API_VERSION

    url = f"https://graph.facebook.com/v20.0/{phone_id}/messages"
    
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json"
    }

    payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                    "components": [
                        {
                            "type": "body",
                            "parameters": message
                        }
                    ]
                }
        }
    response = requests.post(url, json=payload, headers=headers)
    try:
        data = response.json()
    except Exception:
        data = {"error": "Invalid JSON response"}

    # Return both response and parsed JSON to avoid losing info
    return {"status_code": response.status_code, "data": data}


@api_view(['GET'])
@authentication_classes([])  # Bypass default JWT auth - use manual API key validation
@permission_classes([permissions.AllowAny])
def get_invitation_by_phone(request):
    """
    Lookup a pending invitation by phone number.
    Used by the agent to find the token for a user who clicked 'Accept' on WhatsApp.
    """
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
             
        phone = request.query_params.get('phone')
        if not phone:
            return Response({'error': 'phone query parameter is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Normalize phone (digits only)
        clean_phone = ''.join(filter(str.isdigit, phone))
        if not clean_phone or len(clean_phone) < 6:
            return Response({'error': 'Invalid phone number'}, status=status.HTTP_400_BAD_REQUEST)

        # NEW FLOW (ONE-TAP): Check StaffActivationRecord first (pending activation by phone)
        from .models import UserInvitation, StaffActivationRecord
        from .services import _find_staff_activation_record_by_phone
        from django.db.models import Q

        activation_record = _find_staff_activation_record_by_phone(clean_phone)

        if activation_record:
            return Response({
                'success': True,
                'type': 'activation',
                'invitation': {
                    'token': f"ACTIVATION:{activation_record.id}",
                    'first_name': activation_record.first_name or 'Staff',
                    'last_name': activation_record.last_name or '',
                    'role': activation_record.role,
                    'restaurant_id': str(activation_record.restaurant.id),
                    'restaurant_name': activation_record.restaurant.name,
                    'phone': clean_phone
                }
            })

        # OLD FLOW: UserInvitation (email/token invites with phone in extra_data)
        invitation = UserInvitation.objects.filter(
            is_accepted=False,
            expires_at__gt=timezone.now()
        ).filter(
            Q(extra_data__phone__icontains=clean_phone) |
            Q(extra_data__phone_number__icontains=clean_phone)
        ).first()

        if not invitation:
            return Response({'error': 'No pending invitation found for this number'}, status=status.HTTP_404_NOT_FOUND)

        return Response({
            'success': True,
            'type': 'invitation',
            'invitation': {
                'token': invitation.invitation_token,
                'first_name': invitation.first_name,
                'last_name': invitation.last_name,
                'role': invitation.role,
                'restaurant_id': str(invitation.restaurant.id),
                'restaurant_name': invitation.restaurant.name
            }
        })
        
    except Exception as e:
        logger.error(f"Invitation lookup error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _activation_user_payload(user):
    """Build user payload for activation response."""
    return {
        'id': str(user.id),
        'email': user.email,
        'first_name': user.first_name,
        'last_name': user.last_name or '',
        'phone': user.phone,
        'role': user.role,
        'restaurant': {
            'id': str(user.restaurant.id),
            'name': user.restaurant.name,
        },
    }


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def account_activation_from_agent(request):
    """
    Single-step account activation by phone. Used by Miya's account_activation tool.
    ALWAYS checks the database first; returns a strict validation sequence and exact
    message_for_user so Miya never shows technical errors.
    Payload: { "phone": "212600959067" }. Returns { "success", "template_sent?", "user?", "message_for_user" }.
    """
    try:
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        if not expected_key:
            return Response({
                'success': False,
                'error': 'Agent key not configured',
                'message_for_user': "We couldn't complete your request. Please try again later.",
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        phone = request.data.get('phone') or ''
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if not clean_phone or len(clean_phone) < 6:
            return Response({
                'success': False,
                'error': 'Invalid or missing phone number',
                'message_for_user': "We couldn't find your account. Please contact your manager to be added to your restaurant.",
            }, status=status.HTTP_400_BAD_REQUEST)

        from .services import get_activation_status_by_phone, try_activate_staff_on_inbound_message

        # 1. Always check database first
        status_result = get_activation_status_by_phone(clean_phone)
        act_status = status_result['status']
        existing_user = status_result.get('user')

        # 2. pending_activation: activate, update to active, respond with congratulations
        if act_status == 'pending_activation':
            user = try_activate_staff_on_inbound_message(clean_phone)
            if not user:
                return Response(
                    {
                        'success': False,
                        'message_for_user': 'No pending activation found for this phone number.',
                    },
                    status=status.HTTP_200_OK,
                )
            from notifications.services import notification_service
            notification_service.send_staff_activated_welcome(
                phone=clean_phone,
                first_name=user.first_name or "Staff",
                restaurant_name=user.restaurant.name if getattr(user, 'restaurant', None) else "",
            )
            return Response(
                {
                    'success': True,
                    'template_sent': True,
                    'user': _activation_user_payload(user),
                    'message_for_user': 'Congratulations! Your account has been successfully activated. Welcome to the team!',
                },
                status=status.HTTP_200_OK,
            )

        # 3. active: do NOT modify, respond already activated (no template)
        if act_status == 'active' and existing_user:
            return Response(
                {
                    'success': True,
                    'template_sent': False,
                    'user': _activation_user_payload(existing_user),
                    'message_for_user': 'Congratulations! Your account has been successfully activated. Welcome to the team!',
                },
                status=status.HTTP_200_OK,
            )

        # 4. no_pending: user exists but no pending activation record
        if act_status == 'no_pending':
            return Response(
                {
                    'success': False,
                    'message_for_user': 'No pending activation found for this phone number.',
                },
                status=status.HTTP_200_OK,
            )

        # 5. not_found: no user record at all
        return Response(
            {
                'success': False,
                'message_for_user': "We couldn't find your account. Please contact your manager to be added to your restaurant.",
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Account activation error: {e}")
        return Response({
            'success': False,
            'error': str(e),
            'message_for_user': "We couldn't complete your request. Please try again later.",
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass default JWT auth - use manual API key validation
@permission_classes([permissions.AllowAny])  # Authenticated via Agent Key
def accept_invitation_from_agent(request):
    """
    Endpoint for Lua Agent to accept invitations on behalf of staff.
    
    Expected payload:
    {
        "invitation_token": "abc-123",
        "phone": "+1234567890",
        "first_name": "John",
        "last_name": "Doe",  # optional
        "pin": "1234"
    }
    """
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({
                'success': False,
                'error': 'Agent key not configured'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({
                'success': False,
                'error': 'Unauthorized'
            }, status=status.HTTP_401_UNAUTHORIZED)
             
        # Extract parameters
        invitation_token = request.data.get('invitation_token')
        phone = request.data.get('phone', '')
        pin = request.data.get('pin') or '0000'
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')

        # NEW FLOW (ONE-TAP): activation by phone via StaffActivationRecord
        if invitation_token and str(invitation_token).startswith('ACTIVATION:'):
            from .models import StaffActivationRecord
            from .services import try_activate_staff_on_inbound_message
            clean_phone = ''
            try:
                record_id = str(invitation_token).replace('ACTIVATION:', '', 1)
                record = StaffActivationRecord.objects.get(id=record_id, status=StaffActivationRecord.STATUS_NOT_ACTIVATED)
                clean_phone = ''.join(filter(str.isdigit, record.phone))
            except (StaffActivationRecord.DoesNotExist, ValueError):
                pass
            if not clean_phone and phone:
                clean_phone = ''.join(filter(str.isdigit, phone))
            if not clean_phone:
                return Response({
                    'success': False,
                    'error': 'phone is required for activation'
                }, status=status.HTTP_400_BAD_REQUEST)
            user = try_activate_staff_on_inbound_message(clean_phone)
            if not user:
                return Response({
                    'success': False,
                    'error': 'No pending activation found for this phone number'
                }, status=status.HTTP_404_NOT_FOUND)
            message_for_user = (
                "Congratulations! Your account has been successfully activated. Welcome to the team!"
            )
            return Response({
                'success': True,
                'message_for_user': message_for_user,
                'user': {
                    'id': str(user.id),
                    'email': user.email,
                    'first_name': user.first_name,
                    'last_name': user.last_name,
                    'phone': user.phone,
                    'role': user.role,
                    'restaurant': {
                        'id': str(user.restaurant.id),
                        'name': user.restaurant.name
                    }
                }
            })

        if not invitation_token:
            return Response({
                'success': False,
                'error': 'invitation_token is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        # OLD FLOW: UserInvitation (token-based)
        user, error = UserManagementService.accept_invitation(
            token=invitation_token,
            password=pin,
            first_name=first_name,
            last_name=last_name
        )

        if error:
            return Response({
                'success': False,
                'error': error
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': True,
            'user': {
                'id': str(user.id),
                'email': user.email,
                'first_name': user.first_name,
                'last_name': user.last_name,
                'phone': user.phone,
                'role': user.role,
                'restaurant': {
                    'id': str(user.restaurant.id),
                    'name': user.restaurant.name
                }
            }
        })
        
    except Exception as e:
        logger.error(f"Agent invitation acceptance error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _validate_agent_key(request):
    """Validate LUA_WEBHOOK_API_KEY for agent-only endpoints."""
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    if not expected_key:
        return False, "Agent key not configured"
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    return True, None


def _resolve_restaurant_id_agent(request):
    rid = request.META.get('HTTP_X_RESTAURANT_ID')
    if not rid and getattr(request, 'data', None):
        rid = request.data.get('restaurant_id') or request.data.get('restaurantId')
    if not rid and request.method == 'GET':
        rid = request.query_params.get('restaurant_id') or request.query_params.get('restaurantId')
    if isinstance(rid, (list, tuple)):
        rid = rid[0] if rid else None
    return rid


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_failed_invites(request):
    """
    List failed WhatsApp invitation deliveries for the restaurant. Query: restaurant_id or X-Restaurant-Id.
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import InvitationDeliveryLog
    from .models import Restaurant
    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=rid.strip())
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Restaurant not found'}, status=status.HTTP_404_NOT_FOUND)
    qs = InvitationDeliveryLog.objects.filter(
        invitation__restaurant=restaurant,
        channel='whatsapp',
        status='FAILED',
    ).order_by('-sent_at').select_related('invitation')[:30]
    items = [
        {
            'id': str(log.id),
            'invitation_id': str(log.invitation_id),
            'recipient': log.recipient_address,
            'error_message': (log.error_message or '')[:200],
            'sent_at': log.sent_at.isoformat() if log.sent_at else None,
        }
        for log in qs
    ]
    return Response({'success': True, 'failed_invites': items, 'restaurant_id': str(restaurant.id)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_retry_invite(request):
    """
    Retry a failed WhatsApp invite. Body: log_id (InvitationDeliveryLog id) or invitation_id, restaurant_id.
    """
    is_valid, error = _validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    from .models import InvitationDeliveryLog, Restaurant
    from notifications.services import notification_service
    rid = _resolve_restaurant_id_agent(request)
    if not rid:
        rid = (request.data or {}).get('restaurant_id') or (request.data or {}).get('restaurantId')
    if not rid:
        return Response({'success': False, 'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        restaurant = Restaurant.objects.get(id=rid.strip() if isinstance(rid, str) else rid)
    except (Restaurant.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Restaurant not found'}, status=status.HTTP_404_NOT_FOUND)
    data = request.data or {}
    log_id = data.get('log_id') or data.get('logId') or data.get('id')
    inv_id = data.get('invitation_id') or data.get('invitationId')
    if not log_id and not inv_id:
        return Response({'success': False, 'error': 'log_id or invitation_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    if log_id:
        log = InvitationDeliveryLog.objects.filter(
            id=log_id,
            invitation__restaurant=restaurant,
            channel='whatsapp',
            status='FAILED',
        ).select_related('invitation', 'invitation__restaurant').first()
    else:
        log = InvitationDeliveryLog.objects.filter(
            invitation_id=inv_id,
            invitation__restaurant=restaurant,
            channel='whatsapp',
            status='FAILED',
        ).select_related('invitation', 'invitation__restaurant').first()
    if not log:
        return Response({'success': False, 'error': 'Failed invite log not found'}, status=status.HTTP_404_NOT_FOUND)
    inv = log.invitation
    phone = log.recipient_address
    invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={inv.invitation_token}"
    language = getattr(inv.restaurant, 'language', 'en') if getattr(inv, 'restaurant', None) else 'en'
    ok, info = notification_service.send_lua_staff_invite(
        invitation_token=inv.invitation_token,
        phone=phone,
        first_name=inv.first_name,
        restaurant_name=inv.restaurant.name,
        invite_link=invite_link,
        language=language,
    )
    log.attempt_count = getattr(log, 'attempt_count', 1) + 1
    log.status = 'SENT' if ok else 'FAILED'
    log.response_data = info or {}
    log.save(update_fields=['attempt_count', 'status', 'response_data'])
    return Response({
        'success': ok,
        'message': 'Invite sent.' if ok else 'Retry failed.',
        'log_id': str(log.id),
        'status': log.status,
    })
