from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
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


@api_view(['POST'])
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
        pin = request.data.get('pin')
        first_name = request.data.get('first_name', '')
        last_name = request.data.get('last_name', '')
        
        if not invitation_token or not pin:
            return Response({
                'success': False,
                'error': 'invitation_token and pin are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Accept invitation using existing service
        user, error = UserManagementService.accept_invitation(
            token=invitation_token,
            password=pin,  # Using PIN as password
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
