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
        restaurant_id = data.get('restaurant_id')
        title = data.get('title')
        description = data.get('description')
        category = data.get('category', 'General')
        priority = data.get('priority', 'MEDIUM')
        reporter_phone = data.get('reporter_phone')
        
        if not restaurant_id:
            return Response(
                {'error': 'restaurant_id is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        if not title or not description:
            return Response(
                {'error': 'title and description are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate restaurant exists
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return Response(
                {'error': 'Restaurant not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Optionally find reporter by phone
        reporter = None
        if reporter_phone:
            reporter = CustomUser.objects.filter(phone=reporter_phone).first()
        
        # Validate priority
        valid_priorities = ['LOW', 'MEDIUM', 'HIGH', 'CRITICAL']
        if priority.upper() not in valid_priorities:
            priority = 'MEDIUM'
        else:
            priority = priority.upper()
        
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
        return Response(
            {'error': str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )
