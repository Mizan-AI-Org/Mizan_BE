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


from core.agent_auth import validate_agent_bearer


def _validate_agent_key(request):
    ok, err = validate_agent_bearer(request)
    if not ok:
        code = status.HTTP_500_INTERNAL_SERVER_ERROR if err == "Agent key not configured" else status.HTTP_401_UNAUTHORIZED
        return False, Response({"success": False, "error": err}, status=code)
    return True, None


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_send_announcement(request):
    """
    Miya endpoint: manager sends an announcement from the chat widget.
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
    tags = None
    broadcast_all = data.get("broadcast_all") is True or audience == "all"
    if isinstance(audience, dict):
        staff_ids = audience.get("staff_ids") or None
        roles = audience.get("roles") or None
        departments = audience.get("departments") or None
        # ``tags`` is the canonical operational tag vocabulary — see
        # accounts.staff_tags. Enables "send to the kitchen", "message
        # all housekeeping staff", etc.
        tags = audience.get("tags") or None
        if audience.get("all") is True:
            broadcast_all = True
    # Missing audience or bare "all" => explicit team broadcast only.
    # Targeting one person must use staff_ids or create_dashboard_task.

    try:
        success, count, err, details = notification_service.send_announcement_to_audience(
            restaurant_id=str(restaurant_id),
            title=title,
            message=message,
            sender=sender,
            staff_ids=staff_ids,
            roles=roles,
            departments=departments,
            tags=tags,
            channels=["app", "whatsapp"],
            broadcast_all=broadcast_all,
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
    Endpoint for Miya agent to send WhatsApp messages/templates via the backend.
    """
    logger.info(f"Incoming WhatsApp request from agent. Type: {request.data.get('type', 'text')}")
    try:
        ok, err = validate_agent_bearer(request)
        if not ok:
            code = status.HTTP_500_INTERNAL_SERVER_ERROR if err == "Agent key not configured" else status.HTTP_401_UNAUTHORIZED
            return Response({'success': False, 'error': err}, status=code)

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
    Miya: create a staff-captured order for Today's Orders when the agent has the transcript
    (e.g. parallel WhatsApp routing). Auth: Bearer MIYA_MASTRA_API_KEY.

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

        # Auto-detect Bar / Floor / Kitchen from staff role; manager may
        # still toggle validation per order after the fact.
        station = (data.get("detected_station") or data.get("station") or "").strip()
        if not station and user is not None:
            role_l = str(
                getattr(user, "role", None) or getattr(user, "position", None) or ""
            ).lower()
            import re as _re

            if _re.search(r"bar|bartender|barman|mixolog", role_l):
                station = "Bar"
            elif _re.search(r"chef|kitchen|cook|cuisine|commis", role_l):
                station = "Kitchen"
            elif _re.search(r"wait|server|floor|service|host|runner", role_l):
                station = "Floor"
            else:
                station = "Other"
        if station:
            merged["detected_station"] = station[:20]

        requires_val = data.get("requires_manager_validation")
        if requires_val is None:
            requires_val = data.get("requiresManagerValidation")
        if isinstance(requires_val, str):
            requires_val = requires_val.lower() in ("1", "true", "yes")
        if requires_val:
            merged["requires_manager_validation"] = True

        order = StaffCapturedOrder.objects.create(
            restaurant=restaurant,
            recorded_by=user,
            **merged,
        )
        try:
            notification_service.notify_staff_captured_order(user, order, items_summary[:2000])
        except Exception:
            logger.exception("agent_create_staff_captured_order: Miya notify failed (non-fatal)")
    except Exception as e:
        logger.exception("agent_create_staff_captured_order: %s", e)
        return Response({"success": False, "error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    return Response(
        {
            "success": True,
            "order_id": str(order.id),
            "short_id": str(order.id)[:8],
            "detected_station": order.detected_station or None,
            "requires_manager_validation": bool(order.requires_manager_validation),
            "needs_station_clarification": (
                not bool(order.detected_station) or order.detected_station == "Other"
            ),
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

    active_shift = _get_shift_for_checklist(user, allow_standing=True)
    if not active_shift:
        today = timezone.localdate()
        from scheduling.models import AssignedShift
        from django.db.models import Q
        from scheduling.standing_checklist import get_standing_templates_for_staff

        any_shifts = AssignedShift.objects.filter(
            Q(staff=user) | Q(staff_members=user),
            shift_date=today,
        ).values_list('id', 'status', 'start_time', 'end_time')
        standing = get_standing_templates_for_staff(user)
        logger.warning(
            "agent_checklist: no active shift for user %s (%s %s, phone %s) on %s. "
            "All shifts today: %s standing_templates=%s",
            user.id, user.first_name, user.last_name, clean_phone, today, list(any_shifts),
            len(standing),
        )
        if standing:
            msg = _cl_tr(user, "checklist.need_clock_in")
        else:
            msg = (
                "You don't have a checklist assigned right now. "
                "Ask your manager to assign a process to you in Processes & Tasks, "
                "or to put you on today's schedule."
            )
        return user, None, clean_phone, Response(
            {"success": False, "error": "No shift",
             "message_for_user": msg},
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

def _cl_tr(user, key, **kwargs):
    """Translate checklist/PayGuard-adjacent agent strings for the staff user."""
    from core.i18n import get_effective_language, tr
    lang = get_effective_language(user=user, restaurant=getattr(user, "restaurant", None))
    return tr(key, lang, **kwargs)

def agent_preview_checklist(request):
    """
    Miya endpoint: preview OR auto-start the checklist for a staff member's shift.
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
            "message_for_user": _cl_tr(user, "checklist.none"),
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
                "message_for_user": _cl_tr(user, "checklist.already_complete"),
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
                "message_for_user": _cl_tr(
                    user,
                    "checklist.shift_list",
                    count=len(all_items),
                    list=task_list_text,
                ),
            })

    # Not clocked in: return preview (clock-in optional — staff can say start checklist)
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
        "message_for_user": _cl_tr(
            user,
            "checklist.preview_ready",
            start=shift_start or "—",
            end=shift_end or "—",
            count=len(all_items),
            list=task_list_text,
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
            requires_photo = False
            if isinstance(step, dict):
                requires_photo = bool(
                    step.get("requires_photo")
                    or step.get("verification_required")
                    or str(step.get("verification_type") or "").upper() == "PHOTO"
                )
            if not requires_photo:
                requires_photo = str(getattr(tpl, "verification_type", "NONE") or "").upper() == "PHOTO"
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
    from scheduling.checklist_photo import task_requires_photo

    custom_items = []
    for t in custom_tasks:
        custom_items.append({
            "title": t.title,
            "description": t.description or "",
            "source": "custom_task",
            "priority": t.priority or "MEDIUM",
            "requires_photo": task_requires_photo(t),
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
    Miya endpoint: start (or resume) the checklist for a staff member.
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

    clocked_in = _is_staff_clocked_in(user)

    from scheduling.models import ShiftChecklistProgress, ShiftTask

    existing_prog = ShiftChecklistProgress.objects.filter(
        shift=active_shift, staff=user
    ).first()

    if existing_prog and existing_prog.status == 'COMPLETED':
        return Response({
            "success": True,
            "status": "completed",
            "clocked_in": clocked_in,
            "message_for_user": _cl_tr(user, "checklist.already_complete_shift"),
        })

    if existing_prog and existing_prog.status == 'IN_PROGRESS':
        from scheduling.checklist_photo import photo_prompt_for_task, task_requires_photo

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
                    "requires_photo": task_requires_photo(t),
                })
        current_task = tasks_map.get(current_id)
        current_idx = (task_ids.index(current_id) + 1) if current_id and current_id in task_ids else 1

        # Resume mid photo-proof
        try:
            from notifications.models import WhatsAppSession

            sess = WhatsAppSession.objects.filter(phone=clean_phone).first()
            awaiting_id = (
                (sess.context or {}).get("awaiting_verification_for_task_id")
                if sess and isinstance(sess.context, dict)
                else None
            )
            if (
                sess
                and sess.state == "awaiting_task_photo"
                and awaiting_id
                and current_task
                and str(awaiting_id) == str(current_task.id)
            ):
                return Response({
                    "success": True,
                    "status": "awaiting_photo",
                    "clocked_in": clocked_in,
                    "tasks": tasks_out,
                    "total": len(task_ids),
                    "current_task": {
                        "id": current_id,
                        "index": current_idx,
                        "title": current_task.title,
                        "description": current_task.description or "",
                        "requires_photo": True,
                    },
                    "message_for_user": photo_prompt_for_task(current_task, user=user),
                })
        except Exception:
            logger.exception("checklist start: awaiting_photo resume check failed")

        return Response({
            "success": True,
            "status": "in_progress",
            "clocked_in": clocked_in,
            "tasks": tasks_out,
            "total": len(task_ids),
            "current_task": {
                "id": current_id,
                "index": current_idx,
                "title": current_task.title if current_task else "",
                "description": (current_task.description or "") if current_task else "",
                "requires_photo": task_requires_photo(current_task) if current_task else False,
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
             "message_for_user": _cl_tr(user, "checklist.load_error")},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    if result is None:
        return Response(
            {"success": False, "error": "No checklist items",
             "message_for_user": _cl_tr(user, "checklist.none")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    result["clocked_in"] = clocked_in
    return Response(result, status=status.HTTP_200_OK)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_checklist_respond(request):
    """
    Miya endpoint: record a staff response to the current checklist task
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
             "message_for_user": _cl_tr(user, "checklist.reply_yes_no")},
            status=status.HTTP_400_BAD_REQUEST,
        )

    from scheduling.models import ShiftChecklistProgress, ShiftTask, TaskVerificationRecord

    prog = ShiftChecklistProgress.objects.filter(
        shift=active_shift, staff=user, status='IN_PROGRESS'
    ).first()
    if not prog:
        return Response(
            {"success": False, "error": "No active checklist",
             "message_for_user": _cl_tr(user, "checklist.no_active")},
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

    from scheduling.checklist_photo import (
        arm_whatsapp_photo_await,
        photo_prompt_for_task,
        task_requires_photo,
    )

    # Already waiting for a photo on this task — remind them
    try:
        from notifications.models import WhatsAppSession

        sess = WhatsAppSession.objects.filter(phone=clean_phone).first()
        awaiting_id = (
            (sess.context or {}).get("awaiting_verification_for_task_id")
            if sess and isinstance(sess.context, dict)
            else None
        )
        if (
            sess
            and sess.state == "awaiting_task_photo"
            and awaiting_id
            and str(awaiting_id) == str(task.id)
            and response_value == "yes"
        ):
            photo_msg = photo_prompt_for_task(task, user=user)
            try:
                notification_service.send_whatsapp_text(clean_phone, photo_msg)
            except Exception:
                logger.exception("agent_checklist_respond: photo remind WA send failed")
            return Response({
                "success": True,
                "status": "awaiting_photo",
                "answered": len(responses),
                "total": len(task_ids),
                "current_task": {
                    "id": str(task.id),
                    "title": task.title,
                    "description": task.description or "",
                    "requires_photo": True,
                },
                "message_for_user": photo_msg,
            })
    except Exception:
        logger.exception("checklist awaiting_photo remind failed")

    # Yes + photo proof required → arm WA image handler; do not complete yet
    if response_value == "yes" and task_requires_photo(task):
        arm_whatsapp_photo_await(
            phone=clean_phone,
            user=user,
            task=task,
            shift_id=str(active_shift.id),
        )
        # Keep task open; mark started so Live Board shows progress
        task.status = "IN_PROGRESS"
        task.started_at = task.started_at or timezone.now()
        task.save(update_fields=["status", "started_at"])
        prog.current_task_id = str(task.id)
        prog.responses = responses
        prog.save(update_fields=["current_task_id", "responses", "updated_at"])
        try:
            TaskVerificationRecord.objects.get_or_create(
                task=task,
                submitted_by=user,
                defaults={
                    "checklist_responses": {
                        "response": "yes",
                        "awaiting_photo": True,
                        "checklist_item_id": str(task.id),
                        "shift_id": str(active_shift.id),
                    },
                    "photo_evidence": [],
                },
            )
        except Exception:
            pass
        photo_msg = photo_prompt_for_task(task, user=user)
        try:
            notification_service.send_whatsapp_text(clean_phone, photo_msg)
        except Exception:
            logger.exception("agent_checklist_respond: photo prompt WA send failed")
        return Response({
            "success": True,
            "status": "awaiting_photo",
            "answered": len(responses),
            "total": len(task_ids),
            "current_task": {
                "id": str(task.id),
                "title": task.title,
                "description": task.description or "",
                "requires_photo": True,
            },
            "message_for_user": photo_msg,
        })

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

    # Processes & Tasks condition flow (Flag for manager / goto / end)
    branch_outcome = {"action": None, "result": None, "flow": "next"}
    if response_value in ("yes", "no"):
        try:
            from scheduling.checklist_branch_actions import apply_checklist_branch

            branch_outcome = apply_checklist_branch(
                shift_task=task,
                staff_user=user,
                answer=response_value,
            )
        except Exception:
            logger.exception(
                "checklist branch action failed task=%s answer=%s", task.id, response_value
            )

    try:
        TaskVerificationRecord.objects.create(
            task=task,
            submitted_by=user,
            checklist_responses={
                "response": response_value,
                "checklist_item_id": str(task.id),
                "shift_id": str(active_shift.id),
                "branch": branch_outcome.get("action"),
            },
        )
    except Exception:
        pass

    # End process early when branch says so
    if branch_outcome.get("flow") == "end":
        from scheduling.checklist_completion import finalize_shift_checklist_completion

        prog.responses = responses
        summary = finalize_shift_checklist_completion(prog, user)
        alert_note = ""
        if (branch_outcome.get("result") or {}).get("executed") and (
            branch_outcome.get("action") or {}
        ).get("type") == "alert":
            alert_note = _cl_tr(user, "checklist.flagged_generic")
        elif branch_outcome.get("result") and branch_outcome["result"].get("notified"):
            alert_note = _cl_tr(user, "checklist.flagged_generic")
        return Response({
            "success": True,
            "status": "completed",
            "answered": len(responses),
            "total": len(task_ids),
            "branch": branch_outcome.get("action"),
            "branch_result": branch_outcome.get("result"),
            "completion_summary": summary,
            "message_for_user": _cl_tr(
                user, "checklist.stopped", title=task.title, note=alert_note
            ),
        })

    # Find next unanswered task (respect goto target when present)
    from scheduling.checklist_branch_actions import find_next_checklist_task

    next_task, next_idx = find_next_checklist_task(
        task_ids, responses, branch_outcome=branch_outcome
    )

    answered = len(responses)
    total = len(task_ids)

    alert_suffix = ""
    br_result = branch_outcome.get("result") or {}
    if br_result.get("executed") and (branch_outcome.get("action") or {}).get("type") == "alert":
        names = [n.get("name") for n in (br_result.get("notified") or []) if n.get("name")]
        if names:
            alert_suffix = _cl_tr(user, "checklist.flagged_named", names=", ".join(names))
        else:
            alert_suffix = _cl_tr(user, "checklist.flagged_generic")

    if next_task:
        from core.i18n import format_checklist_task_message, get_effective_language

        prog.current_task_id = str(next_task.id)
        prog.responses = responses
        prog.save(update_fields=["current_task_id", "responses", "updated_at"])
        lang = get_effective_language(
            user=user, restaurant=getattr(user, "restaurant", None)
        )
        noted = _cl_tr(
            user, "checklist.noted_next", title=task.title, suffix=alert_suffix
        )
        next_body = format_checklist_task_message(
            lang,
            title=next_task.title,
            index=next_idx or answered + 1,
            total=total,
            description=next_task.description or "",
            requires_photo=task_requires_photo(next_task),
            is_first=False,
        )
        # Prefixed "Noted…" then the next task prompt (skip duplicate ack head)
        message = f"{noted}\n\n" + "\n".join(next_body.split("\n")[2:]).lstrip()
        return Response({
            "success": True,
            "status": "next_task",
            "answered": answered,
            "total": total,
            "branch": branch_outcome.get("action"),
            "branch_result": br_result or None,
            "message_for_user": message,
            "current_task": {
                "id": str(next_task.id),
                "index": next_idx,
                "title": next_task.title,
                "description": next_task.description or "",
                "requires_photo": task_requires_photo(next_task),
            },
        })

    # All tasks answered — checklist complete (archive responses + photos)
    from scheduling.checklist_completion import finalize_shift_checklist_completion

    prog.responses = responses
    summary = finalize_shift_checklist_completion(prog, user)
    yes_count = summary["summary"]["yes"]
    no_count = summary["summary"]["no"]
    na_count = summary["summary"]["n_a"]
    total = summary["summary"]["total"]

    try:
        from notifications.models import WhatsAppSession
        sess = WhatsAppSession.objects.filter(phone=clean_phone).first()
        if sess and sess.state == "in_checklist":
            sess.state = "idle"
            if isinstance(sess.context, dict):
                sess.context.pop("checklist", None)
            sess.save(update_fields=["state", "context"])
    except Exception:
        pass

    return Response({
        "success": True,
        "status": "completed",
        "answered": answered,
        "total": total,
        "summary": {
            "yes": yes_count,
            "no": no_count,
            "n_a": na_count,
            "fully_compliant": summary.get("fully_compliant", True),
            "photo_count": sum(t.get("photo_count", 0) for t in summary.get("tasks", [])),
        },
        "completion_summary": summary,
        "message_for_user": _cl_tr(
            user,
            "checklist.complete",
            yes=yes_count,
            no=no_count,
            na=na_count,
            total=total,
        ),
    })


@api_view(["POST"])
@authentication_classes([])
@permission_classes([AllowAny])
def agent_voice_reply(request):
    """Miya voice-reply endpoint.

    Body:
      restaurant_id  required (for auditing only -- not used to scope)
      phone          required E.164 (e.g. +212600000000) or local; we
                     normalize via normalize_whatsapp_phone
      text           required, the spoken content (Miya's chat reply)
      caption        optional follow-up text bubble (e.g. action buttons,
                     since WhatsApp audio messages don't carry inline text)
      voice          optional voice id ("alloy", "nova", "shimmer", ...)
      speed          optional 0.25-4.0
      voice_note     optional bool, default true (push-to-talk style)

    Response on success:
      {
        success: true,
        delivered: true,
        media_id: "...",
        message_id: "...",
        message_for_user: "Sent voice note (X seconds)"
      }
    """
    ok, err = _validate_agent_key(request)
    if not ok:
        return err

    data = request.data or {}
    text = (data.get("text") or "").strip()
    phone = (data.get("phone") or "").strip()
    if not text or not phone:
        return Response(
            {
                "success": False,
                "error": "phone and text are required",
                "message_for_user": "I need both a phone number and the text to speak.",
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    voice = (data.get("voice") or "").strip() or None
    try:
        speed = float(data.get("speed") or 0) or None
    except (TypeError, ValueError):
        speed = None
    voice_note = data.get("voice_note")
    voice_note = True if voice_note is None else bool(voice_note)
    caption = (data.get("caption") or "").strip() or None

    audio_bytes, mime = notification_service.synthesize_whatsapp_voice_bytes(
        text, voice=voice, speed=speed,
    )
    if not audio_bytes:
        return Response(
            {
                "success": False,
                "error": "TTS failed",
                "message_for_user": (
                    "I couldn't generate the voice note (TTS provider error). "
                    "Falling back to text would be safer."
                ),
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    sent_ok, info = notification_service.send_whatsapp_audio(
        phone=phone,
        audio_bytes=audio_bytes,
        mime_type=mime or "audio/ogg; codecs=opus",
        caption=caption,
        voice_note=voice_note,
    )
    if not sent_ok:
        return Response(
            {
                "success": False,
                "delivered": False,
                "error": (info or {}).get("error") or "WhatsApp send failed",
                "message_for_user": (
                    "Generated the audio but WhatsApp wouldn't deliver it. "
                    "I'll send the reply as text instead."
                ),
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    return Response(
        {
            "success": True,
            "delivered": True,
            "media_id": info.get("media_id"),
            "message_id": info.get("external_id"),
            "bytes": len(audio_bytes),
            "message_for_user": "Voice note sent.",
        },
        status=status.HTTP_200_OK,
    )
