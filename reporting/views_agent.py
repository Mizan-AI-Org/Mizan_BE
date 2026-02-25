"""
Agent-authenticated views for reporting app.
These endpoints use the LUA_WEBHOOK_API_KEY for authentication instead of JWT.
"""
from rest_framework import status, permissions
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response
from django.conf import settings
from .models import Incident
from accounts.models import Restaurant, CustomUser
import logging
from core.utils import resolve_agent_restaurant_and_user

logger = logging.getLogger(__name__)


def validate_agent_key(request):
    """Validate the agent API key from Authorization header."""
    auth_header = request.headers.get('Authorization', '')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', '')
    
    if not expected_key:
        return False, 'Agent authentication not configured'
    
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        if token == expected_key:
            return True, None
    
    return False, 'Invalid or missing agent key'


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_create_incident(request):
    """
    Create an incident report from the agent.
    
    Expected payload:
    {
        "restaurant_id": "uuid",
        "title": "Short summary",
        "description": "Full description from staff",
        "category": "Safety|Maintenance|HR|Service|General",
        "priority": "LOW|MEDIUM|HIGH|CRITICAL",
        "reporter_phone": "optional phone number of reporter"
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        restaurant_id = data.get('restaurant_id') or data.get('restaurantId')
        description = (data.get('description') or '').strip()
        category = data.get('category', 'General')
        priority = data.get('priority', 'MEDIUM')
        reporter_phone = data.get('reporter_phone')

        # Canonical incident types (Miya determines type; title is constant per type)
        VALID_CATEGORIES = ('Safety', 'Maintenance', 'HR', 'Service', 'General')
        category = (category or 'General').strip()
        if category not in VALID_CATEGORIES:
            category = 'General'
        title = f"{category} incident"

        if not description:
            description = title
        if not description:
            return Response(
                {'error': 'description is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Resolve restaurant + reporter without requiring restaurant_id explicitly
        restaurant = None
        reporter = None
        if restaurant_id:
            try:
                restaurant = Restaurant.objects.get(id=restaurant_id)
            except Restaurant.DoesNotExist:
                restaurant = None

        if not restaurant:
            restaurant, reporter = resolve_agent_restaurant_and_user(request=request, payload=data)

        if not reporter and reporter_phone:
            digits = ''.join(filter(str.isdigit, str(reporter_phone)))
            if digits and len(digits) >= 9:
                suffix = digits[-9:]
                reporter = CustomUser.objects.filter(phone__endswith=suffix, is_active=True).select_related('restaurant').first()
                if reporter and not restaurant and getattr(reporter, 'restaurant', None):
                    restaurant = reporter.restaurant
                    logger.info(f"[AgentIncident] Resolved restaurant from reporter_phone: {restaurant.name}")

        if not restaurant:
            return Response(
                {
                    'error': 'Unable to resolve restaurant context. Provide restaurant_id, or include sessionId/userId/email/phone/token in the payload. Ensure your phone number is linked to a staff account.',
                    'message_for_user': "We couldn't link this report to your restaurant. Please make sure you're messaging from the phone number we have on file for your staff account.",
                },
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Auto-infer priority from description if not provided or invalid
        def _infer_priority(text: str) -> str:
            t = (text or '').lower()
            if any(k in t for k in ['life threatening', 'life-threatening', 'fire', 'gas leak', 'explosion']):
                return 'CRITICAL'
            if any(k in t for k in ['injury', 'hurt', 'bleeding', 'severe', 'danger', 'urgent', 'emergency']):
                return 'HIGH'
            if any(k in t for k in ['minor', 'small issue', 'low risk', 'low-risk']):
                return 'LOW'
            return 'MEDIUM'

        valid_priorities = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
        priority_upper = str(priority or '').upper()
        if priority_upper not in valid_priorities:
            priority = _infer_priority(f"{title}\n{description}")
        else:
            priority = priority_upper
        
        # Create the incident
        incident = Incident.objects.create(
            restaurant=restaurant,
            reporter=reporter,
            title=title,
            description=description,
            category=category,
            priority=priority,
            status='OPEN'
        )
        
        logger.info(f"[AgentIncident] Created incident {incident.id} for restaurant {restaurant.name}")
        
        return Response({
            'success': True,
            'id': str(incident.id),
            'title': incident.title,
            'priority': incident.priority,
            'status': incident.status,
            'created_at': incident.created_at.isoformat()
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.error(f"[AgentIncident] Error creating incident: {e}")
        err_msg = str(e)
        return Response(
            {
                'error': err_msg,
                'message_for_user': "Something went wrong while saving the report. Please try again or contact your manager.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
