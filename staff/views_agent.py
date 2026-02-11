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

