from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from .services import notification_service
import logging

logger = logging.getLogger(__name__)
User = get_user_model()


def _validate_agent_key(request):
    auth_header = request.headers.get("Authorization")
    expected = getattr(dj_settings, "LUA_WEBHOOK_API_KEY", None)
    if not expected:
        return False, Response(
            {"success": False, "error": "Agent key not configured"},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
    if not auth_header or auth_header != f"Bearer {expected}":
        return False, Response(
            {"success": False, "error": "Unauthorized"},
            status=status.HTTP_401_UNAUTHORIZED,
        )
    return True, None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_send_announcement(request):
    """
    Miya/Lua endpoint: manager sends an announcement from the chat widget.
    Request body:
      - restaurant_id (required): UUID of the restaurant.
      - message (required): Announcement text (e.g. "No work tomorrow due to public holiday").
      - title (optional): Short title; default "Announcement".
      - audience (optional): "all" (default) or dict with any of:
          staff_ids: list of user UUIDs
          roles: list of role names (e.g. ["CHEF", "WAITER"])
          departments: list of department names
      - sender_id (optional): UUID of the manager who sent it (for attribution).
    Sends in-app + WhatsApp to the selected staff.
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    data = request.data or {}
    restaurant_id = data.get("restaurant_id")
    message = (data.get("message") or "").strip()
    if not restaurant_id or not message:
        return Response(
            {"success": False, "error": "restaurant_id and message are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    title = (data.get("title") or "Announcement").strip() or "Announcement"
    audience = data.get("audience")
    sender = None
    sender_id = data.get("sender_id")
    if sender_id:
        try:
            sender = User.objects.get(id=sender_id)
        except User.DoesNotExist:
            pass

    staff_ids = None
    roles = None
    departments = None
    if isinstance(audience, dict):
        staff_ids = audience.get("staff_ids") or None
        roles = audience.get("roles") or None
        departments = audience.get("departments") or None
    # "all" or missing audience => no filters (staff_ids, roles, departments stay None)

    try:
        success, count, err, details = notification_service.send_announcement_to_audience(
            restaurant_id=str(restaurant_id),
            title=title,
            message=message,
            sender=sender,
            staff_ids=staff_ids,
            roles=roles,
            departments=departments,
            channels=["app", "whatsapp"],
        )
        if not success:
            return Response(
                {"success": False, "error": err or "Send failed", "notification_count": count},
                status=status.HTTP_400_BAD_REQUEST,
            )
        whatsapp_sent = details.get("whatsapp_sent", count)
        recipients_without_phone = details.get("recipients_without_phone") or []
        # When staff don't use the app, WhatsApp is the only way to reach them; surface when we couldn't send WhatsApp.
        if recipients_without_phone:
            names = [r.get("full_name") or r.get("id", "") for r in recipients_without_phone]
            message_text = (
                f"Announcement sent to {count} recipient(s) (WhatsApp: {whatsapp_sent}). "
                f"The following have no phone number on file, so they only received an in-app message: {', '.join(names)}. "
                "If your team doesn't use the app, add their phone numbers so Miya can reach them by WhatsApp."
            )
        else:
            message_text = f"Announcement sent to {count} recipient(s) via app and WhatsApp."
        return Response(
            {
                "success": True,
                "message": message_text,
                "notification_count": count,
                "whatsapp_sent": whatsapp_sent,
                "recipients_without_phone": recipients_without_phone,
            },
            status=status.HTTP_200_OK,
        )
    except Exception as e:
        logger.exception("agent_send_announcement error: %s", e)
        return Response(
            {"success": False, "error": str(e)},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(['POST'])
@authentication_classes([]) # Bypass global JWT authentication
@permission_classes([AllowAny]) # Authenticated via Agent Key manually in the view
def send_whatsapp_from_agent(request):
    """
    Endpoint for Lua Agent to send WhatsApp messages/templates via the backend.
    """
    logger.info(f"Incoming WhatsApp request from agent. Type: {request.data.get('type', 'text')}")
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(dj_settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
             return Response({'success': False, 'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
             return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
             
        phone = request.data.get('phone')
        type = request.data.get('type', 'text')
        
        if not phone:
             return Response({'success': False, 'error': 'Phone required'}, status=status.HTTP_400_BAD_REQUEST)
             
        if type == 'template':
            template_name = request.data.get('template_name')
            language_code = request.data.get('language_code', 'en')
            components = request.data.get('components', [])
            
            if not template_name:
                return Response({'success': False, 'error': 'Template name required'}, status=status.HTTP_400_BAD_REQUEST)
                
            ok, resp = notification_service.send_whatsapp_template(phone, template_name, language_code, components)
            return Response({'success': ok, 'provider_response': resp})
            
        elif type == 'text':
            body = request.data.get('body')
            if not body:
                return Response({'success': False, 'error': 'Body required'}, status=status.HTTP_400_BAD_REQUEST)
                
            ok, resp = notification_service.send_whatsapp_text(phone, body)
            logger.info(f"WhatsApp text sent: {ok}")
            return Response({'success': ok, 'provider_response': resp})
            
        else:
             return Response({'success': False, 'error': 'Invalid type'}, status=status.HTTP_400_BAD_REQUEST)
             
    except Exception as e:
        logger.error(f"Agent WhatsApp send error: {e}")
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


def _resolve_staff_and_shift(request_data):
    """
    Shared helper: resolve staff user and today's active shift from phone.
    Returns (user, shift, clean_phone, error_response).
    If error_response is not None, return it immediately.
    """
    from accounts.services import _find_active_user_by_phone
    from notifications.views import _get_shift_for_checklist
    from django.utils import timezone

    phone = (request_data.get("phone") or request_data.get("phoneNumber") or "").strip()
    clean_phone = "".join(filter(str.isdigit, str(phone)))
    if not clean_phone or len(clean_phone) < 6:
        return None, None, clean_phone, Response(
            {"success": False, "error": "Invalid or missing phone",
             "message_for_user": "I couldn't find your account. Please make sure you're messaging from the number we have on file."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    user = _find_active_user_by_phone(clean_phone)
    if not user:
        logger.warning("agent_checklist: staff not found for phone %s", clean_phone)
        return None, None, clean_phone, Response(
            {"success": False, "error": "Staff not found",
             "message_for_user": "We couldn't find your account. Please contact your manager to be added."},
            status=status.HTTP_404_NOT_FOUND,
        )

    active_shift = _get_shift_for_checklist(user)
    if not active_shift:
        logger.warning("agent_checklist: no active shift for user %s (phone %s) on %s", user.id, clean_phone, timezone.now().date())
        return user, None, clean_phone, Response(
            {"success": False, "error": "No shift",
             "message_for_user": "You do not have a scheduled shift at this time. If you believe this is wrong, please ask your manager to check your shift assignment."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return user, active_shift, clean_phone, None


def _is_staff_clocked_in(user):
    """Check if the staff member is currently clocked in (last event is 'in')."""
    from timeclock.models import ClockEvent
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    return last_event is not None and last_event.event_type == 'in'


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_preview_checklist(request):
    """
    Miya/Lua endpoint: preview the checklist items for a staff member's upcoming/current shift.
    Staff do NOT need to be clocked in. Returns the list of checklist items so they can prepare.
    Request body: phone (required).
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    user, active_shift, clean_phone, err = _resolve_staff_and_shift(request.data or {})
    if err:
        return err

    from checklists.models import ChecklistTemplate

    task_templates = active_shift.task_templates.filter(is_active=True)
    checklist_templates = ChecklistTemplate.objects.filter(
        task_template__in=task_templates, is_active=True
    ).prefetch_related('steps').distinct()

    if not checklist_templates.exists() and task_templates.exists():
        task_types = task_templates.values_list('template_type', flat=True)
        checklist_templates = ChecklistTemplate.objects.filter(
            restaurant=user.restaurant, is_active=True, category__in=task_types
        ).prefetch_related('steps').distinct()

    from django.utils import timezone as tz
    shift_start = tz.localtime(active_shift.start_time).strftime('%H:%M') if active_shift.start_time else None
    shift_end = tz.localtime(active_shift.end_time).strftime('%H:%M') if active_shift.end_time else None

    checklists_data = []
    for tpl in checklist_templates:
        steps = tpl.steps.all().order_by('order')
        checklists_data.append({
            'name': tpl.name,
            'category': tpl.category,
            'total_steps': steps.count(),
            'estimated_duration_minutes': int(tpl.estimated_duration.total_seconds() / 60) if tpl.estimated_duration else None,
            'steps': [{'order': s.order, 'title': s.title, 'requires_photo': s.requires_photo} for s in steps],
        })

    clocked_in = _is_staff_clocked_in(user)

    if not checklists_data:
        return Response({
            "success": True,
            "mode": "preview",
            "clocked_in": clocked_in,
            "shift": {"start": shift_start, "end": shift_end},
            "checklists": [],
            "message_for_user": "No checklists are assigned to your shift right now. You're all set!",
        })

    total_items = sum(c['total_steps'] for c in checklists_data)
    return Response({
        "success": True,
        "mode": "preview",
        "clocked_in": clocked_in,
        "shift": {"start": shift_start, "end": shift_end},
        "checklists": checklists_data,
        "total_items": total_items,
        "message_for_user": (
            f"Your shift ({shift_start} – {shift_end}) has {len(checklists_data)} checklist(s) "
            f"with {total_items} item(s) total."
        ),
    })


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_start_whatsapp_checklist(request):
    """
    Miya/Lua endpoint: start the step-by-step WhatsApp checklist for a staff member by phone.
    Staff MUST be clocked in. If not, returns a message telling them to clock in first.
    Request body: phone (required).
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    user, active_shift, clean_phone, err = _resolve_staff_and_shift(request.data or {})
    if err:
        return err

    if not _is_staff_clocked_in(user):
        return Response(
            {
                "success": False,
                "error": "Not clocked in",
                "clocked_in": False,
                "message_for_user": "You need to clock in before starting your checklist. Please clock in first, then ask me to start your checklist.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    try:
        started = notification_service.start_conversational_checklist_after_clock_in(
            user, active_shift, phone_digits=clean_phone
        )
    except Exception as e:
        logger.exception("agent_start_whatsapp_checklist failed for user %s: %s", user.id, e)
        return Response(
            {"success": False, "error": str(e),
             "message_for_user": "I'm having trouble loading your checklist. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if started:
        return Response(
            {
                "success": True,
                "first_item_sent": True,
                "clocked_in": True,
                "message_for_user": "Your checklist has been started! You should receive the first task now.",
            },
            status=status.HTTP_200_OK,
        )

    return Response(
        {"success": False, "error": "No checklist items",
         "message_for_user": "No checklist is assigned to your shift right now. You're all set!"},
        status=status.HTTP_400_BAD_REQUEST,
    )
