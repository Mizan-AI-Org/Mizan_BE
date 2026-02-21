"""
Agent-authenticated endpoints for staff app.
Used to ingest Staff Requests coming from Lua/WhatsApp into the manager inbox.
"""

from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.conf import settings
from django.utils import timezone
import logging

from accounts.models import CustomUser, Restaurant
from core.utils import resolve_agent_restaurant_and_user


def _resolve_restaurant_and_staff_by_phone(phone_raw):
    """Resolve (restaurant, staff) from phone when agent sends phone but not restaurant_id."""
    if not phone_raw:
        return None, None
    phone_digits = ''.join(filter(str.isdigit, str(phone_raw)))
    if not phone_digits:
        return None, None
    default_cc = ''.join(filter(str.isdigit, str(getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '') or '')))
    possible_patterns = [phone_digits, f"+{phone_digits}"]
    if len(phone_digits) > 10:
        possible_patterns.extend([phone_digits[-10:], f"+{phone_digits[-10:]}"])
    if default_cc and phone_digits.startswith(default_cc):
        local = phone_digits[len(default_cc):]
        if local:
            possible_patterns.extend([local, f"0{local}", f"+{default_cc}{local}"])
    if phone_digits.startswith('0') and len(phone_digits) > 1:
        possible_patterns.append(phone_digits.lstrip('0'))
        if default_cc:
            possible_patterns.append(f"{default_cc}{phone_digits.lstrip('0')}")
    seen = set()
    possible_patterns = [p for p in possible_patterns if p and not (p in seen or seen.add(p))]
    for pattern in possible_patterns:
        try:
            staff = CustomUser.objects.filter(
                phone__icontains=pattern,
                is_active=True
            ).exclude(role='SUPER_ADMIN').select_related('restaurant').first()
            if staff and getattr(staff, 'restaurant_id', None):
                return staff.restaurant, staff
        except Exception:
            continue
    return None, None
from notifications.services import notification_service
from notifications.models import Notification

from .models import StaffRequest, StaffRequestComment
from .models_task import SafetyConcernReport

logger = logging.getLogger(__name__)


def validate_agent_key(request):
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    if not expected_key:
        return False, "Agent key not configured"
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    return True, None


def _notify_managers_of_staff_request(req: StaffRequest):
    try:
        managers = CustomUser.objects.filter(
            restaurant=req.restaurant,
            role__in=['MANAGER', 'ADMIN', 'SUPER_ADMIN', 'OWNER'],
            is_active=True,
        )
        for m in managers:
            notif = Notification.objects.create(
                recipient=m,
                title="New Staff Request",
                message=(req.subject or "Staff request") + (f" — {req.staff_name}" if req.staff_name else ""),
                notification_type='STAFF_REQUEST',
                priority=req.priority,
                data={
                    'staff_request_id': str(req.id),
                    'route': f"/dashboard/staff-requests/{req.id}",
                    'status': req.status,
                    'category': req.category,
                },
            )
            notification_service.send_custom_notification(
                recipient=m,
                notification=notif,
                message=notif.message,
                notification_type='STAFF_REQUEST',
                title=notif.title,
                channels=['app'],
            )
    except Exception as e:
        logger.warning(f"StaffRequest notify managers failed: {e}")


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT
@permission_classes([permissions.AllowAny])
def agent_ingest_staff_request(request):
    """
    Ingest a staff request coming from Lua/WhatsApp into Mizan.

    Expected payload (flexible):
    - subject/title
    - description/message/body
    - priority: LOW|MEDIUM|HIGH|URGENT (optional)
    - category: DOCUMENT|HR|SCHEDULING|PAYROLL|OPERATIONS|OTHER (optional)
    - phone/phoneNumber/from (optional) to resolve staff
    - external_id/inquiryId/ticketId (optional)
    - metadata (optional)
    - restaurant_id / sessionId / userId / email / token (optional) for resolution
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)

    data = request.data or {}

    # Resolve staff/restaurant context first
    restaurant, staff = resolve_agent_restaurant_and_user(request=request, payload=data)
    if not restaurant:
        # Try explicit restaurant_id
        rest_id = data.get('restaurant_id') or data.get('restaurantId')
        if rest_id:
            try:
                restaurant = Restaurant.objects.get(id=rest_id)
            except Exception:
                restaurant = None

    if not restaurant:
        # Fallback: resolve restaurant (and staff) from phone so agent ingest succeeds when tool sends phone but not restaurant_id
        phone_raw = data.get('phone') or data.get('phoneNumber') or data.get('from')
        restaurant, staff = _resolve_restaurant_and_staff_by_phone(phone_raw) or (None, None)

    if not restaurant:
        return Response({'success': False, 'error': 'Unable to resolve restaurant context'}, status=status.HTTP_400_BAD_REQUEST)

    subject = (data.get('subject') or data.get('title') or data.get('requestTitle') or '').strip()
    description = (data.get('description') or data.get('message') or data.get('body') or data.get('text') or '').strip()
    if not subject and description:
        subject = description[:80]
    if not description:
        return Response({'success': False, 'error': 'description/message is required'}, status=status.HTTP_400_BAD_REQUEST)

    priority = str(data.get('priority') or 'MEDIUM').upper()
    if priority not in ['LOW', 'MEDIUM', 'HIGH', 'URGENT']:
        priority = 'MEDIUM'

    category = str(data.get('category') or 'OTHER').upper()
    if category not in ['DOCUMENT', 'HR', 'SCHEDULING', 'PAYROLL', 'OPERATIONS', 'OTHER']:
        category = 'OTHER'

    external_id = (data.get('external_id') or data.get('inquiryId') or data.get('ticketId') or '').strip()
    source = (data.get('source') or data.get('channel') or 'whatsapp').strip().lower()

    phone_raw = data.get('phone') or data.get('phoneNumber') or data.get('from')
    phone_digits = ''.join(filter(str.isdigit, str(phone_raw or '')))

    staff_name = ''
    staff_phone = phone_raw or ''
    if staff:
        try:
            staff_name = staff.get_full_name() or f"{staff.first_name} {staff.last_name}".strip()
        except Exception:
            staff_name = f"{getattr(staff, 'first_name', '')} {getattr(staff, 'last_name', '')}".strip()
        staff_phone = getattr(staff, 'phone', '') or staff_phone
    else:
        # Best-effort link by phone within restaurant
        if phone_digits:
            try:
                staff = CustomUser.objects.filter(
                    restaurant=restaurant,
                    phone__icontains=phone_digits[-10:] if len(phone_digits) > 10 else phone_digits,
                    is_active=True
                ).exclude(role='SUPER_ADMIN').first()
                if staff:
                    staff_name = staff.get_full_name() or f"{staff.first_name} {staff.last_name}".strip()
                    staff_phone = getattr(staff, 'phone', '') or staff_phone
            except Exception:
                staff = None

    req = StaffRequest.objects.create(
        restaurant=restaurant,
        staff=staff,
        staff_name=staff_name,
        staff_phone=staff_phone,
        category=category,
        priority=priority,
        status='PENDING',
        subject=subject,
        description=description,
        source=source,
        external_id=external_id,
        metadata=dict(data.get('metadata') or {}),
    )

    StaffRequestComment.objects.create(
        request=req,
        author=None,
        kind='system',
        body='Request received',
        metadata={'source': source, 'external_id': external_id, 'phone': phone_digits},
    )

    _notify_managers_of_staff_request(req)

    return Response({
        'success': True,
        'id': str(req.id),
        'status': req.status,
    }, status=status.HTTP_201_CREATED)


def _resolve_restaurant_for_staff_agent(request):
    """Resolve restaurant for staff agent endpoints (list/approve/reject)."""
    from core.utils import resolve_agent_restaurant_and_user
    payload = getattr(request, 'data', None) or {}
    if request.method == 'GET':
        payload = dict(request.query_params)
    rid = request.META.get('HTTP_X_RESTAURANT_ID') or payload.get('restaurant_id') or payload.get('restaurantId')
    if isinstance(rid, (list, tuple)):
        rid = rid[0] if rid else None
    if rid and isinstance(rid, str):
        try:
            return Restaurant.objects.get(id=rid.strip()), None
        except (Restaurant.DoesNotExist, ValueError, TypeError):
            pass
    payload = dict(request.query_params)
    if request.method == 'POST' and isinstance(getattr(request, 'data', None), dict):
        for k, v in (request.data or {}).items():
            if k == 'metadata' and isinstance(v, dict):
                for mk, mv in v.items():
                    payload.setdefault(mk, mv)
            else:
                payload.setdefault(k, v)
    restaurant, _ = resolve_agent_restaurant_and_user(request=request, payload=payload)
    if not restaurant:
        return None, {'error': 'Unable to resolve restaurant context.', 'status': 400}
    return restaurant, None


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_staff_requests(request):
    """
    List pending staff requests for the restaurant. Used by Miya so managers can approve/reject from WhatsApp.
    Auth: Bearer LUA_WEBHOOK_API_KEY. Query or X-Restaurant-Id: restaurant_id.
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    status_filter = request.query_params.get('status', 'PENDING').upper()
    qs = StaffRequest.objects.filter(restaurant=restaurant).order_by('-created_at')
    if status_filter != 'ALL':
        qs = qs.filter(status=status_filter)
    qs = qs[:50].select_related('staff')
    items = [
        {
            'id': str(r.id),
            'subject': r.subject or '',
            'description': (r.description or '')[:200],
            'staff_name': r.staff_name or (r.staff.get_full_name() if r.staff else ''),
            'staff_phone': r.staff_phone or '',
            'category': r.category,
            'priority': r.priority,
            'status': r.status,
            'created_at': r.created_at.isoformat() if r.created_at else None,
        }
        for r in qs
    ]
    return Response({'success': True, 'requests': items, 'restaurant_id': str(restaurant.id)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_approve_staff_request(request):
    """
    Approve a staff request. Miya uses this so managers can approve from WhatsApp. Notifies staff via WhatsApp.
    Auth: Bearer LUA_WEBHOOK_API_KEY. Body: request_id, restaurant_id (or X-Restaurant-Id).
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    data = request.data or {}
    req_id = data.get('request_id') or data.get('requestId') or data.get('id')
    if not req_id:
        return Response({'success': False, 'error': 'request_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        req = StaffRequest.objects.get(id=req_id, restaurant=restaurant)
    except StaffRequest.DoesNotExist:
        return Response({'success': False, 'error': 'Request not found'}, status=status.HTTP_404_NOT_FOUND)
    if req.status != 'PENDING':
        return Response({'success': False, 'error': f'Request is already {req.status}'}, status=status.HTTP_400_BAD_REQUEST)
    req.status = 'APPROVED'
    req.reviewed_by = None
    req.reviewed_at = timezone.now()
    req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
    StaffRequestComment.objects.create(
        request=req, author=None, kind='status_change',
        body='Approved via Miya (WhatsApp)',
        metadata={'from': 'PENDING', 'to': 'APPROVED'},
    )
    # Notify staff via WhatsApp so they don't need the app
    phone = req.staff_phone or (getattr(req.staff, 'phone', None) if req.staff else None)
    if phone:
        try:
            notification_service.send_whatsapp_text(
                phone,
                f"✅ Your request \"{req.subject or 'Request'}\" has been *approved* by your manager."
            )
        except Exception as e:
            logger.warning("Staff request approval WhatsApp notify failed: %s", e)
    return Response({
        'success': True,
        'message': 'Request approved. Staff has been notified via WhatsApp.',
        'request_id': str(req.id),
        'status': req.status,
    })


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_reject_staff_request(request):
    """
    Reject a staff request. Miya uses this so managers can reject from WhatsApp. Notifies staff via WhatsApp.
    Auth: Bearer LUA_WEBHOOK_API_KEY. Body: request_id, reason (optional), restaurant_id (or X-Restaurant-Id).
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    data = request.data or {}
    req_id = data.get('request_id') or data.get('requestId') or data.get('id')
    reason = (data.get('reason') or data.get('message') or '').strip()
    if not req_id:
        return Response({'success': False, 'error': 'request_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        req = StaffRequest.objects.get(id=req_id, restaurant=restaurant)
    except StaffRequest.DoesNotExist:
        return Response({'success': False, 'error': 'Request not found'}, status=status.HTTP_404_NOT_FOUND)
    if req.status != 'PENDING':
        return Response({'success': False, 'error': f'Request is already {req.status}'}, status=status.HTTP_400_BAD_REQUEST)
    req.status = 'REJECTED'
    req.reviewed_by = None
    req.reviewed_at = timezone.now()
    req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
    StaffRequestComment.objects.create(
        request=req, author=None, kind='status_change',
        body='Rejected via Miya (WhatsApp)' + (f': {reason}' if reason else ''),
        metadata={'from': 'PENDING', 'to': 'REJECTED', 'reason': reason},
    )
    phone = req.staff_phone or (getattr(req.staff, 'phone', None) if req.staff else None)
    if phone:
        try:
            msg = f"Your request \"{req.subject or 'Request'}\" was not approved."
            if reason:
                msg += f"\nReason: {reason}"
            notification_service.send_whatsapp_text(phone, msg)
        except Exception as e:
            logger.warning("Staff request rejection WhatsApp notify failed: %s", e)
    return Response({
        'success': True,
        'message': 'Request rejected. Staff has been notified via WhatsApp.',
        'request_id': str(req.id),
        'status': req.status,
    })


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_incidents(request):
    """
    List open incidents (safety concerns) for the restaurant. Query: restaurant_id or status.
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    status_filter = (request.query_params.get('status') or 'REPORTED,UNDER_REVIEW').strip().upper().split(',')
    qs = SafetyConcernReport.objects.filter(
        restaurant=restaurant,
        status__in=[s.strip() for s in status_filter if s.strip()],
    ).order_by('-created_at').select_related('reporter')[:30]
    items = [
        {
            'id': str(i.id),
            'title': i.title or '',
            'description': (i.description or '')[:200],
            'severity': i.severity,
            'status': i.status,
            'created_at': i.created_at.isoformat() if i.created_at else None,
        }
        for i in qs
    ]
    return Response({'success': True, 'incidents': items, 'restaurant_id': str(restaurant.id)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_close_incident(request):
    """Close/resolve an incident. Body: incident_id, resolution_notes (optional), restaurant_id."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    data = request.data or {}
    iid = data.get('incident_id') or data.get('incidentId') or data.get('id')
    notes = (data.get('resolution_notes') or data.get('notes') or '').strip()
    if not iid:
        return Response({'success': False, 'error': 'incident_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        inc = SafetyConcernReport.objects.get(id=iid, restaurant=restaurant)
    except SafetyConcernReport.DoesNotExist:
        return Response({'success': False, 'error': 'Incident not found'}, status=status.HTTP_404_NOT_FOUND)
    inc.status = 'RESOLVED'
    inc.resolved_at = timezone.now()
    inc.resolution_notes = notes or 'Closed via Miya'
    inc.resolved_by = None
    inc.save(update_fields=['status', 'resolved_at', 'resolution_notes', 'resolved_by', 'updated_at'])
    return Response({'success': True, 'message': 'Incident closed.', 'incident_id': str(inc.id)})


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_escalate_incident(request):
    """Escalate an incident to UNDER_REVIEW. Body: incident_id, restaurant_id."""
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])
    data = request.data or {}
    iid = data.get('incident_id') or data.get('incidentId') or data.get('id')
    if not iid:
        return Response({'success': False, 'error': 'incident_id is required'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        inc = SafetyConcernReport.objects.get(id=iid, restaurant=restaurant)
    except SafetyConcernReport.DoesNotExist:
        return Response({'success': False, 'error': 'Incident not found'}, status=status.HTTP_404_NOT_FOUND)
    inc.status = 'UNDER_REVIEW'
    inc.save(update_fields=['status', 'updated_at'])
    return Response({'success': True, 'message': 'Incident escalated.', 'incident_id': str(inc.id)})

