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
from core.read_through_cache import get_or_set, safe_cache_delete
from core.utils import resolve_agent_restaurant_and_user


# ---------------------------------------------------------------------------
# Agent read-through caches (keeps Miya's polling off the DB).
#
# TTLs are short (30s / 45s) because these feeds drive manager UX
# on WhatsApp — we want approvals/rejections/assignments to appear
# quickly, and we bust the cache explicitly on every mutating call
# below. The TTL is only the fallback for writes we don't invalidate
# (e.g. admin console edits, migrations) and for crash-loop safety.
# ---------------------------------------------------------------------------

_REQUESTS_CACHE_TTL = 30
_INCIDENTS_CACHE_TTL = 45


def _staff_requests_cache_key(restaurant_id, status_filter: str) -> str:
    return f"agent:staff:requests:v1:{restaurant_id}:{(status_filter or 'PENDING').upper()}"


def _staff_incidents_cache_key(restaurant_id, status_filter: str) -> str:
    return f"agent:staff:incidents:v1:{restaurant_id}:{(status_filter or 'OPEN').upper()}"


def _invalidate_staff_requests_cache(restaurant_id) -> None:
    """Bust every status-slice of the staff-requests feed for this tenant.

    We don't know which status the caller was looking at (the list view
    accepts ``?status=PENDING|APPROVED|REJECTED|ALL``), so we wipe all
    slices. Each delete is best-effort so a Redis hiccup can never turn
    a successful write into a 500.
    """
    for sf in ("PENDING", "APPROVED", "REJECTED", "ALL"):
        safe_cache_delete(_staff_requests_cache_key(restaurant_id, sf))


def _invalidate_staff_incidents_cache(restaurant_id) -> None:
    for sf in ("OPEN", "RESOLVED", "UNDER_REVIEW", "ESCALATED"):
        safe_cache_delete(_staff_incidents_cache_key(restaurant_id, sf))


def _resolve_restaurant_and_staff_by_phone(phone_raw, *, exclude_super_admin=True):
    """Resolve (restaurant, staff) from phone when agent sends phone but not restaurant_id.

    By default SUPER_ADMIN rows are skipped so WhatsApp-style resolution
    prefers tenant staff. Dashboard agent flows call again with
    ``exclude_super_admin=False`` when no staff match so platform admins
    with only a phone on file can still be resolved.
    """
    from accounts.services import resolve_restaurant_and_staff_by_phone

    return resolve_restaurant_and_staff_by_phone(
        phone_raw, exclude_super_admin=exclude_super_admin
    )
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
from dashboard.category_routing import (
    category_lane_hint,
    ensure_dashboard_widgets_for_managers,
    primary_widget_for_category,
    widget_lane_label,
)

logger = logging.getLogger(__name__)


# Canonical values for StaffRequest.category — kept in one place so agent
# ingest, the manager API, and the frontend all validate against the same
# list. Must match ``StaffRequest.CATEGORY_CHOICES``.
STAFF_REQUEST_CATEGORIES = (
    'DOCUMENT', 'HR', 'SCHEDULING', 'PAYROLL', 'FINANCE', 'OPERATIONS',
    'MAINTENANCE', 'RESERVATIONS', 'INVENTORY', 'PURCHASE_ORDER', 'MEDICAL', 'OTHER',
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
        'MEDICAL_SERVICE': 'MEDICAL',
        'MEDICAL_SERVICES': 'MEDICAL',
        'HEALTH': 'MEDICAL',
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


def _coerce_bool(val, default=True):
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    return str(val).lower() not in ('false', '0', 'no', 'off')


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
        widget_pin = ensure_dashboard_widgets_for_managers(
            restaurant, incident=True
        )
        return Response({
            'success': True,
            'routed': True,
            'destination': 'INCIDENT',
            'incident_id': str(concern.id),
            'incident_type': decision.category,
            'priority': incident_priority,
            'matched_terms': list(decision.matched_terms),
            'dashboard_widget': 'incidents',
            'widget_label': widget_lane_label('incidents'),
            'widgets_pinned': widget_pin.get('widgets', []),
            'message_for_user': (
                f"This sounded like a {decision.category.lower()} incident "
                f"({', '.join(decision.matched_terms[:3]) or 'safety signal'}), "
                "so I logged it on the Reported Incidents board instead of the inbox."
            ),
            'message_for_staff': (
                "I've reported this to management right away. "
                "Someone will follow up with you as soon as possible."
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

    follow_up_enabled = _coerce_bool(
        data.get('follow_up_enabled') or data.get('followUpEnabled'),
        default=True,
    )
    follow_up_max = int(data.get('follow_up_max') or data.get('followUpMax') or 2)
    follow_up_max = max(0, min(3, follow_up_max))

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
        follow_up_enabled=follow_up_enabled,
        follow_up_max=follow_up_max,
    )
    _invalidate_staff_requests_cache(restaurant.id)

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
                if wa_ok:
                    req.whatsapp_notified_at = timezone.now()
                    req.save(update_fields=['whatsapp_notified_at', 'updated_at'])
            except Exception as exc:
                logger.warning("StaffRequest assignee WhatsApp ping failed: %s", exc)
                whatsapp_sent_to_assignee = False

    _notify_managers_of_staff_request(req)

    widget_pin = ensure_dashboard_widgets_for_managers(restaurant, category=category)
    primary_widget = primary_widget_for_category(category)
    lane_label = widget_lane_label(primary_widget)

    ref = _short_ref(req.id)
    base_msg = (
        f"Logged in the {lane_label} lane (#{ref})."
        if req.category != 'OTHER'
        else f"Logged in Miscellaneous (#{ref}) — a manager will triage it."
    )
    staff_base_msg = (
        "Thanks — I've logged your request and let the right person know."
        if req.category != 'OTHER'
        else "Thanks — I've logged your request. A manager will review it soon."
    )
    follow_phrase = ""
    if assignee and follow_up_enabled and whatsapp_sent_to_assignee:
        follow_phrase = " I'll follow up automatically on WhatsApp if they don't respond."
    elif assignee and follow_up_enabled and not whatsapp_sent_to_assignee:
        follow_phrase = " Automatic WhatsApp follow-ups are enabled once they're reachable on WhatsApp."

    return Response({
        'success': True,
        'routed': True,
        'destination': 'INBOX',
        'id': str(req.id),
        'record_id': str(req.id),
        'task_ref': ref,
        'status': req.status,
        'category': req.category,
        'auto_categorised': inbox_metadata['intent_router']['auto_categorised'],
        'matched_terms': list(decision.matched_terms),
        'dashboard_widget': primary_widget,
        'widget_label': lane_label,
        'inbox_lane': primary_widget,
        'dashboard_hint': category_lane_hint(category),
        'widgets_pinned': widget_pin.get('widgets', []),
        'message_for_user': f"✓ {base_msg}{follow_phrase}",
        'message_for_staff': (
            f"✓ {staff_base_msg} They'll get back to you as soon as they can."
        ),
        'follow_up_enabled': follow_up_enabled,
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
    cache_key = _staff_requests_cache_key(restaurant.id, status_filter)

    def _compute_requests_payload():
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
        return {'success': True, 'requests': items, 'restaurant_id': str(restaurant.id)}

    return Response(get_or_set(cache_key, _REQUESTS_CACHE_TTL, _compute_requests_payload))


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
    _invalidate_staff_requests_cache(restaurant.id)
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
    _invalidate_staff_requests_cache(restaurant.id)
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
    _invalidate_staff_requests_cache(restaurant.id)

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
    raw_status = (request.query_params.get('status') or 'OPEN').strip().upper()
    status_tokens = [s.strip() for s in raw_status.split(',') if s.strip()]
    # Cache key uses the raw (sorted) status string so 'OPEN,UNDER_REVIEW'
    # and 'UNDER_REVIEW,OPEN' share a slot. The invalidator below clears
    # every common single-status slice — multi-status slices cost one
    # extra DB hit once per TTL, which is fine.
    cache_key = _staff_incidents_cache_key(restaurant.id, ",".join(sorted(status_tokens)))

    def _compute_incidents_payload():
        qs = SafetyConcernReport.objects.filter(
            restaurant=restaurant,
            status__in=status_tokens,
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
        return {'success': True, 'incidents': items, 'restaurant_id': str(restaurant.id)}

    return Response(get_or_set(cache_key, _INCIDENTS_CACHE_TTL, _compute_incidents_payload))


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
    _invalidate_staff_incidents_cache(restaurant.id)
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
    _invalidate_staff_incidents_cache(restaurant.id)
    return Response({'success': True, 'message': 'Incident escalated.', 'incident_id': str(inc.id)})


def _short_ref(record_id) -> str:
    digits = str(record_id or '').replace('-', '')
    return (digits[-8:] if len(digits) >= 8 else digits).upper()


_CATEGORY_LANE_HINT = {
    cat: category_lane_hint(cat)
    for cat in STAFF_REQUEST_CATEGORIES
}


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_search_operational_records(request):
    """
    Search staff requests, dashboard tasks, and invoices by ref tail, id fragment,
    external_id, invoice number, or subject keywords.

    GET /api/staff/agent/records/search/?restaurant_id=...&q=33931578
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])

    raw_q = (request.query_params.get('q') or request.query_params.get('query') or '').strip()
    if len(raw_q) < 2:
        return Response(
            {'success': False, 'error': 'Query too short', 'message_for_user': 'Tell me the reference number or a few keywords.'},
            status=status.HTTP_400_BAD_REQUEST,
        )

    q_lower = raw_q.lower()
    q_hex = ''.join(ch for ch in raw_q.lower() if ch in '0123456789abcdef')
    matches = []

    # Staff requests
    req_qs = StaffRequest.objects.filter(restaurant=restaurant).order_by('-created_at')[:200]
    for req in req_qs:
        rid = str(req.id)
        ref = _short_ref(req.id)
        hay = ' '.join(
            filter(
                None,
                [
                    rid,
                    ref,
                    req.external_id or '',
                    req.subject or '',
                    req.description or '',
                    req.category or '',
                ],
            )
        ).lower()
        if (
            q_lower in hay
            or (len(q_hex) >= 6 and q_hex in rid.replace('-', ''))
            or (q_lower and ref.lower() == q_lower)
        ):
            matches.append(
                {
                    'type': 'staff_request',
                    'id': rid,
                    'ref': ref,
                    'subject': req.subject or '',
                    'title': req.subject or '',
                    'category': req.category,
                    'status': req.status,
                    'created_at': req.created_at.isoformat() if req.created_at else None,
                    'dashboard_hint': _CATEGORY_LANE_HINT.get(req.category, 'All Requests'),
                    'lane': req.category,
                }
            )

    # Dashboard tasks (Tasks & Demands / Operations tasks)
    try:
        from dashboard.models import Task

        task_qs = Task.objects.filter(restaurant=restaurant).order_by('-created_at')[:200]
        for task in task_qs:
            tid = str(task.id)
            ref = _short_ref(task.id)
            hay = ' '.join(
                filter(None, [tid, ref, task.title or '', task.description or '', task.category or ''])
            ).lower()
            if (
                q_lower in hay
                or (len(q_hex) >= 6 and q_hex in tid.replace('-', ''))
                or (q_lower and ref.lower() == q_lower)
            ):
                cat = (task.category or 'OTHER').upper()
                hint = _CATEGORY_LANE_HINT.get(cat, 'Tasks & Demands widget')
                if cat == 'OPERATIONS':
                    hint = 'Operations tasks widget (?lane=operations_tasks) or Tasks & Demands'
                matches.append(
                    {
                        'type': 'dashboard_task',
                        'id': tid,
                        'ref': ref,
                        'title': task.title or '',
                        'subject': task.title or '',
                        'category': cat,
                        'status': task.status,
                        'due_date': task.due_date.isoformat() if task.due_date else None,
                        'created_at': task.created_at.isoformat() if getattr(task, 'created_at', None) else None,
                        'dashboard_hint': hint,
                        'lane': cat,
                    }
                )
    except Exception as exc:
        logger.warning('agent_search_operational_records task search failed: %s', exc)

    # Finance invoices (by invoice number)
    try:
        from finance.models import Invoice

        inv_qs = Invoice.objects.filter(restaurant=restaurant).order_by('-created_at')[:100]
        for inv in inv_qs:
            if q_lower in (inv.invoice_number or '').lower() or q_lower in (inv.vendor_name or '').lower():
                matches.append(
                    {
                        'type': 'invoice',
                        'id': str(inv.id),
                        'ref': (inv.invoice_number or _short_ref(inv.id)),
                        'title': f'{inv.vendor_name} invoice #{inv.invoice_number or "?"}',
                        'subject': inv.vendor_name or '',
                        'category': 'FINANCE',
                        'status': inv.status,
                        'amount': str(inv.amount),
                        'currency': inv.currency,
                        'due_date': inv.due_date.isoformat() if inv.due_date else None,
                        'dashboard_hint': 'Finance widget (?lane=finance)',
                        'lane': 'FINANCE',
                    }
                )
    except Exception as exc:
        logger.warning('agent_search_operational_records invoice search failed: %s', exc)

    # De-dupe and cap
    seen = set()
    deduped = []
    for m in matches:
        key = (m['type'], m['id'])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(m)
        if len(deduped) >= 10:
            break

    return Response(
        {
            'success': True,
            'query': raw_q,
            'count': len(deduped),
            'matches': deduped,
            'message_for_user': (
                f'Found {len(deduped)} match(es) for "{raw_q}".'
                if deduped
                else f'Nothing found for "{raw_q}".'
            ),
        }
    )


def _find_operational_record(restaurant, *, record_id=None, record_type=None, query=None):
    """Resolve a staff request or dashboard task for chase / follow-up."""
    from dashboard.models import Task

    rid = (record_id or '').strip()
    rtype = (record_type or '').strip().lower()
    q = (query or '').strip()

    if rid:
        if rtype in ('', 'staff_request', 'request'):
            req = StaffRequest.objects.filter(restaurant=restaurant, id=rid).select_related('assignee').first()
            if req:
                return 'staff_request', req
        if rtype in ('', 'dashboard_task', 'task'):
            task = Task.objects.filter(restaurant=restaurant, id=rid).select_related('assigned_to').first()
            if task:
                return 'dashboard_task', task
        req = StaffRequest.objects.filter(restaurant=restaurant, id=rid).select_related('assignee').first()
        if req:
            return 'staff_request', req
        task = Task.objects.filter(restaurant=restaurant, id=rid).select_related('assigned_to').first()
        if task:
            return 'dashboard_task', task

    if not q or len(q) < 2:
        return None, None

    q_lower = q.lower()
    q_hex = ''.join(ch for ch in q.lower() if ch in '0123456789abcdef')

    for req in StaffRequest.objects.filter(restaurant=restaurant).order_by('-created_at')[:100]:
        hay = ' '.join(
            filter(None, [str(req.id), _short_ref(req.id), req.subject or '', req.description or ''])
        ).lower()
        if q_lower in hay or (len(q_hex) >= 6 and q_hex in str(req.id).replace('-', '')):
            return 'staff_request', req

    for task in Task.objects.filter(restaurant=restaurant).order_by('-created_at')[:100]:
        hay = ' '.join(filter(None, [str(task.id), _short_ref(task.id), task.title or ''])).lower()
        if q_lower in hay or (len(q_hex) >= 6 and q_hex in str(task.id).replace('-', '')):
            return 'dashboard_task', task

    return None, None


@api_view(['POST'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_chase_operational_record(request):
    """
    Send an immediate WhatsApp follow-up for a pending staff request or task.

    POST /api/staff/agent/records/chase/
    Body: restaurant_id, q | record_id, optional record_type
    """
    is_valid, error = validate_agent_key(request)
    if not is_valid:
        return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)

    restaurant, err = _resolve_restaurant_for_staff_agent(request)
    if err:
        return Response({'success': False, 'error': err['error']}, status=err['status'])

    data = request.data or {}
    record_type, record = _find_operational_record(
        restaurant,
        record_id=data.get('record_id') or data.get('recordId') or data.get('id'),
        record_type=data.get('record_type') or data.get('recordType') or data.get('type'),
        query=data.get('q') or data.get('query'),
    )
    if not record:
        return Response(
            {
                'success': False,
                'error': 'Record not found',
                'message_for_user': "I couldn't find that request or task. Give me the reference number or a few keywords.",
            },
            status=status.HTTP_404_NOT_FOUND,
        )

    from staff.follow_up_helpers import (
        build_staff_request_follow_up_message,
        build_task_follow_up_message,
        normalize_phone,
    )

    now = timezone.now()
    whatsapp_sent = False
    assignee_name = ''
    title = ''
    ref = _short_ref(record.id)

    if record_type == 'staff_request':
        if record.status not in ('PENDING', 'ESCALATED'):
            return Response(
                {
                    'success': False,
                    'message_for_user': f"Request #{ref} is already {record.status.lower()} — no follow-up needed.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        assignee = record.assignee
        if not assignee:
            return Response(
                {
                    'success': False,
                    'message_for_user': f"Request #{ref} has no assignee yet — assign someone first.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        phone = normalize_phone(getattr(assignee, 'phone', None))
        if not phone:
            return Response(
                {
                    'success': False,
                    'message_for_user': f"{assignee.get_full_name() or 'The assignee'} has no phone on file.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        message = build_staff_request_follow_up_message(record, record.follow_up_count + 1)
        ok, _ = notification_service.send_whatsapp_text(phone, message)
        whatsapp_sent = bool(ok)
        if ok:
            record.follow_up_count += 1
            record.last_follow_up_at = now
            if not record.whatsapp_notified_at:
                record.whatsapp_notified_at = now
            record.follow_up_enabled = True
            record.save(
                update_fields=[
                    'follow_up_count',
                    'last_follow_up_at',
                    'whatsapp_notified_at',
                    'follow_up_enabled',
                    'updated_at',
                ]
            )
            StaffRequestComment.objects.create(
                request=record,
                author=None,
                kind='system',
                body=f"📲 Manual WhatsApp follow-up #{record.follow_up_count} sent via Miya.",
                metadata={'trigger': 'agent_chase'},
            )
        assignee_name = assignee.get_full_name() or assignee.email or 'Assignee'
        title = record.subject or 'Request'
    else:
        if record.status != 'PENDING':
            return Response(
                {
                    'success': False,
                    'message_for_user': f"Task #{ref} is already {record.status.lower()} — no follow-up needed.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        assignee = record.assigned_to
        if not assignee:
            return Response(
                {
                    'success': False,
                    'message_for_user': f"Task #{ref} has no assignee yet.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        phone = normalize_phone(getattr(assignee, 'phone', None))
        if not phone:
            return Response(
                {
                    'success': False,
                    'message_for_user': f"{assignee.get_full_name() or 'The assignee'} has no phone on file.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        message = build_task_follow_up_message(record, record.follow_up_count + 1)
        ok, _ = notification_service.send_whatsapp_text(phone, message)
        whatsapp_sent = bool(ok)
        if ok:
            record.follow_up_count += 1
            record.last_follow_up_at = now
            if not record.whatsapp_notified_at:
                record.whatsapp_notified_at = now
            record.follow_up_enabled = True
            record.save(
                update_fields=[
                    'follow_up_count',
                    'last_follow_up_at',
                    'whatsapp_notified_at',
                    'follow_up_enabled',
                    'updated_at',
                ]
            )
        assignee_name = assignee.get_full_name() or assignee.email or 'Assignee'
        title = record.title or 'Task'

    if not whatsapp_sent:
        return Response(
            {
                'success': False,
                'message_for_user': f"I couldn't reach {assignee_name} on WhatsApp right now. They'll still see it in the app.",
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(
        {
            'success': True,
            'record_type': record_type,
            'record_id': str(record.id),
            'ref': ref,
            'whatsapp_sent': True,
            'follow_up_count': record.follow_up_count,
            'message_for_user': (
                f"✓ Follow-up sent on WhatsApp to {assignee_name} about "
                f"\"{title}\" (#{ref}). I'll keep chasing automatically if it stays pending."
            ),
        }
    )

