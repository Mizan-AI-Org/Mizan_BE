"""
Agent-authenticated endpoints for checklist review (Miya autonomy).
Managers can list completed checklists pending review and approve/reject from WhatsApp.
"""
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.conf import settings
from django.utils import timezone

from .models import ChecklistExecution, ChecklistAction
from accounts.models import Restaurant
import logging

logger = logging.getLogger(__name__)


def validate_agent_key(request):
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    if not expected_key:
        return False, "Agent key not configured"
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    return True, None


def _resolve_restaurant(request):
    rid = request.META.get('HTTP_X_RESTAURANT_ID')
    if not rid and getattr(request, 'data', None):
        rid = (request.data.get('restaurant_id') or request.data.get('restaurantId'))
    if not rid and request.method == 'GET':
        rid = request.query_params.get('restaurant_id') or request.query_params.get('restaurantId')
    if isinstance(rid, (list, tuple)):
        rid = rid[0] if rid else None
    if rid and isinstance(rid, str):
        try:
            return Restaurant.objects.get(id=rid.strip()), None
        except (Restaurant.DoesNotExist, ValueError, TypeError):
            pass
    from core.utils import resolve_agent_restaurant_and_user
    payload = dict(request.query_params)
    if getattr(request, 'data', None):
        for k, v in (request.data or {}).items():
            if k == 'metadata' and isinstance(v, dict):
                payload.update(v)
            else:
                payload[k] = v
    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=payload)
    if not restaurant:
        return None, {'error': 'Unable to resolve restaurant context.', 'status': 400}
    return restaurant, None


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_checklists_for_review(request):
    """
    List completed checklist executions pending manager approval.
    Query: restaurant_id or X-Restaurant-Id.
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    qs = ChecklistExecution.objects.filter(
        template__restaurant=restaurant,
        status='COMPLETED',
        supervisor_approved=False,
    ).order_by('-completed_at').select_related('assigned_to', 'template')[:30]
    items = [
        {
            'id': str(e.id),
            'template_name': e.template.name if e.template else '—',
            'staff_name': f"{e.assigned_to.first_name} {e.assigned_to.last_name}".strip() if e.assigned_to else '—',
            'staff_id': str(e.assigned_to.id) if e.assigned_to else None,
            'completed_at': e.completed_at.isoformat() if e.completed_at else None,
        }
        for e in qs
    ]
    return Response({'success': True, 'executions': items, 'restaurant_id': str(restaurant.id)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_approve_checklist(request):
    """Approve a completed checklist. Body: execution_id, restaurant_id."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    data = request.data or {}
    eid = data.get('execution_id') or data.get('executionId') or data.get('id')
    if not eid:
        return Response({'success': False, 'error': 'execution_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        execution = ChecklistExecution.objects.get(
            id=eid,
            template__restaurant=restaurant,
            status='COMPLETED',
        )
    except ChecklistExecution.DoesNotExist:
        return Response({'success': False, 'error': 'Execution not found'}, status=status.HTTP_404_NOT_FOUND)
    execution.supervisor_approved = True
    execution.approved_by = None
    execution.approved_at = timezone.now()
    execution.save(update_fields=['supervisor_approved', 'approved_by', 'approved_at'])
    return Response({
        'success': True,
        'message': 'Checklist approved.',
        'execution_id': str(execution.id),
    })


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_reject_checklist(request):
    """Reject a completed checklist. Body: execution_id, reason (optional), restaurant_id."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    data = request.data or {}
    eid = data.get('execution_id') or data.get('executionId') or data.get('id')
    reason = (data.get('reason') or data.get('message') or 'Rejected via Miya').strip()
    if not eid:
        return Response({'success': False, 'error': 'execution_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        execution = ChecklistExecution.objects.get(
            id=eid,
            template__restaurant=restaurant,
            status='COMPLETED',
        )
    except ChecklistExecution.DoesNotExist:
        return Response({'success': False, 'error': 'Execution not found'}, status=status.HTTP_404_NOT_FOUND)
    execution.supervisor_approved = False
    execution.approved_by = None
    execution.approved_at = None
    execution.save(update_fields=['supervisor_approved', 'approved_by', 'approved_at'])
    try:
        ChecklistAction.objects.create(
            execution=execution,
            title='Checklist Rejected',
            description=reason,
            priority='MEDIUM',
            assigned_to=execution.assigned_to,
        )
    except Exception:
        pass
    return Response({
        'success': True,
        'message': 'Checklist rejected.',
        'execution_id': str(execution.id),
    })
