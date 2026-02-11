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


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def account_activation_from_agent(request):
    """
    Single-step account activation by phone. Used by Miya's account_activation tool.
    Staff send first message → agent calls this with their phone → we activate and return success.
    On success we send the staff_activated_welcome WhatsApp template; response has template_sent=True
    and message_for_user=None so Miya does not send a duplicate inline reply.
    Payload: { "phone": "212600959067" }. Returns { "success", "template_sent?", "user?", "message_for_user?" }.
    """
    try:
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        if not expected_key:
            return Response({'success': False, 'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        phone = request.data.get('phone') or ''
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if not clean_phone or len(clean_phone) < 6:
            return Response({
                'success': False,
                'error': 'Invalid or missing phone number'
            }, status=status.HTTP_400_BAD_REQUEST)

        from .services import try_activate_staff_on_inbound_message
        from .models import CustomUser

        user = try_activate_staff_on_inbound_message(clean_phone)

        if not user:
            # Idempotency: if the account is already active for this phone, treat as SUCCESS
            existing_user = CustomUser.objects.filter(phone__icontains=clean_phone).first()
            if existing_user:
                from notifications.services import notification_service
                notification_service.send_staff_activated_welcome(
                    phone=clean_phone,
                    first_name=existing_user.first_name or "Staff",
                    restaurant_name=existing_user.restaurant.name if getattr(existing_user, 'restaurant', None) else "",
                )
                return Response(
                    {
                        'success': True,
                        'template_sent': True,
                        'user': {
                            'id': str(existing_user.id),
                            'email': existing_user.email,
                            'first_name': existing_user.first_name,
                            'last_name': existing_user.last_name or '',
                            'phone': existing_user.phone,
                            'role': existing_user.role,
                            'restaurant': {
                                'id': str(existing_user.restaurant.id),
                                'name': existing_user.restaurant.name,
                            },
                        },
                        'message_for_user': None,
                    },
                    status=status.HTTP_200_OK,
                )

            # No pending activation and no existing account: give a gentle, actionable error
            return Response(
                {
                    'success': False,
                    'error': 'No pending activation found for this phone number.',
                    'message_for_user': (
                        "I can’t see your activation details yet. "
                        "Please ask your manager to add your WhatsApp number to the staff list or resend the activation link. "
                        "Once that’s done, just send me this same message again."
                    ),
                },
                status=status.HTTP_404_NOT_FOUND,
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
                'user': {
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
                },
                'message_for_user': None,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.error(f"Account activation error: {e}")
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


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
                "Your account has been successfully activated! You can now interact with Mizan AI Agent. "
                "Welcome to the team!"
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
