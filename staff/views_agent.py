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
from .request_routing import resolve_default_assignee_for_category
from .intent_router import (
    DEST_INCIDENT,
    IntentDecision,
    classify_request,
)
from .incident_routing import resolve_default_assignee_for_incident_type

logger = logging.getLogger(__name__)


# Canonical values for StaffRequest.category — kept in one place so agent
# ingest, the manager API, and the frontend all validate against the same
# list. Must match ``StaffRequest.CATEGORY_CHOICES``.
STAFF_REQUEST_CATEGORIES = (
    'DOCUMENT', 'HR', 'SCHEDULING', 'PAYROLL', 'FINANCE', 'OPERATIONS',
    'MAINTENANCE', 'RESERVATIONS', 'INVENTORY', 'OTHER',
)


def _normalize_category(raw) -> str:
    """Return a valid StaffRequest.category or 'OTHER'."""
    cat = str(raw or 'OTHER').upper().strip()
    # Accept a few common synonyms Miya (or webhook payloads) may send.
    aliases = {
        'MAINTAIN': 'MAINTENANCE',
        'REPAIR': 'MAINTENANCE',
        'EQUIPMENT': 'MAINTENANCE',
        'RESERVATION': 'RESERVATIONS',
        'BOOKING': 'RESERVATIONS',
        'BOOKINGS': 'RESERVATIONS',
        'STOCK': 'INVENTORY',
        'SUPPLIES': 'INVENTORY',
        'DOCUMENTS': 'DOCUMENT',
        # Common ways Miya / managers refer to FINANCE bills:
        'INVOICE': 'FINANCE',
        'INVOICES': 'FINANCE',
        'BILL': 'FINANCE',
        'BILLS': 'FINANCE',
        'TAX': 'FINANCE',
        'TAXES': 'FINANCE',
        'ACCOUNTING': 'FINANCE',
        'ACCOUNTS': 'FINANCE',
        'FINANCES': 'FINANCE',
    }
    cat = aliases.get(cat, cat)
    return cat if cat in STAFF_REQUEST_CATEGORIES else 'OTHER'


def validate_agent_key(request):
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    if not expected_key:
        return False, "Agent key not configured"
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    return True, None


def _create_incident_from_inbox_message(
    *,
    restaurant,
    reporter,
    subject: str,
    description: str,
    decision: IntentDecision,
    requested_priority: str,
    source: str,
    external_id: str,
    metadata: dict,
):
    """Create a ``SafetyConcernReport`` (and, best-effort, a legacy
    ``Incident``) from a staff message that the intent router has
    flagged as an incident rather than an inbox row.

    This duplicates the persistence layer used by
    ``reporting/views_agent.agent_create_incident`` but is callable
    in-process so the staff-request ingest endpoint can transparently
    re-route without a second HTTP hop.

    The reason we don't simply ``import`` the other view is that it is
    a DRF ``@api_view`` — calling it requires a fake ``Request`` and
    smuggles auth concerns. Inlining the few lines we need keeps the
    code path obvious.
    """
    from reporting.models import Incident  # local import to avoid cycles

    incident_type = decision.category or "General"
    # Prefer router-derived priority, fall back to the agent's hint, then
    # default to MEDIUM. ``CRITICAL`` is only ever set by the router.
    valid_priorities = {"LOW", "MEDIUM", "HIGH", "CRITICAL"}
    priority = (decision.priority or requested_priority or "MEDIUM").upper()
    # The inbox uses URGENT — incidents use CRITICAL. Map across.
    if priority == "URGENT":
        priority = "CRITICAL"
    if priority not in valid_priorities:
        priority = "MEDIUM"

    title = subject or f"{incident_type} incident"
    title = title[:255]

    assignee = resolve_default_assignee_for_incident_type(restaurant, incident_type)

    concern = SafetyConcernReport.objects.create(
        restaurant=restaurant,
        reporter=reporter,
        is_anonymous=False,
        incident_type=incident_type,
        title=title,
        description=description,
        severity=priority,
        status="OPEN",
        occurred_at=timezone.now(),
        assigned_to=assignee,
    )

    # Best-effort mirror into the legacy ``Incident`` table so the
    # reporting pipeline keeps working — never let this fail the call.
    try:
        Incident.objects.create(
            restaurant=restaurant,
            reporter=reporter,
            title=title,
            description=description,
            category=incident_type,
            priority=priority,
            status="OPEN",
            assigned_to=assignee,
        )
    except Exception as exc:  # noqa: BLE001 — best-effort mirror
        logger.warning("Legacy Incident mirror failed: %s", exc)

    # In-app notification for managers so the incident shows up next
    # to the existing "Reported Incidents" widget without waiting for
    # the next poll.
    try:
        managers = CustomUser.objects.filter(
            restaurant=restaurant,
            role__in=["MANAGER", "ADMIN", "SUPER_ADMIN", "OWNER"],
            is_active=True,
        )
        for m in managers:
            notif = Notification.objects.create(
                recipient=m,
                title=f"New {incident_type} incident",
                message=title,
                notification_type="INCIDENT",
                priority=priority,
                data={
                    "incident_id": str(concern.id),
                    "incident_type": incident_type,
                    "route": "/dashboard/analytics?tab=incidents",
                    "auto_routed_from": "staff_request_inbox",
                    "matched_terms": list(decision.matched_terms),
                },
            )
            notification_service.send_custom_notification(
                recipient=m,
                notification=notif,
                message=notif.message,
                notification_type="INCIDENT",
                title=notif.title,
                channels=["app"],
            )
    except Exception as exc:  # noqa: BLE001
        logger.warning("Incident notify managers failed: %s", exc)

    return concern, assignee, priority


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

    # Run the deterministic intent router *before* we persist anything.
    # This is the safety net that stops Miya from dumping every message
    # into the inbox with category=OTHER: obvious incidents (broken
    # equipment, fires, leaks, injuries, harassment, food-safety
    # hazards) get re-routed to the Reported Incidents surface, and
    # everything else gets a real category instead of falling back to
    # "OTHER". See ``staff/intent_router.py`` for the rule table.
    decision = classify_request(
        subject=subject,
        description=description,
        agent_category=data.get('category'),
    )

    # When the agent sent something concrete, ``_normalize_category``
    # is still our last line of defence (it accepts a few synonyms and
    # rejects garbage). The router output wins for inbox rows because
    # it has already validated against ``INBOX_CATEGORIES`` and
    # incorporated keyword inference.
    if decision.is_incident():
        # We'll call ``_create_incident_from_inbox_message`` below; the
        # ``category`` variable is unused for this branch but kept
        # initialised so static analyzers don't complain.
        category = _normalize_category(data.get('category'))
    else:
        category = decision.category

    external_id = (data.get('external_id') or data.get('inquiryId') or data.get('ticketId') or '').strip()
    source = (data.get('source') or data.get('channel') or 'whatsapp').strip().lower()

    # Voice note fields — populated when WhatsApp audio was transcribed
    # by ``notifications/services.py::transcribe_audio_bytes`` upstream.
    voice_audio_url = (data.get('voice_audio_url') or data.get('audio_url') or '').strip()
    transcription = (data.get('transcription') or data.get('transcript') or '').strip()
    transcription_language = (data.get('transcription_language') or data.get('language') or '').strip()[:16]

    phone_raw = data.get('phone') or data.get('phoneNumber') or data.get('from')
    phone_digits = ''.join(filter(str.isdigit, str(phone_raw or '')))

    staff_name = (data.get('staff_name') or data.get('name') or data.get('sender_name') or '').strip()
    staff_phone = phone_raw or ''
    if staff:
        try:
            staff_name = staff_name or staff.get_full_name() or f"{staff.first_name} {staff.last_name}".strip()
        except Exception:
            staff_name = staff_name or f"{getattr(staff, 'first_name', '')} {getattr(staff, 'last_name', '')}".strip()
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
                    staff_name = staff_name or staff.get_full_name() or f"{staff.first_name} {staff.last_name}".strip()
                    staff_phone = getattr(staff, 'phone', '') or staff_phone
            except Exception:
                staff = None

    # ------------------------------------------------------------------
    # Routing fork: if the intent router decided this is an incident,
    # short-circuit and create a SafetyConcernReport instead of an
    # inbox row. The agent gets a routed=True response so Miya can say
    # "I logged that as an incident" rather than "I added it to the
    # inbox" — making the rerouting visible to the user as well.
    # ------------------------------------------------------------------
    if decision.is_incident():
        concern, incident_assignee, incident_priority = _create_incident_from_inbox_message(
            restaurant=restaurant,
            reporter=staff,
            subject=subject,
            description=description,
            decision=decision,
            requested_priority=priority,
            source=source,
            external_id=external_id,
            metadata=dict(data.get('metadata') or {}),
        )
        logger.info(
            "agent_ingest_staff_request: re-routed to incident "
            "(restaurant=%s, incident=%s, type=%s, terms=%s)",
            restaurant.id, concern.id, decision.category, decision.matched_terms,
        )
        return Response({
            'success': True,
            'routed': True,
            'destination': 'INCIDENT',
            'incident_id': str(concern.id),
            'incident_type': decision.category,
            'priority': incident_priority,
            'matched_terms': list(decision.matched_terms),
            'message_for_user': (
                f"This sounded like a {decision.category.lower()} incident "
                f"({', '.join(decision.matched_terms[:3]) or 'safety signal'}), "
                "so I logged it on the Reported Incidents board instead of the inbox."
            ),
            'assignee': (
                {
                    'id': str(incident_assignee.id),
                    'name': incident_assignee.get_full_name() or incident_assignee.email,
                    'email': incident_assignee.email,
                    'auto_assigned': True,
                }
                if incident_assignee
                else None
            ),
        }, status=status.HTTP_201_CREATED)

    # Auto-assign from the tenant's onboarding category-owners map.
    # Miya may override this by passing an explicit ``assignee_id`` / ``assignee_email``.
    assignee = None
    explicit_assignee = (data.get('assignee_id') or data.get('assigneeId') or '').strip()
    explicit_assignee_email = (data.get('assignee_email') or '').strip()
    if explicit_assignee:
        try:
            assignee = CustomUser.objects.filter(
                id=explicit_assignee, restaurant=restaurant, is_active=True
            ).first()
        except (ValueError, TypeError):
            assignee = None
    if not assignee and explicit_assignee_email:
        assignee = CustomUser.objects.filter(
            email__iexact=explicit_assignee_email,
            restaurant=restaurant,
            is_active=True,
        ).first()
    auto_assigned = False
    if not assignee and str(data.get('auto_assign', True)).lower() not in ('false', '0', 'no'):
        assignee = resolve_default_assignee_for_category(restaurant, category)
        auto_assigned = assignee is not None

    # Stash the router's reasoning on the row so managers can see "this
    # was auto-categorised as PAYROLL because of the words salary,
    # payslip" — handy for tuning the rules later.
    inbox_metadata = dict(data.get('metadata') or {})
    inbox_metadata['intent_router'] = {
        'category': decision.category,
        'confidence': decision.confidence,
        'matched_terms': list(decision.matched_terms),
        'agent_category': (data.get('category') or 'OTHER'),
        'auto_categorised': decision.category != (data.get('category') or 'OTHER').upper(),
    }

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
        assignee=assignee,
        voice_audio_url=voice_audio_url,
        transcription=transcription,
        transcription_language=transcription_language,
        source=source,
        external_id=external_id,
        metadata=inbox_metadata,
    )

    StaffRequestComment.objects.create(
        request=req,
        author=None,
        kind='system',
        body='Request received',
        metadata={'source': source, 'external_id': external_id, 'phone': phone_digits},
    )

    # Audit comment when the router corrected Miya's category — makes
    # the auto-categorisation visible in the request timeline.
    if inbox_metadata['intent_router']['auto_categorised']:
        StaffRequestComment.objects.create(
            request=req,
            author=None,
            kind='system',
            body=(
                f"Auto-categorised as {decision.category} "
                f"(matched: {', '.join(decision.matched_terms[:5]) or 'fallback'})."
            ),
            metadata=inbox_metadata['intent_router'],
        )

    # Track whether the WhatsApp ping to the assignee actually fired so
    # the agent response can tell Miya the truth — preventing "they've
    # been notified on WhatsApp" hallucinations when the assignee has
    # no phone or the send failed.
    whatsapp_sent_to_assignee = False
    if assignee:
        StaffRequestComment.objects.create(
            request=req,
            author=None,
            kind='system',
            body=(
                f"Auto-assigned to {assignee.get_full_name() or assignee.email} "
                f"(category owner for {category.lower()})"
                if auto_assigned
                else f"Assigned to {assignee.get_full_name() or assignee.email}"
            ),
            metadata={
                'assignee_id': str(assignee.id),
                'assignee_name': assignee.get_full_name() or assignee.email,
                'auto_assigned': auto_assigned,
                'category': category,
            },
        )
        # Best-effort WhatsApp ping to the owner so they know something
        # landed in their lane. Silent on failure — not critical, but we
        # do flip a flag the agent reads back so its reply doesn't fib.
        owner_phone = getattr(assignee, 'phone', '') or ''
        if owner_phone:
            try:
                # ``send_whatsapp_text`` returns ``(ok: bool, info: dict)``
                # — capture the boolean so the agent reply only claims a
                # WhatsApp ping when one actually went out (HTTP 200 from
                # Meta). Fail closed otherwise.
                wa_ok, _wa_info = notification_service.send_whatsapp_text(
                    owner_phone,
                    (
                        f"📩 New {category.lower()} request from "
                        f"{staff_name or 'a staff member'}: "
                        f"\"{subject[:80]}\". Open the inbox to review."
                    ),
                )
                whatsapp_sent_to_assignee = bool(wa_ok)
            except Exception as exc:
                logger.warning("StaffRequest assignee WhatsApp ping failed: %s", exc)
                whatsapp_sent_to_assignee = False

    _notify_managers_of_staff_request(req)

    return Response({
        'success': True,
        'routed': True,
        'destination': 'INBOX',
        'id': str(req.id),
        'status': req.status,
        'category': req.category,
        'auto_categorised': inbox_metadata['intent_router']['auto_categorised'],
        'matched_terms': list(decision.matched_terms),
        'message_for_user': (
            f"Logged in the {req.category.title()} lane of the team inbox."
            if req.category != 'OTHER'
            else "Logged in the team inbox — I couldn't pin a specific category, "
                 "so a manager will triage it."
        ),
        'assignee': (
            {
                'id': str(assignee.id),
                'name': assignee.get_full_name() or assignee.email,
                'email': assignee.email,
                'auto_assigned': auto_assigned,
                'has_phone': bool(getattr(assignee, 'phone', '') or ''),
                'whatsapp_sent': whatsapp_sent_to_assignee,
            }
            if assignee
            else None
        ),
        # Hoisted to the top level so Miya's reply rules (see persona:
        # "ESCALATION / ASSIGNMENT") can branch on it without digging
        # into the assignee sub-object — keeps the persona prompt simple.
        'whatsapp_sent': whatsapp_sent_to_assignee,
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
    qs = qs[:50].select_related('staff', 'assignee')
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
            'assignee': (
                {
                    'id': str(r.assignee_id),
                    'name': r.assignee.get_full_name() or r.assignee.email,
                    'email': r.assignee.email,
                }
                if r.assignee_id
                else None
            ),
            'has_voice': bool(r.voice_audio_url),
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


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_assign_staff_request(request):
    """
    Assign (or reassign) an existing StaffRequest to a specific user.
    Miya calls this when a manager says e.g. "reassign that plumbing
    request to Yassine" or when a category owner changes.

    Auth: Bearer LUA_WEBHOOK_API_KEY.
    Body: request_id, assignee_id OR assignee_email, note (optional),
          restaurant_id (or X-Restaurant-Id).
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
    except (StaffRequest.DoesNotExist, ValueError, TypeError):
        return Response({'success': False, 'error': 'Request not found'}, status=status.HTTP_404_NOT_FOUND)

    new_assignee = None
    assignee_id = (data.get('assignee_id') or data.get('assigneeId') or '').strip()
    assignee_email = (data.get('assignee_email') or data.get('email') or '').strip()
    if assignee_id:
        try:
            new_assignee = CustomUser.objects.filter(
                id=assignee_id, restaurant=restaurant, is_active=True
            ).first()
        except (ValueError, TypeError):
            new_assignee = None
    if not new_assignee and assignee_email:
        new_assignee = CustomUser.objects.filter(
            email__iexact=assignee_email,
            restaurant=restaurant,
            is_active=True,
        ).first()
    if not new_assignee:
        return Response(
            {'success': False, 'error': 'assignee_id or assignee_email must resolve to an active user in this restaurant'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    previous_id = str(req.assignee_id) if req.assignee_id else None
    req.assignee = new_assignee
    req.save(update_fields=['assignee', 'updated_at'])

    note = (data.get('note') or data.get('reason') or '').strip()
    StaffRequestComment.objects.create(
        request=req,
        author=None,
        kind='system',
        body=(
            f"Reassigned to {new_assignee.get_full_name() or new_assignee.email}"
            + (f" — {note}" if note else " via Miya")
        ),
        metadata={
            'previous_assignee_id': previous_id,
            'new_assignee_id': str(new_assignee.id),
            'new_assignee_name': new_assignee.get_full_name() or new_assignee.email,
            'note': note,
        },
    )

    # Best-effort WhatsApp ping to the new owner. Track success so the
    # agent reply doesn't claim a notification went out when it didn't.
    whatsapp_sent_to_assignee = False
    owner_phone = getattr(new_assignee, 'phone', '') or ''
    if owner_phone:
        try:
            wa_ok, _wa_info = notification_service.send_whatsapp_text(
                owner_phone,
                (
                    f"📩 You've been assigned a {req.category.lower()} request: "
                    f"\"{(req.subject or '')[:80]}\"."
                ),
            )
            whatsapp_sent_to_assignee = bool(wa_ok)
        except Exception as exc:
            logger.warning("StaffRequest reassign WhatsApp ping failed: %s", exc)
            whatsapp_sent_to_assignee = False

    return Response({
        'success': True,
        'request_id': str(req.id),
        'whatsapp_sent': whatsapp_sent_to_assignee,
        'assignee': {
            'id': str(new_assignee.id),
            'name': new_assignee.get_full_name() or new_assignee.email,
            'email': new_assignee.email,
            'has_phone': bool(owner_phone),
            'whatsapp_sent': whatsapp_sent_to_assignee,
        },
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
    status_filter = (request.query_params.get('status') or 'OPEN').strip().upper().split(',')
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
    inc.status = 'OPEN'
    inc.save(update_fields=['status', 'updated_at'])
    return Response({'success': True, 'message': 'Incident escalated.', 'incident_id': str(inc.id)})

