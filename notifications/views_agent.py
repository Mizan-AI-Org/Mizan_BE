from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework import status
from django.conf import settings as dj_settings
from django.contrib.auth import get_user_model
from django.utils import timezone
from .services import notification_service
from .order_parsing import merge_parsed_order_fields
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
        recipients_whatsapp_failed = details.get("recipients_whatsapp_failed") or []
        # When staff don't use the app, WhatsApp is the only way to reach them; surface when we couldn't send WhatsApp.
        if recipients_whatsapp_failed:
            names = [r.get("full_name") or r.get("id", "") for r in recipients_whatsapp_failed]
            return Response(
                {
                    "success": False,
                    "error": (
                        f"Message sent in-app to {count} recipient(s), but WhatsApp delivery failed for: {', '.join(names)}. "
                        "Most often this is a phone-number format issue: Miya needs the number as country code + subscriber number, "
                        "digits only, no '+' and no leading zero — e.g. 212622286214 (Morocco), 2203736808 (Gambia), 254722286214 (Kenya). "
                        "Open Staff → that person's profile and re-save the WhatsApp number in that format. "
                        "If the number is already correct, check WhatsApp Business API settings (access token + phone number ID)."
                    ),
                    "notification_count": count,
                    "whatsapp_sent": whatsapp_sent,
                    "recipients_whatsapp_failed": recipients_whatsapp_failed,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if recipients_without_phone:
            names = [r.get("full_name") or r.get("id", "") for r in recipients_without_phone]
            message_text = (
                f"Announcement sent to {count} recipient(s) (WhatsApp: {whatsapp_sent}). "
                f"The following have no phone number on file, so they only received an in-app message: {', '.join(names)}. "
                "Add their WhatsApp number in this format so Miya can reach them next time: country code + subscriber number, "
                "digits only, no '+' and no leading zero (e.g. 212622286214, 2203736808, 254722286214)."
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


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_create_staff_captured_order(request):
    """
    Miya/Lua: create a staff-captured order for Today's Orders when the agent has the transcript
    (e.g. parallel WhatsApp routing). Auth: Bearer LUA_WEBHOOK_API_KEY.

    Body (JSON):
      - restaurant_id (required): UUID
      - items_summary or transcript (required): order text
      - user_id (optional): staff user UUID at that restaurant
      - phone / staff_phone (optional): if user_id omitted, resolve staff by phone digits
      - channel (optional): VOICE | TEXT | MANUAL (default VOICE)
      - customer_name, customer_phone, order_type, table_or_location (optional)
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    from dashboard.models import StaffCapturedOrder
    from accounts.models import Restaurant

    data = request.data or {}
    restaurant_id = data.get("restaurant_id")
    items_summary = (data.get("items_summary") or data.get("transcript") or "").strip()
    if not restaurant_id or not items_summary:
        return Response(
            {"success": False, "error": "restaurant_id and items_summary (or transcript) are required"},
            status=status.HTTP_400_BAD_REQUEST,
        )
    try:
        restaurant = Restaurant.objects.get(id=restaurant_id)
    except Restaurant.DoesNotExist:
        return Response({"success": False, "error": "restaurant not found"}, status=status.HTTP_404_NOT_FOUND)

    user = None
    uid = data.get("user_id") or data.get("staff_id")
    if uid:
        try:
            user = User.objects.get(id=uid, restaurant_id=restaurant.id)
        except User.DoesNotExist:
            return Response(
                {"success": False, "error": "user not found for this restaurant"},
                status=status.HTTP_404_NOT_FOUND,
            )
    else:
        phone = (data.get("phone") or data.get("staff_phone") or "").strip()
        digits = "".join(filter(str.isdigit, str(phone)))
        if len(digits) >= 9:
            user = User.objects.filter(
                restaurant_id=restaurant.id,
                phone__isnull=False,
            ).filter(phone__icontains=digits[-9:]).first()
        if not user:
            return Response(
                {
                    "success": False,
                    "error": "Provide user_id or staff_phone to attribute the order capture",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

    ch = (data.get("channel") or "VOICE").upper()
    if ch not in ("VOICE", "TEXT", "MANUAL"):
        ch = "VOICE"

    overrides = {}
    for key in (
        "customer_name",
        "customer_phone",
        "order_type",
        "table_or_location",
        "dietary_notes",
        "special_instructions",
    ):
        val = data.get(key)
        if val is not None and str(val).strip():
            overrides[key] = val
    if data.get("items_summary") and str(data.get("items_summary")).strip():
        overrides["items_summary"] = str(data.get("items_summary")).strip()

    try:
        merged = merge_parsed_order_fields(items_summary, overrides)
        merged["channel"] = ch
        order = StaffCapturedOrder.objects.create(
            restaurant=restaurant,
            recorded_by=user,
            **merged,
        )
        try:
            notification_service.send_lua_staff_captured_order(user, order, items_summary[:2000])
        except Exception:
            logger.exception("agent_create_staff_captured_order: Lua notify failed (non-fatal)")
    except Exception as e:
        logger.exception("agent_create_staff_captured_order: %s", e)
        return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(
        {
            "success": True,
            "order_id": str(order.id),
            "short_id": str(order.id)[:8],
        },
        status=status.HTTP_201_CREATED,
    )


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
        today = timezone.localdate()
        from scheduling.models import AssignedShift
        from django.db.models import Q
        any_shifts = AssignedShift.objects.filter(
            Q(staff=user) | Q(staff_members=user),
            shift_date=today,
        ).values_list('id', 'status', 'start_time', 'end_time')
        logger.warning(
            "agent_checklist: no active shift for user %s (%s %s, phone %s) on %s. "
            "All shifts today: %s",
            user.id, user.first_name, user.last_name, clean_phone, today, list(any_shifts),
        )
        return user, None, clean_phone, Response(
            {"success": False, "error": "No shift",
             "message_for_user": "You do not have a scheduled shift at this time. If you believe this is wrong, please ask your manager to check your shift assignment."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return user, active_shift, clean_phone, None


def _is_staff_clocked_in(user):
    """Check if the staff member is currently clocked in (today's last event is 'in' or 'break_start'/'break_end')."""
    from timeclock.models import ClockEvent
    import datetime as _dt
    today = timezone.localdate()
    today_start = timezone.make_aware(_dt.datetime.combine(today, _dt.time.min))
    last_event = ClockEvent.objects.filter(
        staff=user, timestamp__gte=today_start
    ).order_by('-timestamp').first()
    if not last_event:
        return False
    return last_event.event_type in ('in', 'break_start', 'break_end')


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_preview_checklist(request):
    """
    Miya/Lua endpoint: preview OR auto-start the checklist for a staff member's shift.
    - If staff is clocked in: automatically starts the conversational checklist
      (tasks sent one-by-one via WhatsApp) so progress is recorded on the Live Board.
    - If staff is NOT clocked in: returns a preview of the tasks and asks them to clock in.
    Includes both process/template tasks AND custom ShiftTasks.
    Request body: phone (required).
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    user, active_shift, clean_phone, err = _resolve_staff_and_shift(request.data or {})
    if err:
        return err

    from scheduling.models import ShiftTask, ShiftChecklistProgress
    from django.utils import timezone as tz

    shift_start = tz.localtime(active_shift.start_time).strftime('%H:%M') if active_shift.start_time else None
    shift_end = tz.localtime(active_shift.end_time).strftime('%H:%M') if active_shift.end_time else None
    clocked_in = _is_staff_clocked_in(user)

    # Build the task list for the response (process template tasks + custom ShiftTasks)
    all_items = _collect_shift_task_items(active_shift)

    if not all_items:
        return Response({
            "success": True,
            "mode": "preview",
            "clocked_in": clocked_in,
            "shift": {"start": shift_start, "end": shift_end},
            "tasks": [],
            "total_items": 0,
            "message_for_user": "No tasks or checklists are assigned to your shift right now. You're all set!",
        })

    # If staff is clocked in, auto-start the conversational checklist
    if clocked_in:
        existing_prog = ShiftChecklistProgress.objects.filter(
            shift=active_shift, staff=user
        ).first()

        if existing_prog and existing_prog.status == 'COMPLETED':
            return Response({
                "success": True,
                "mode": "completed",
                "clocked_in": True,
                "shift": {"start": shift_start, "end": shift_end},
                "tasks": all_items,
                "total_items": len(all_items),
                "message_for_user": "Your checklist is already complete. Great work!",
            })

        if existing_prog and existing_prog.status == 'IN_PROGRESS':
            notification_service.resume_conversational_checklist(
                user, active_shift, phone_digits=clean_phone
            )
            return Response({
                "success": True,
                "mode": "in_progress",
                "first_item_sent": True,
                "suppress_reply": True,
                "clocked_in": True,
                "shift": {"start": shift_start, "end": shift_end},
                "tasks": all_items,
                "total_items": len(all_items),
            })

        try:
            started = notification_service.start_conversational_checklist_after_clock_in(
                user, active_shift, phone_digits=clean_phone
            )
        except Exception as e:
            logger.exception("agent_preview_checklist auto-start failed for user %s: %s", user.id, e)
            started = False

        if started is True:
            return Response({
                "success": True,
                "mode": "started",
                "first_item_sent": True,
                "suppress_reply": True,
                "clocked_in": True,
                "shift": {"start": shift_start, "end": shift_end},
                "tasks": all_items,
                "total_items": len(all_items),
            })

        if started is False:
            # WhatsApp delivery failed; return the task list so Miya can relay it
            task_list_text = "\n".join(f"{i+1}. *{item['title']}*" for i, item in enumerate(all_items))
            logger.warning("agent_preview_checklist: WhatsApp delivery failed, returning task text for Miya (phone=%s)", clean_phone)
            return Response({
                "success": True,
                "mode": "started",
                "first_item_sent": False,
                "suppress_reply": False,
                "clocked_in": True,
                "shift": {"start": shift_start, "end": shift_end},
                "tasks": all_items,
                "total_items": len(all_items),
                "message_for_user": (
                    f"📋 *Your shift has {len(all_items)} task(s):*\n\n{task_list_text}\n\n"
                    "Reply *Yes*, *No*, or *N/A* for each task as I send them."
                ),
            })

    # Not clocked in or no tasks: return preview
    task_list_text = "\n".join(
        f"{i+1}. {item['title']}" for i, item in enumerate(all_items)
    )

    return Response({
        "success": True,
        "mode": "preview",
        "clocked_in": clocked_in,
        "shift": {"start": shift_start, "end": shift_end},
        "tasks": all_items,
        "total_items": len(all_items),
        "message_for_user": (
            f"Your shift ({shift_start} – {shift_end}) has {len(all_items)} task(s):\n{task_list_text}\n\n"
            + ("Say *Start checklist* when you're ready to begin." if clocked_in else "Clock in first, then I'll start your checklist.")
        ),
    })


def _collect_shift_task_items(active_shift):
    """
    Build a merged list of task items for a shift: process template tasks
    (from TaskTemplate.tasks / sop_steps JSON) + custom ShiftTasks.
    """
    from scheduling.models import ShiftTask

    template_items = []
    try:
        templates = list(active_shift.task_templates.all())
    except Exception:
        templates = []

    for tpl in templates:
        steps = []
        try:
            if getattr(tpl, "sop_steps", None):
                steps = list(tpl.sop_steps or [])
            elif getattr(tpl, "tasks", None):
                steps = list(tpl.tasks or [])
        except Exception:
            steps = []
        if not steps:
            steps = [{"title": getattr(tpl, "name", "Task"), "description": getattr(tpl, "description", "") or ""}]
        for step in steps:
            if isinstance(step, str):
                title = (step.strip()[:255] or getattr(tpl, "name", "Task")).strip()
                desc = ""
            elif isinstance(step, dict):
                title = (step.get("title") or step.get("name") or step.get("task") or getattr(tpl, "name", "Task"))[:255].strip()
                desc = (step.get("description") or step.get("details") or "").strip()
            else:
                title = (getattr(tpl, "name", "Task") or "Task").strip()
                desc = ""
            if not title:
                title = getattr(tpl, "name", "Task") or "Task"
            requires_photo = bool(
                (step.get("verification_type") if isinstance(step, dict) else None) == "PHOTO"
                or getattr(tpl, "verification_type", "NONE") == "PHOTO"
            )
            template_items.append({
                "title": title,
                "description": desc,
                "source": "process_template",
                "template_name": getattr(tpl, "name", ""),
                "requires_photo": requires_photo,
            })

    custom_tasks = ShiftTask.objects.filter(shift=active_shift).exclude(
        status__in=["COMPLETED", "CANCELLED"]
    )
    custom_items = []
    for t in custom_tasks:
        custom_items.append({
            "title": t.title,
            "description": t.description or "",
            "source": "custom_task",
            "priority": t.priority or "MEDIUM",
            "requires_photo": getattr(t, "verification_type", "NONE") == "PHOTO",
            "status": t.status,
        })

    all_items = []
    seen_titles = set()
    for item in custom_items:
        seen_titles.add(item["title"])
        all_items.append(item)
    for item in template_items:
        if item["title"] not in seen_titles:
            seen_titles.add(item["title"])
            all_items.append(item)

    return all_items


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_start_whatsapp_checklist(request):
    """
    Miya/Lua endpoint: start (or resume) the checklist for a staff member.
    Returns the task list so Miya delivers tasks conversationally — Django
    never sends WhatsApp messages for checklists.
    Body: {phone}
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

    from scheduling.models import ShiftChecklistProgress, ShiftTask

    existing_prog = ShiftChecklistProgress.objects.filter(
        shift=active_shift, staff=user
    ).first()

    if existing_prog and existing_prog.status == 'COMPLETED':
        return Response({
            "success": True,
            "status": "completed",
            "clocked_in": True,
            "message_for_user": "Your checklist is already complete. Have a productive shift!",
        })

    if existing_prog and existing_prog.status == 'IN_PROGRESS':
        task_ids = existing_prog.task_ids or []
        responses = existing_prog.responses or {}
        current_id = existing_prog.current_task_id or (task_ids[0] if task_ids else None)
        tasks_qs = ShiftTask.objects.filter(id__in=task_ids)
        tasks_map = {str(t.id): t for t in tasks_qs}
        tasks_out = []
        for tid in task_ids:
            t = tasks_map.get(tid)
            if t:
                tasks_out.append({
                    "id": str(t.id), "title": t.title,
                    "description": t.description or "",
                    "status": t.status,
                    "response": responses.get(tid),
                })
        current_task = tasks_map.get(current_id)
        current_idx = (task_ids.index(current_id) + 1) if current_id and current_id in task_ids else 1
        return Response({
            "success": True,
            "status": "in_progress",
            "clocked_in": True,
            "tasks": tasks_out,
            "total": len(task_ids),
            "current_task": {
                "id": current_id,
                "index": current_idx,
                "title": current_task.title if current_task else "",
                "description": (current_task.description or "") if current_task else "",
            } if current_id else None,
        })

    try:
        result = notification_service.prepare_checklist_for_miya(
            user, active_shift, phone_digits=clean_phone
        )
    except Exception as e:
        logger.exception("agent_start_whatsapp_checklist failed for user %s: %s", user.id, e)
        return Response(
            {"success": False, "error": str(e),
             "message_for_user": "I'm having trouble loading your checklist. Please try again."},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result is None:
        return Response(
            {"success": False, "error": "No checklist items",
             "message_for_user": "No tasks are assigned to your shift right now. You're all set!"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    return Response(result, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_checklist_respond(request):
    """
    Miya/Lua endpoint: record a staff response to the current checklist task
    and return the next task (or completion status).
    Body: {phone, response: "yes"|"no"|"n_a", notes?: str}
    """
    ok, err_response = _validate_agent_key(request)
    if not ok:
        return err_response

    user, active_shift, clean_phone, err = _resolve_staff_and_shift(request.data or {})
    if err:
        return err

    response_value = (request.data.get("response") or "").strip().lower().replace("/", "_")
    if response_value not in ("yes", "no", "n_a"):
        return Response(
            {"success": False, "error": "Invalid response",
             "message_for_user": "Please reply Yes, No, or N/A."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from scheduling.models import ShiftChecklistProgress, ShiftTask, TaskVerificationRecord

    prog = ShiftChecklistProgress.objects.filter(
        shift=active_shift, staff=user, status='IN_PROGRESS'
    ).first()
    if not prog:
        return Response(
            {"success": False, "error": "No active checklist",
             "message_for_user": "You don't have an active checklist. Say 'Start checklist' to begin."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    task_ids = prog.task_ids or []
    responses = prog.responses or {}
    current_id = prog.current_task_id
    if not current_id:
        return Response(
            {"success": False, "error": "No current task"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    task = ShiftTask.objects.filter(id=current_id).first()
    if not task:
        return Response(
            {"success": False, "error": "Task not found"},
            status=status.HTTP_404_NOT_FOUND,
        )

    # Record the response
    responses[current_id] = response_value
    if response_value == "yes":
        task.status = "COMPLETED"
        task.completed_at = timezone.now()
    elif response_value == "no":
        task.status = "IN_PROGRESS"
        task.started_at = task.started_at or timezone.now()
        if request.data.get("notes"):
            task.notes = (task.notes or "") + f"\n[Staff] {request.data['notes']}"
    elif response_value == "n_a":
        task.status = "CANCELLED"
    task.save(update_fields=["status", "completed_at", "started_at", "notes"])

    try:
        TaskVerificationRecord.objects.create(
            task=task,
            submitted_by=user,
            checklist_responses={
                "response": response_value,
                "checklist_item_id": str(task.id),
                "shift_id": str(active_shift.id),
            },
        )
    except Exception:
        pass

    # Find next unanswered task
    next_task = None
    next_idx = None
    for i, tid in enumerate(task_ids):
        if tid not in responses:
            next_task_obj = ShiftTask.objects.filter(id=tid).first()
            if next_task_obj and next_task_obj.status not in ("COMPLETED", "CANCELLED"):
                next_task = next_task_obj
                next_idx = i + 1
                break

    answered = len(responses)
    total = len(task_ids)

    if next_task:
        prog.current_task_id = str(next_task.id)
        prog.responses = responses
        prog.save(update_fields=["current_task_id", "responses", "updated_at"])
        return Response({
            "success": True,
            "status": "next_task",
            "answered": answered,
            "total": total,
            "current_task": {
                "id": str(next_task.id),
                "index": next_idx,
                "title": next_task.title,
                "description": next_task.description or "",
            },
        })

    # All tasks answered — checklist complete
    prog.status = "COMPLETED"
    prog.completed_at = timezone.now()
    prog.responses = responses
    prog.save(update_fields=["status", "completed_at", "responses", "updated_at"])
    yes_count = sum(1 for v in responses.values() if v == "yes")
    no_count = sum(1 for v in responses.values() if v == "no")
    na_count = sum(1 for v in responses.values() if v == "n_a")
    return Response({
        "success": True,
        "status": "completed",
        "answered": answered,
        "total": total,
        "summary": {
            "yes": yes_count,
            "no": no_count,
            "n_a": na_count,
        },
        "message_for_user": (
            f"✅ Checklist complete! {yes_count} done, "
            f"{no_count} not done, {na_count} skipped out of {total} tasks."
        ),
    })
