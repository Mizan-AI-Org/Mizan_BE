from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from datetime import timedelta
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from django.core.files.base import ContentFile
from accounts.permissions import IsAdminOrManager
from rest_framework.parsers import MultiPartParser, FormParser
import logging
import json
import threading
import requests as http_requests
import re

logger = logging.getLogger(__name__)
from .models import Notification, NotificationPreference, DeviceToken, NotificationAttachment, NotificationIssue, WhatsAppMessageProcessed
from .serializers import (
    NotificationSerializer, 
    NotificationPreferenceSerializer,
    DeviceTokenSerializer,
    AnnouncementCreateSerializer
)
from .services import notification_service
from core.whatsapp_config import (
    get_miya_whatsapp_enabled,
    get_whatsapp_verify_token,
)
from .utils import (
    infer_incident_type,
    infer_severity,
    extract_occurred_at,
    extract_incident_location,
    looks_like_guest_order_intent,
    looks_like_whatsapp_incident_report,
    should_route_whatsapp_voice_to_incident,
)
from scheduling.audit import AuditTrailService, AuditActionType, AuditSeverity
from core.utils import build_tenant_context
from .models import WhatsAppSession
from accounts.models import CustomUser
from accounts.utils import calculate_distance, find_matching_location, restaurant_has_clockin_geofence
from accounts.services import UserManagementService, try_activate_staff_on_inbound_message, normalize_activation_phone_inbound
from timeclock.models import ClockEvent
from scheduling.models import ShiftTask, AssignedShift, ShiftChecklistProgress
from django.conf import settings as dj_settings
from core.i18n import get_effective_language, tr, whatsapp_language_code
from dashboard.models import StaffCapturedOrder
from staff.incident_routing import resolve_default_assignee_for_incident_type
from .order_parsing import merge_parsed_order_fields


def _looks_like_voice_ui_placeholder(body: str) -> bool:
    """
    WhatsApp/Mastra sometimes surfaces a voice note as text with no transcript (e.g. 'Voice message (0:13)').
    Forwarding that to Miya as plain text caused refusals ('use the POS'). Django handles this path instead.
    """
    if not body or not isinstance(body, str):
        return False
    s = body.strip()
    if len(s) > 120:
        return False
    if re.search(
        r'voice\s*message|message\s+vocal|note\s+vocale|رسالة\s*صوتية|audio\s*message|message\s+audio',
        s,
        re.I,
    ):
        return True
    if '🎤' in s and re.search(r'\(\s*\d+\s*:\s*\d+\s*\)', s):
        return True
    return False


def _text_looks_like_shared_gps_clock_in(body: str) -> bool:
    """True when plain text is almost certainly a WhatsApp-style location share, not random numbers."""
    if not body or not isinstance(body, str):
        return False
    bl = body.lower()
    needles = (
        "coordinates:",
        "coordinate:",
        "shared a location",
        "maps.google.com",
        "google.com/maps",
        "g.co/",
        "?q=",
        "live location",
        "current location",
    )
    return any(n in bl for n in needles)


def _django_owns_whatsapp_inbound_message(msg: dict, django_owned_types: set) -> bool:
    """True if Django should process this message and Mastra must not see the webhook.

    Shared GPS replies occasionally omit ``type: \"location\"`` or echo ``type: null``
    while still including a ``location`` object. Replies to the Location Request
    interactive often arrive as ``type: interactive`` / ``location_reply`` —
    those must never be forwarded to Mastra while Django skips its handlers after the
    early defer guard.

    Own ``location_reply`` even when coordinates are in a non-standard shape so Mastra
    never races ahead with a generic error before Django parses coords.

    Coordinate extraction mirrors `_extract_whatsapp_inbound_location` (runs later
    in module load — OK at call time).
    """
    if not isinstance(msg, dict):
        return False

    # Any message while a staff flow is mid-flight stays in Django — including
    # location pins, interactive replies, and follow-up text after an incident photo.
    from_phone = msg.get("from")
    phone_digits = "".join(filter(str.isdigit, str(from_phone or "")))
    phone_digits = normalize_activation_phone_inbound(phone_digits) or phone_digits
    if phone_digits:
        sess = WhatsAppSession.objects.filter(phone=phone_digits).first()
        if sess:
            st = getattr(sess, "state", None) or ""
            ctx = getattr(sess, "context", None) or {}
            if st in (
                "awaiting_clock_in_location",
                "awaiting_incident_text",
                "awaiting_incident_clarification",
                "awaiting_incident_photo",
                "awaiting_task_photo",
                "awaiting_feedback",
            ) or ctx.get("incident_photo_media_id"):
                return True

    t = msg.get("type")
    if t in django_owned_types:
        return True
    if t == "interactive" and (msg.get("interactive") or {}).get("type") == "location_reply":
        return True
    if t == "interactive":
        inter = msg.get("interactive") or {}
        if inter.get("type") == "button_reply":
            btn = inter.get("button_reply") or {}
            btn_id = (btn.get("id") or "").strip()
            title = (btn.get("title") or "").strip()
            if btn_id in ("clock_in_now", "clock_out_now"):
                return True
            if _normalize_clock_in_intent(title):
                return True
            title_l = title.strip().lower().replace("-", " ")
            if title_l in ("clock out", "clockout", "clock out.", "pointer sortie", "pointage sortie"):
                return True
            try:
                from staff.whatsapp_escalation import (
                    is_cancel_send_reply,
                    is_explicit_confirm_send_reply,
                    looks_like_staff_manager_escalation,
                )

                if (
                    is_explicit_confirm_send_reply(title)
                    or is_cancel_send_reply(title)
                    or looks_like_staff_manager_escalation(title)
                ):
                    return True
            except Exception:
                # Fail closed: keep escalations/confirm buttons out of Mastra.
                return True
    _, lat_raw, lon_raw = _extract_whatsapp_inbound_location(msg)
    lat_c, lon_c = _coerce_whatsapp_location_lat_lon(lat_raw, lon_raw)
    if lat_c is not None and lon_c is not None:
        return True
    if t == "text":
        body = ((msg.get("text") or {}).get("body") or "").strip()
        if _looks_like_voice_ui_placeholder(body):
            return True
        # Clock-in / clock-out are owned by Django (Share Location + geofence).
        # Never forward to Mastra/Space — Space invents "technical issue" / "opening float".
        if _normalize_clock_in_intent(body):
            return True
        body_l = body.strip().lower().replace("-", " ")
        if body_l in ("clock out", "clockout", "clock out.", "pointer sortie", "pointage sortie"):
            return True
        try:
            from staff.whatsapp_escalation import looks_like_cash_clock_in_followup

            if looks_like_cash_clock_in_followup(body):
                return True
        except Exception:
            pass
        # Staff → manager escalations (wages, payslip, absence) must land as
        # StaffRequest on the dashboard — never Mastra leave-form / confirm invents.
        try:
            from staff.whatsapp_escalation import (
                is_cancel_send_reply,
                is_confirm_send_reply,
                is_explicit_confirm_send_reply,
                looks_like_staff_manager_escalation,
                session_has_staff_escalation_context,
            )

            if looks_like_staff_manager_escalation(body):
                return True
            # Own confirm/cancel only when explicit ("Yes, send it") or this
            # session already has a pending escalate-to-manager ask. Bare
            # "Yes"/"Ok" stay with Mastra for checklists.
            if is_explicit_confirm_send_reply(body) or (
                (is_cancel_send_reply(body) or is_confirm_send_reply(body))
                and session_has_staff_escalation_context(
                    WhatsAppSession.objects.filter(phone=phone_digits).first()
                    if phone_digits
                    else None
                )
            ):
                return True
        except Exception:
            # Fail closed on escalation detection errors.
            return True
        # Safety / maintenance incident reports (broken glass, slips, …)
        if looks_like_whatsapp_incident_report(body):
            return True
        try:
            from staff.whatsapp_my_shifts import looks_like_my_shifts_query

            if looks_like_my_shifts_query(body):
                return True
        except Exception:
            pass
        try:
            from notifications.dashboard_task_whatsapp import (
                looks_like_dashboard_task_status_reply,
            )

            if looks_like_dashboard_task_status_reply(body):
                return True
        except Exception:
            pass
        if _normalize_start_checklist_intent(body):
            return True
        pair = _parse_lat_lon_from_clock_in_text(body)
        if pair:
            if _text_looks_like_shared_gps_clock_in(body):
                return True
            # Bare coordinates while we've asked for GPS — Django owns this turn.
            if phone_digits:
                sess = WhatsAppSession.objects.filter(phone=phone_digits).first()
                if sess and getattr(sess, "state", None) == "awaiting_clock_in_location":
                    return True
        return False
    return False


def _create_staff_captured_order_parsed(restaurant, user, text, channel):
    """
    Persist Today's Orders row with heuristic parsing (customer, phone, table, dietary, etc.)
    and notify Miya (best-effort), matching incident voice parity.
    """
    fields = merge_parsed_order_fields(text, {})
    fields["channel"] = channel
    order = StaffCapturedOrder.objects.create(
        restaurant=restaurant,
        recorded_by=user,
        **fields,
    )
    try:
        notification_service.notify_staff_captured_order(user, order, (text or "")[:2000])
    except Exception:
        logger.exception("notify_staff_captured_order failed (non-fatal)")
    return order


def _is_likely_shared_static_location(loc, rest, lat, lon, ref_lat=None, ref_lon=None):
    """
    Detect if the location is almost certainly a pinned map place (same coords as
    the venue marker) rather than real GPS.

    WhatsApp's Cloud API often includes ``name`` / ``address`` on *live* shares
    too, so we no longer treat those fields as proof of a static pin — that
    heuristic caused widespread false rejections.

    We only flag when the reported point is unrealistically close (< 2 m) to
    the reference coordinates (restaurant legacy lat/lon, or the matched branch).

    ``ref_lat`` / ``ref_lon`` override the restaurant centroid when the tenant
    uses BusinessLocation geofences (multi-site).
    """
    if not loc or (ref_lat is None and ref_lon is None and not rest):
        return False, None
    try:
        if ref_lat is not None and ref_lon is not None:
            rlat, rlon = float(ref_lat), float(ref_lon)
        elif rest:
            rlat = float(rest.latitude or 0)
            rlon = float(rest.longitude or 0)
        else:
            return False, None
        dist = calculate_distance(float(lat), float(lon), rlat, rlon)
        if dist < 2:
            return True, "Your location appears to match the restaurant pin exactly. Please share your *live location* from where you are standing (current position), not a place picked from the map."
    except (TypeError, ValueError):
        pass
    return False, None


def _parse_lat_lon_from_clock_in_text(text):
    """
    Extract (lat, lon) from plain-text clock-in replies. Some clients send
    coordinates as text (e.g. "Shared a location at coordinates: X, Y" or
    a maps link) instead of a WhatsApp ``location`` message — parse those so
    staff can still clock in while ``awaiting_clock_in_location``.

    WhatsApp often wraps numbers in markdown bold (``**32.…**``), which would
    break naive ``coordinates?:`` patterns — strip ``*`` / ``_`` noise first.
    """
    if not text or not isinstance(text, str):
        return None
    # Bold/italic markers from WhatsApp web/mobile (not real markdown, but breaks digit-adjacent regexes).
    t = re.sub(r"[*_`]+", " ", text)
    t = re.sub(r"\s+", " ", t).strip()
    patterns = (
        re.compile(r"coordinates?:\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)", re.I),
        re.compile(r"[?&]q=\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\b"),
        re.compile(r"@\s*(-?\d+\.?\d*)\s*,\s*(-?\d+\.?\d*)\b"),
        re.compile(r"\b(-?\d{1,3}\.\d+)\s*,\s*(-?\d{1,3}\.\d+)\b"),
    )
    for rx in patterns:
        m = rx.search(t)
        if not m:
            continue
        try:
            lat = float(m.group(1).replace("\u2212", "-").replace("−", "-"))
            lon = float(m.group(2).replace("\u2212", "-").replace("−", "-"))
            if -90 <= lat <= 90 and -180 <= lon <= 180:
                return lat, lon
        except (ValueError, IndexError):
            continue
    return None


def _coerce_whatsapp_location_lat_lon(lat, lon):
    """Return (float, float) or (None, None). Treats 0 as valid."""
    try:
        if lat is None or lon is None:
            return None, None
        if lat == "" or lon == "":
            return None, None
        return float(lat), float(lon)
    except (TypeError, ValueError):
        return None, None


def _extract_whatsapp_inbound_location(msg):
    """Return ``(loc_dict, lat_raw, lon_raw)`` for Cloud API and minor payload variants.

    Meta normally sends ``messages[].location.{latitude,longitude}``. Some clients or
    bridges use ``degreesLatitude`` / ``degreesLongitude`` (On-Prem-style names), or
    an ``interactive`` wrapper with ``type: location_reply`` when replying to a
    Location Request message.

    ``loc_dict`` is best-effort metadata for static-pin heuristics (may be ``{}``).
    """
    if not isinstance(msg, dict):
        return {}, None, None
    raw = msg.get("location")
    loc = raw if isinstance(raw, dict) else {}
    lat_raw = loc.get("latitude")
    lon_raw = loc.get("longitude")
    if lat_raw is None and lon_raw is None:
        lat_raw = loc.get("degreesLatitude") or loc.get("degrees_latitude")
        lon_raw = loc.get("degreesLongitude") or loc.get("degrees_longitude")
    if lat_raw is None and lon_raw is None and msg.get("type") == "interactive":
        inter = msg.get("interactive") or {}
        if inter.get("type") == "location_reply":
            lr = inter.get("location_reply") or {}
            loc = lr if lr else loc
            lat_raw = lr.get("latitude") or lr.get("degreesLatitude")
            lon_raw = lr.get("longitude") or lr.get("degreesLongitude")
            # Meta / some BSPs send coordinates on ``interactive`` when ``location_reply`` is empty.
            if lat_raw is None and lon_raw is None:
                lat_raw = inter.get("latitude") or inter.get("degreesLatitude")
                lon_raw = inter.get("longitude") or inter.get("degreesLongitude")
            iloc = inter.get("location")
            if (lat_raw is None or lon_raw is None) and isinstance(iloc, dict):
                loc = iloc if iloc else loc
                lat_raw = lat_raw or iloc.get("latitude") or iloc.get("degreesLatitude")
                lon_raw = lon_raw or iloc.get("longitude") or iloc.get("degreesLongitude")
    return loc, lat_raw, lon_raw


def _gps_clock_in_applies_to_whatsapp_message(msg, session) -> bool:
    """Whether this inbound webhook message should run GPS clock-in (not text/incident flows)."""
    if not isinstance(msg, dict):
        return False
    t = msg.get("type")
    inter = msg.get("interactive") or {}
    # Always handle native location pins and replies to "share location" in Django — even if
    # coords are missing/malformed (handler re-prompts). Otherwise ``interactive`` falls through
    # the defer guard while idle and Mastra answers with a useless generic error.
    if t == "location":
        return True
    if t == "interactive" and inter.get("type") == "location_reply":
        return True
    if t == "text":
        tb = ((msg.get("text") or {}).get("body") or "").strip()
        pair = _parse_lat_lon_from_clock_in_text(tb)
        if pair and (
            (session and getattr(session, "state", None) == "awaiting_clock_in_location")
            or _text_looks_like_shared_gps_clock_in(tb)
        ):
            return True
    loc, _lat, _lon = _extract_whatsapp_inbound_location(msg)
    lat_c, lon_c = _coerce_whatsapp_location_lat_lon(_lat, _lon)
    if lat_c is None or lon_c is None:
        return False
    if session and getattr(session, "state", None) == "awaiting_clock_in_location":
        return True
    # BSP / echo payloads with coordinates but missing ``type`` — still a map pin.
    if isinstance(msg.get("location"), dict) and (t in (None, "") or t not in (
        "text", "image", "audio", "voice", "document", "button", "contacts"
    )):
        return True
    return False


def _safe_whatsapp_text_send(phone_digits, body, *, log_ctx: str) -> bool:
    """Best-effort WhatsApp text. Returns False on Graph/network failure (never raises)."""
    if not phone_digits or body is None:
        return False
    try:
        ok, info = notification_service.send_whatsapp_text(phone_digits, body)
        if not ok:
            tail = str(phone_digits)[-6:] if phone_digits else ""
            err = (info or {}).get("error") if isinstance(info, dict) else str(info)
            logger.warning("%s: WhatsApp send failed …%s — %s", log_ctx, tail, err)
        return bool(ok)
    except Exception:
        tail = str(phone_digits)[-6:] if phone_digits else ""
        logger.warning("%s: WhatsApp send failed …%s", log_ctx, tail, exc_info=True)
        return False


def _mark_whatsapp_message_processed(wamid: str | None) -> None:
    """Record idempotency only after a turn is handled (allows Meta retry on crash)."""
    if not wamid:
        return
    try:
        WhatsAppMessageProcessed.objects.get_or_create(
            wamid=wamid,
            defaults={"channel": "whatsapp", "processed_at": timezone.now()},
        )
    except Exception:
        logger.warning("Could not mark WhatsApp wamid processed: %s", wamid, exc_info=True)


def _process_whatsapp_clock_in_from_gps(user, phone_digits, session, lat, lon, loc, R):
    """
    Run geofence validation, create :class:`~timeclock.models.ClockEvent`,
    notify staff. ``loc`` is the WhatsApp ``location`` dict for pin/live
    heuristics; use ``{}`` for text-parsed coordinates.
    Returns True when this turn is fully handled (caller should ``continue``).
    """
    rest = getattr(user, "restaurant", None)
    if not rest:
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "no_restaurant_linked"),
            log_ctx="whatsapp_clock_in_gps",
        )
        return True

    # Align with ``timeclock.views.agent_clock_in_by_phone``: evaluate every
    # BusinessLocation (multi-site). Legacy-only tenants still resolve via
    # Restaurant.* inside ``find_matching_location``. The old path only
    # compared the user to ``Restaurant.latitude`` — wrong branch / empty
    # legacy coords caused false "outside zone" or DB errors on save.
    try:
        matched_location, dist_m, nearest = find_matching_location(rest, float(lat), float(lon))
    except (TypeError, ValueError):
        logger.warning("WhatsApp clock-in: invalid lat/lon lat=%r lon=%r", lat, lon)
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "location_unreadable"),
            log_ctx="whatsapp_clock_in_gps",
        )
        return True
    except Exception:
        logger.exception("WhatsApp clock-in: find_matching_location failed")
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "location_check_error"),
            log_ctx="whatsapp_clock_in_gps",
        )
        return True
    if nearest is None:
        try:
            from accounts.models import AuditLog

            AuditLog.create_log(
                restaurant=rest,
                user=user,
                action_type="OTHER",
                entity_type="CLOCK_EVENT",
                description="Clock-in attempt rejected: no geofence configured for tenant",
                new_values={"phone": phone_digits},
            )
        except Exception:
            pass
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "no_geofence_configured"),
            log_ctx="whatsapp_clock_in_gps",
        )
        return True

    if matched_location is None:
        try:
            from accounts.models import AuditLog

            AuditLog.create_log(
                restaurant=rest,
                user=user,
                action_type="OTHER",
                entity_type="CLOCK_EVENT",
                description="Clock-in attempt rejected: outside geofence (WhatsApp)",
                new_values={
                    "phone": phone_digits,
                    "distance_m": int(round(float(dist_m))) if dist_m is not None else None,
                    "nearest_site": getattr(nearest, "name", None),
                },
            )
        except Exception:
            pass
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "outside_geofence"),
            log_ctx="whatsapp_clock_in_gps",
        )
        # Show the approved workplace pin so staff know where to go, then
        # re-offer Share Location — matches the proven WhatsApp clock-in UX.
        try:
            notification_service.send_approved_clockin_zone_hint(phone_digits, nearest)
        except Exception:
            logger.warning("WhatsApp clock-in: zone hint send failed", exc_info=True)
        try:
            session.state = "awaiting_clock_in_location"
            session.save(update_fields=["state"])
        except Exception:
            pass
        try:
            notification_service.send_whatsapp_location_request(
                phone_digits,
                R(user, "share_location_prompt"),
            )
        except Exception:
            logger.warning("WhatsApp clock-in: re-prompt location after outside-zone failed", exc_info=True)
        return True

    dist = float(dist_m) if dist_m is not None else 0.0

    # Optional: tie to today's shift when one exists in the clock-in window.
    # Staff may clock in without a scheduled shift (unplanned / extra cover).
    active_shift = _get_shift_for_clock_in(user)

    # Do NOT reject "pin matches venue exactly" — WhatsApp place shares and
    # live GPS standing at the door both land on/near the site centroid. The
    # geofence match above is the security boundary (see product WhatsApp flow).

    from datetime import timedelta as _td_cl

    last_event = ClockEvent.objects.filter(staff=user).order_by("-timestamp").first()
    if last_event and last_event.event_type == "in":
        now_local = timezone.localtime(timezone.now()).date()
        if timezone.localtime(last_event.timestamp).date() == now_local:
            first_name = getattr(user, "first_name", None) or "Team Member"
            local_time = timezone.localtime(last_event.timestamp).strftime("%H:%M")
            _safe_whatsapp_text_send(
                phone_digits,
                R(user, "already_clocked_in", time=local_time, name=first_name),
                log_ctx="whatsapp_clock_in_gps",
            )
            session.state = "idle"
            session.save(update_fields=["state"])
            return True
        try:
            eight_hours_later = last_event.timestamp + _td_cl(hours=8)
            end_of_that_day = last_event.timestamp.replace(hour=23, minute=59, second=59, microsecond=0)
            auto_out_at = min(eight_hours_later, end_of_that_day)
            auto_out = ClockEvent.objects.create(
                staff=user,
                event_type="out",
                latitude=None,
                longitude=None,
                device_id="whatsapp (auto)",
                notes=(
                    "Auto clock-out: previous clock-in was left open "
                    "across days. Closed so today's clock-in can be "
                    "recorded on the manager dashboard."
                ),
                location=getattr(last_event, "location", None),
                location_mismatch=False,
            )
            ClockEvent.objects.filter(pk=auto_out.pk).update(timestamp=auto_out_at)
        except Exception:
            logger.warning(
                "whatsapp clock-in: auto clock-out for stale event %s failed; continuing to record today's clock-in.",
                getattr(last_event, "id", None),
            )

    matched_loc_fk = matched_location if (
        getattr(matched_location, "pk", None) or getattr(matched_location, "id", None)
    ) else None
    try:
        location_mismatch = bool(matched_loc_fk and not user.can_work_at(matched_loc_fk))
    except Exception:
        logger.warning("WhatsApp clock-in: can_work_at check failed (non-fatal)", exc_info=True)
        location_mismatch = False

    try:
        try:
            dist_note = int(round(float(dist))) if dist is not None else 0
        except (TypeError, ValueError):
            dist_note = 0
        with transaction.atomic():
            last_event = ClockEvent.objects.filter(staff=user).order_by("-timestamp").first()
            if (
                last_event
                and last_event.event_type == "in"
                and timezone.localtime(last_event.timestamp).date() == timezone.localtime(timezone.now()).date()
            ):
                first_name = getattr(user, "first_name", None) or "Team Member"
                local_time = timezone.localtime(last_event.timestamp).strftime("%H:%M")
                _safe_whatsapp_text_send(
                    phone_digits,
                    R(user, "already_clocked_in", time=local_time, name=first_name),
                    log_ctx="whatsapp_clock_in_gps",
                )
                session.state = "idle"
                session.save(update_fields=["state"])
                return True
            clock_event = ClockEvent.objects.create(
                staff=user,
                event_type="in",
                latitude=float(lat),
                longitude=float(lon),
                device_id="whatsapp",
                notes=f"Clock-in via WhatsApp (location verified, distance={dist_note}m)",
                location_encrypted=f"{lat},{lon}",
                location=matched_loc_fk,
                location_mismatch=location_mismatch,
            )
        # Shift status is best-effort: a validation or DB issue on AssignedShift
        # must not roll back an otherwise valid ClockEvent (staff saw a generic
        # error even though geofence passed).
        if active_shift:
            try:
                active_shift.status = "IN_PROGRESS"
                active_shift.save(update_fields=["status"])
            except Exception as shift_err:
                logger.warning(
                    "WhatsApp clock-in: shift IN_PROGRESS update failed (non-fatal) shift=%s: %s",
                    getattr(active_shift, "id", None),
                    shift_err,
                    exc_info=True,
                )
        from accounts.models import AuditLog

        _audit_new = {"distance_m": dist_note}
        if active_shift:
            _audit_new["shift_id"] = str(active_shift.id)
        try:
            AuditLog.create_log(
                restaurant=rest,
                user=user,
                action_type="CREATE",
                entity_type="CLOCK_EVENT",
                entity_id=str(clock_event.id),
                description="Clock-in successful (WhatsApp, location verified)",
                new_values=_audit_new,
            )
        except Exception as audit_err:
            # Clock-in already committed — never make the staff retry because audit failed.
            logger.warning("Clock-in audit log failed (non-fatal): %s", audit_err)
        if location_mismatch and matched_loc_fk:
            try:
                from timeclock.views import _notify_managers_of_location_mismatch

                _notify_managers_of_location_mismatch(clock_event, user, matched_loc_fk)
            except Exception:
                logger.warning("WhatsApp clock-in: location mismatch notify skipped", exc_info=True)
    except Exception as e:
        logger.exception("Clock-in create failed: %s", e)
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "generic_error"),
            log_ctx="whatsapp_clock_in_gps",
        )
        return True

    # Match ``timeclock.views.agent_clock_in_by_phone`` / Miya relay of ``message_for_user``
    # so WhatsApp-direct GPS clock-in reads the same as the Mastra tool path.
    first_name = getattr(user, "first_name", None) or "Team Member"
    success_body = R(user, "clockin_recorded", name=first_name)
    if not _safe_whatsapp_text_send(phone_digits, success_body, log_ctx="whatsapp_clock_in_gps_success"):
        logger.warning("WhatsApp clock-in success send failed; trying localized copy")
        _safe_whatsapp_text_send(
            phone_digits,
            R(user, "clockin_ok", time=timezone.now().strftime("%H:%M")),
            log_ctx="whatsapp_clock_in_gps_success_fallback",
        )

    try:
        if not active_shift:
            try:
                from scheduling.standing_checklist import ensure_checklist_shift_for_staff

                active_shift = ensure_checklist_shift_for_staff(user, create_adhoc=True)
            except Exception:
                logger.exception("WhatsApp clock-in: standing checklist shift failed")
        if active_shift:
            checklist_started = notification_service.start_conversational_checklist_after_clock_in(
                user, active_shift, phone_digits=phone_digits
            )
            if not checklist_started:
                session.state = "idle"
                session.save(update_fields=["state"])
        else:
            session.state = "idle"
            session.save(update_fields=["state"])
    except Exception:
        logger.exception("WhatsApp clock-in: checklist or session cleanup failed (non-fatal)")
        try:
            session.state = "idle"
            session.save(update_fields=["state"])
        except Exception:
            pass
    return True


def _normalize_clock_in_intent(body):
    """
    Detect clock-in intent: case-insensitive, ignore minor punctuation, handle variations.
    Returns True if the message means "I want to clock in".
    """
    if not body or not isinstance(body, str):
        return False
    # Normalize: lower, strip, collapse spaces, remove common punctuation
    raw = body.strip().lower()
    normalized = ''.join(c for c in raw if c.isalnum() or c.isspace())
    normalized = ' '.join(normalized.split())
    if not normalized:
        return False
    exact = normalized in (
        'clock in', 'clockin',
        'pointer', 'pointage', 'je pointe', 'je veux pointer',
    )
    phrases = (
        'want to clock in', 'want to do clock in', 'clock me in', 'i want to clock in',
        'i need to clock in', 'can you clock me in', 'please clock me in', 'id like to clock in',
        'i would like to clock in', 'do clock in', 'let me clock in', 'need to clock in',
        'wanna clock in', 'going to clock in', 'id like to clock in',
        'start my shift', 'im here',
        'je veux pointer', 'je pointe', 'pointage entree',
        'بغيت نبدا', 'سجل دخول',
    )
    return exact or any(p in normalized for p in phrases)


def _normalize_what_next_intent(body):
    """Staff companion: 'what should I do next?' / 'what's next?'"""
    if not body or not isinstance(body, str):
        return False
    raw = body.strip().lower()
    normalized = "".join(c for c in raw if c.isalnum() or c.isspace())
    normalized = " ".join(normalized.split())
    if not normalized:
        return False
    phrases = (
        "what should i do next",
        "whats next",
        "what next",
        "what do i do next",
        "what are my tasks",
        "what are my tasks today",
        "show my tasks",
        "my tasks today",
        "que faire maintenant",
        "que faire ensuite",
    )
    return any(p in normalized for p in phrases)


def _normalize_start_checklist_intent(body):
    """
    Detect start-checklist intent: case-insensitive, variation tolerant.
    Returns True if the message means "I want to start my checklist / task checklist".
    """
    if not body or not isinstance(body, str):
        return False
    if _normalize_what_next_intent(body):
        return True
    raw = body.strip().lower()
    normalized = ''.join(c for c in raw if c.isalnum() or c.isspace())
    normalized = ' '.join(normalized.split())
    if not normalized:
        return False
    phrases = (
        'start checklist', 'start my checklist', 'start the checklist',
        'lets start the task checklist', 'let\'s start the task checklist',
        'start task checklist', 'begin checklist', 'start my tasks',
        'start the task checklist', 'run checklist', 'do my checklist',
        "let's begin tasks", 'lets begin tasks',
    )
    return any(p in normalized for p in phrases)


def _process_whatsapp_staff_escalation(
    notification_service,
    user,
    phone_digits,
    session,
    raw_body: str,
    *,
    wamid: str = "",
    msg: dict | None = None,
) -> bool:
    """
    Create a StaffRequest for staff→manager escalations (wages, payslip, etc.).
    Returns True when handled (caller should continue the webhook loop).

    Creates the inbox row immediately on the first ask (no fake confirmation card).
    ``Yes, send it`` recovers the original ask from session pending or WhatsApp quotes.
    """
    from staff.whatsapp_escalation import (
        classify_whatsapp_escalation,
        extract_escalation_text_from_whatsapp_message,
        is_cancel_send_reply,
        is_confirm_send_reply,
    )
    from staff.whatsapp_request_ingest import ingest_staff_escalation_from_whatsapp

    raw = (raw_body or "").strip()
    if not raw and not msg:
        return False

    # `user` may still be None here (unlinked number) — get_effective_language
    # degrades to English in that case, same as everywhere else in this module.
    lang = get_effective_language(user=user)

    if is_cancel_send_reply(raw):
        if session:
            ctx = dict(getattr(session, "context", None) or {})
            ctx.pop("pending_staff_escalation", None)
            session.context = ctx
            session.save(update_fields=["context"])
        notification_service.send_whatsapp_text(
            phone_digits,
            tr("escalation.cancelled", lang),
        )
        return True

    candidates = extract_escalation_text_from_whatsapp_message(msg, raw)
    routed = None

    if is_confirm_send_reply(raw):
        pending = (getattr(session, "context", None) or {}).get("pending_staff_escalation") or {}
        if pending.get("description"):
            routed = {
                "category": pending.get("category") or "OTHER",
                "subject": pending.get("subject") or pending["description"][:200],
                "description": pending["description"],
            }
        else:
            for candidate in candidates:
                if is_confirm_send_reply(candidate) or is_cancel_send_reply(candidate):
                    continue
                routed = classify_whatsapp_escalation(candidate)
                if routed:
                    break
            if not routed:
                # Last resort: any stored last escalation on this session
                last = (getattr(session, "context", None) or {}).get("last_staff_escalation") or {}
                if last.get("description"):
                    routed = {
                        "category": last.get("category") or "OTHER",
                        "subject": last.get("subject") or last["description"][:200],
                        "description": last["description"],
                    }
    else:
        for candidate in candidates:
            routed = classify_whatsapp_escalation(candidate)
            if routed:
                break

    if not routed:
        if is_confirm_send_reply(raw):
            notification_service.send_whatsapp_text(
                phone_digits,
                tr("escalation.retry_prompt", lang),
            )
            return True
        return False

    # Remember for a possible follow-up "Yes, send it" if Mastra raced a confirm UI.
    if session:
        try:
            ctx = dict(getattr(session, "context", None) or {})
            ctx["last_staff_escalation"] = {
                "category": routed.get("category") or "OTHER",
                "subject": routed.get("subject") or "",
                "description": routed.get("description") or "",
            }
            # Keep pending only until we successfully ingest (confirm path uses it).
            if not is_confirm_send_reply(raw):
                ctx["pending_staff_escalation"] = ctx["last_staff_escalation"]
            session.context = ctx
            session.save(update_fields=["context"])
        except Exception:
            logger.warning("WhatsApp staff escalation: could not persist session context", exc_info=True)

    if not user:
        notification_service.send_whatsapp_text(
            phone_digits,
            "Please link your phone number in your profile to use this feature.",
        )
        return True

    try:
        reply = ingest_staff_escalation_from_whatsapp(
            user=user,
            phone_digits=phone_digits,
            subject=routed["subject"],
            description=routed["description"],
            agent_category=routed.get("category"),
            external_id=wamid or "",
        )
        if session:
            try:
                ctx = dict(getattr(session, "context", None) or {})
                ctx.pop("pending_staff_escalation", None)
                # Prevent "Yes, send it" from creating a duplicate after we already ingested.
                ctx.pop("last_staff_escalation", None)
                session.context = ctx
                session.save(update_fields=["context"])
            except Exception:
                pass
        notification_service.send_whatsapp_text(phone_digits, reply)
    except Exception:
        logger.exception("WhatsApp staff escalation ingest failed phone=%s", phone_digits)
        notification_service.send_whatsapp_text(
            phone_digits,
            tr("escalation.ingest_failed", lang),
        )
    return True


def _get_shift_for_checklist(user, *, allow_standing: bool = True):
    """
    Return today's shift for checklist: prefer the current or next shift (one that hasn't ended yet).
    Staff can have multiple shifts per day; we pick the one that is in progress or upcoming so the
    checklist runs for the right shift, not an already-ended one.

    When ``allow_standing`` is True and the staff has Processes & Tasks standing
    assignments but no scheduled shift, create/reuse an ad-hoc day container.
    """
    if not user:
        return None
    try:
        from scheduling.standing_checklist import ensure_checklist_shift_for_staff

        if allow_standing:
            shift = ensure_checklist_shift_for_staff(user, create_adhoc=True)
            if shift:
                return shift
    except Exception:
        logger.exception("_get_shift_for_checklist standing resolve failed user=%s", getattr(user, "id", None))

    now = timezone.now()
    today = timezone.localdate()
    qs = AssignedShift.objects.filter(
        Q(staff=user) | Q(staff_members=user),
        shift_date=today,
        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'],
    ).distinct().select_related('staff').order_by('start_time')

    total = qs.count()
    if total == 0:
        all_today = AssignedShift.objects.filter(shift_date=today).filter(
            Q(staff=user) | Q(staff_members=user)
        ).values_list('id', 'status', named=True)
        logger.warning(
            "_get_shift_for_checklist: 0 active shifts for user %s on %s. "
            "All shifts for this user today (any status): %s",
            user.id, today, list(all_today),
        )
        return None

    current_or_next = qs.filter(end_time__gt=now).order_by('start_time').first()
    if current_or_next:
        return current_or_next
    # Also try shifts with null end_time
    null_end = qs.filter(end_time__isnull=True).order_by('start_time').first()
    if null_end:
        return null_end
    return qs.first()


def _get_shift_for_clock_in(user):
    """
    When staff has a scheduled shift today, return the AssignedShift that falls
    within the allowed clock-in window (CLOCK_IN_WINDOW_MINUTES_BEFORE /
    CLOCK_IN_WINDOW_MINUTES_AFTER around shift start).

    Returns None if there is no matching shift — staff may still clock in
    (unplanned attendance); GPS and duplicate same-day rules still apply.
    """
    if not user:
        return None
    from datetime import timedelta
    now = timezone.now()
    today = timezone.localdate()
    window_before = timedelta(minutes=getattr(dj_settings, 'CLOCK_IN_WINDOW_MINUTES_BEFORE', 30))
    window_after = timedelta(minutes=getattr(dj_settings, 'CLOCK_IN_WINDOW_MINUTES_AFTER', 15))
    qs = AssignedShift.objects.filter(
        Q(staff=user) | Q(staff_members=user),
        shift_date=today,
        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'],
    ).distinct().select_related('staff')
    for shift in qs.order_by('start_time'):
        if not shift.start_time:
            continue
        start = shift.start_time if timezone.is_aware(shift.start_time) else timezone.make_aware(shift.start_time)
        earliest = start - window_before
        latest = start + window_after
        if earliest <= now <= latest:
            return shift
    return None


def _sync_checklist_progress_create(shift, staff, phone_digits, task_ids):
    """Create ShiftChecklistProgress when starting a WhatsApp checklist."""
    try:
        ShiftChecklistProgress.objects.update_or_create(
            shift=shift,
            staff=staff,
            defaults={
                'channel': 'whatsapp',
                'phone': phone_digits,
                'task_ids': task_ids,
                'current_task_id': task_ids[0] if task_ids else '',
                'responses': {},
                'status': 'IN_PROGRESS',
            }
        )
    except Exception as e:
        logger.warning("ShiftChecklistProgress create failed: %s", e)


def _sync_checklist_progress_update(shift_id, staff, checklist_dict):
    """Update ShiftChecklistProgress when checklist state changes."""
    if not shift_id or not staff:
        return
    try:
        shift_obj = AssignedShift.objects.filter(id=shift_id).first()
        if not shift_obj:
            return
        prog = ShiftChecklistProgress.objects.filter(
            shift=shift_obj, staff=staff, status='IN_PROGRESS'
        ).first()
        if prog:
            prog.task_ids = checklist_dict.get('tasks', prog.task_ids)
            prog.current_task_id = checklist_dict.get('current_task_id', '')
            prog.responses = checklist_dict.get('responses', {})
            prog.save(update_fields=['task_ids', 'current_task_id', 'responses', 'updated_at'])
    except Exception as e:
        logger.warning("ShiftChecklistProgress update failed: %s", e)


def _sync_checklist_progress_complete(shift_id, staff):
    """Mark ShiftChecklistProgress completed and archive full compliance snapshot."""
    if not shift_id or not staff:
        return
    try:
        from scheduling.checklist_completion import finalize_shift_checklist_completion

        prog = ShiftChecklistProgress.objects.filter(
            shift_id=shift_id, staff=staff, status="IN_PROGRESS"
        ).first()
        if not prog:
            prog = ShiftChecklistProgress.objects.filter(
                shift_id=shift_id, staff=staff
            ).order_by("-updated_at").first()
        if prog:
            finalize_shift_checklist_completion(prog, staff)
    except Exception as e:
        logger.warning("ShiftChecklistProgress complete failed: %s", e)


def _sync_checklist_progress_cancel(shift_id, staff):
    """Mark ShiftChecklistProgress as cancelled (shift ended, etc)."""
    if not shift_id or not staff:
        return
    try:
        shift_obj = AssignedShift.objects.filter(id=shift_id).first()
        if not shift_obj:
            return
        ShiftChecklistProgress.objects.filter(
            shift=shift_obj, staff=staff, status='IN_PROGRESS'
        ).update(status='CANCELLED', completed_at=timezone.now())
    except Exception as e:
        logger.warning("ShiftChecklistProgress cancel failed: %s", e)


def _handle_checklist_response(notification_service, session, user, phone_digits, response_value):
    """
    Process one checklist step response (Yes/No/N/A) from button or typed text.
    Returns True if the response was handled (caller should continue), False otherwise.
    """
    if session.state != 'in_checklist':
        return False
    from scheduling.models import ShiftTask, TaskVerificationRecord
    checklist = session.context.get('checklist', {})
    shift_id = checklist.get('shift_id')
    if shift_id and user:
        prog = ShiftChecklistProgress.objects.filter(shift_id=shift_id, staff=user).first()
        if prog and prog.status in ('INCOMPLETE_SHIFT_END', 'CANCELLED'):
            notification_service.send_whatsapp_text(
                phone_digits,
                "This checklist was closed because your shift ended. Contact your manager if you need to update it."
            )
            return True
    tasks = checklist.get('tasks', [])
    responses = checklist.get('responses', {})
    current_task_id = checklist.get('current_task_id')
    if not current_task_id and tasks:
        current_index = int(checklist.get('current_index', 0) or 0)
        if 0 <= current_index < len(tasks):
            current_task_id = tasks[current_index]
    if not current_task_id and tasks:
        current_task_id = tasks[0]
    if not current_task_id:
        return False
    if str(current_task_id) in responses:
        return True

    branch_outcome = {"action": None, "result": None, "flow": "next"}

    try:
        task = ShiftTask.objects.get(id=current_task_id)
        if shift_id and user:
            sft = AssignedShift.objects.filter(id=shift_id).first()
            if sft and sft.end_time and timezone.now() > sft.end_time:
                notification_service.send_whatsapp_text(phone_digits, "⏱️ Shift ended. Checklist paused.")
                _sync_checklist_progress_cancel(shift_id, user)
                session.context.pop('checklist', None)
                session.state = 'idle'
                session.save(update_fields=['state', 'context'])
                return True

        from scheduling.checklist_photo import (
            arm_whatsapp_photo_await,
            photo_prompt_for_task,
            task_requires_photo,
        )
        from scheduling.checklist_branch_actions import (
            apply_checklist_branch,
            find_next_checklist_task,
        )

        # Yes + photo proof → arm image handler; do not advance until photo arrives
        if response_value == 'yes' and task_requires_photo(task):
            arm_whatsapp_photo_await(
                phone=phone_digits,
                user=user,
                task=task,
                shift_id=str(shift_id) if shift_id else None,
            )
            checklist['current_task_id'] = current_task_id
            session.context['checklist'] = checklist
            notification_service.send_whatsapp_text(
                phone_digits, photo_prompt_for_task(task, user=user)
            )
            return True

        responses[current_task_id] = response_value
        checklist['responses'] = responses
        session.context['checklist'] = checklist
        _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)

        if response_value == 'yes':
            task.status = 'COMPLETED'
            task.completed_at = timezone.now()
            task.save(update_fields=['status', 'completed_at'])
            try:
                TaskVerificationRecord.objects.create(
                    task=task,
                    submitted_by=user,
                    checklist_responses={'response': 'yes', 'checklist_item_id': str(task.id), 'shift_id': str(task.shift_id)},
                )
            except Exception:
                pass
        elif response_value == 'n_a':
            task.status = 'CANCELLED'
            task.notes = (task.notes or '') + f"\nN/A ({timezone.now().strftime('%H:%M')})"
            task.save(update_fields=['status', 'notes'])
            try:
                TaskVerificationRecord.objects.create(
                    task=task,
                    submitted_by=user,
                    checklist_responses={'response': 'n_a', 'checklist_item_id': str(task.id), 'shift_id': str(task.shift_id)},
                )
            except Exception:
                pass
        elif response_value == 'no':
            task.status = 'IN_PROGRESS'
            task.started_at = task.started_at or timezone.now()
            task.notes = (task.notes or '') + f"\nNot complete ({timezone.now().strftime('%H:%M')})"
            task.save(update_fields=['status', 'started_at', 'notes'])

        branch_outcome = {"action": None, "result": None, "flow": "next"}
        if response_value in ('yes', 'no'):
            try:
                branch_outcome = apply_checklist_branch(
                    shift_task=task,
                    staff_user=user,
                    answer=response_value,
                )
            except Exception:
                logger.exception(
                    "checklist branch action failed (legacy WA) task=%s answer=%s",
                    task.id,
                    response_value,
                )
            try:
                TaskVerificationRecord.objects.create(
                    task=task,
                    submitted_by=user,
                    checklist_responses={
                        'response': response_value,
                        'checklist_item_id': str(task.id),
                        'shift_id': str(task.shift_id),
                        'branch': branch_outcome.get('action'),
                    },
                )
            except Exception:
                pass

        if response_value == 'no':
            try:
                AuditTrailService.log_activity(
                    user=user,
                    action=AuditActionType.PROGRESS_UPDATE,
                    description=f"Checklist task marked No — follow-up needed: {getattr(task, 'title', 'Task')}",
                    content_object=task,
                    new_values={'response': 'no', 'shift_id': str(task.shift_id), 'task_id': str(task.id)},
                    severity=AuditSeverity.MEDIUM,
                    metadata={
                        'source': 'whatsapp_checklist',
                        'follow_up': True,
                        'branch': branch_outcome.get('action'),
                    },
                )
            except Exception:
                pass

            if (branch_outcome.get("action") or {}).get("type") == "alert":
                names = [
                    n.get("name")
                    for n in ((branch_outcome.get("result") or {}).get("notified") or [])
                    if n.get("name")
                ]
                who = f" ({', '.join(names)})" if names else ""
                notification_service.send_whatsapp_text(
                    phone_digits,
                    f"Got it — *{task.title}* flagged for follow-up{who}. Continuing…",
                )
            elif branch_outcome.get("flow") != "end":
                checklist['pending_task_id'] = current_task_id
                checklist['responses'] = responses
                session.context['checklist'] = checklist
                session.state = 'checklist_followup'
                session.save(update_fields=['state', 'context'])
                follow_msg = (
                    f"Got it — *{task.title}* isn't complete yet.\n\n"
                    "What would you like to do?"
                )
                follow_buttons = [
                    {"id": "need_help", "title": "❓ Need help"},
                    {"id": "delay", "title": "⏳ Delay"},
                    {"id": "skip", "title": "⏭️ Skip"}
                ]
                notification_service.send_whatsapp_buttons(phone_digits, follow_msg, follow_buttons)
                return True

        if branch_outcome.get("flow") == "end":
            notification_service.send_whatsapp_text(
                phone_digits,
                f"Got it — checklist stopped after *{task.title}*.",
            )
            _sync_checklist_progress_complete(checklist.get('shift_id'), user)
            session.context.pop('checklist', None)
            session.state = 'idle'
            session.save(update_fields=['state', 'context'])
            return True

        session.save(update_fields=['context'])
        next_task, next_idx = find_next_checklist_task(
            tasks, responses, branch_outcome=branch_outcome
        )
        if not next_task:
            completed = sum(1 for r in responses.values() if r == 'yes')
            total = len(tasks)
            completion_msg = (
                f"🎉 *Checklist Complete!*\n\n"
                f"✅ {completed}/{total} items confirmed\n\n"
                "Great job! Your checklist is complete — everything is saved for your manager."
            )
            notification_service.send_whatsapp_text(phone_digits, completion_msg)
            _sync_checklist_progress_complete(checklist.get('shift_id'), user)
            session.context.pop('checklist', None)
            session.state = 'idle'
            session.save(update_fields=['state', 'context'])
            return True

        next_task_id = str(next_task.id)
        checklist['current_task_id'] = next_task_id
        session.context['checklist'] = checklist
        _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)
        session.save(update_fields=['context'])
        notification_service._send_task_step_to_whatsapp(
            phone_digits, next_task, next_idx or 1, len(tasks), session
        )
        return True
    except ShiftTask.DoesNotExist:
        return True


def _attach_whatsapp_media_to_incident(notification_service, ticket, media_id, mime_type=None, filename=None, caption=None, user=None):
    """Download WhatsApp media by media_id and save to SafetyConcernReport (S3 + photo_evidence)."""
    if not media_id or not ticket:
        logger.warning("_attach_whatsapp_media_to_incident: missing media_id=%s or ticket=%s", media_id, ticket)
        return
    try:
        from notifications.media_persist import download_whatsapp_media
        from staff.incident_evidence import (
            append_incident_file_attachment,
            append_incident_photo_evidence,
        )

        file_bytes, resolved_mime, resolved_name = download_whatsapp_media(media_id)
        if not file_bytes:
            logger.warning(
                "_attach_whatsapp_media_to_incident: download returned empty for media_id=%s",
                media_id,
            )
            return

        mime = (mime_type or resolved_mime or "image/jpeg").split(";")[0].strip()
        name = (filename or resolved_name or f"incident_{ticket.id}.jpg").strip()
        is_image = mime.lower().startswith("image/") or not mime.lower()

        if is_image:
            append_incident_photo_evidence(
                ticket,
                file_bytes=file_bytes,
                mime_type=mime,
                filename=name,
                media_id=media_id,
                caption=caption or "",
                source="whatsapp",
                submitted_by=user,
            )
        else:
            append_incident_file_attachment(
                ticket,
                file_bytes=file_bytes,
                mime_type=mime,
                filename=name,
                source="whatsapp",
            )
        logger.info(
            "Attached WhatsApp %s to incident %s (%d bytes)",
            "photo" if is_image else "file",
            ticket.id,
            len(file_bytes),
        )
    except Exception as e:
        logger.warning("Could not attach WhatsApp media to incident %s: %s", getattr(ticket, 'id', '?'), e)


def _attach_whatsapp_photo_to_incident(notification_service, ticket, media_id, mime_type=None):
    """Backward-compatible wrapper for image-only callers."""
    _attach_whatsapp_media_to_incident(notification_service, ticket, media_id, mime_type)


def _notify_managers_of_whatsapp_incident(ticket):
    """Notify configured category owners in-app (not every manager)."""
    try:
        from staff.incident_assignee_notify import notify_incident_category_owners_in_app

        notify_incident_category_owners_in_app(ticket)
    except Exception as e:
        logger.warning("WhatsApp incident notify owners failed: %s", e)


def _finalize_whatsapp_incident(notification_service, ticket, session, raw_body, user, phone_digits, *, incident_type: str):
    """Attach stored photo, pin widgets, notify Mastra + managers after SafetyConcernReport create."""
    try:
        from dashboard.category_routing import ensure_dashboard_widgets_for_managers

        ensure_dashboard_widgets_for_managers(ticket.restaurant, incident=True)
    except Exception:
        logger.warning("ensure_dashboard_widgets_for_managers(incident) failed", exc_info=True)

    media_id = None
    mime = None
    if session:
        media_id = (session.context or {}).get('incident_photo_media_id')
        mime = (session.context or {}).get('incident_photo_mime_type')
    if media_id:
        _attach_whatsapp_media_to_incident(
            notification_service, ticket, media_id, mime, user=user
        )
        if session:
            session.context.pop('incident_photo_media_id', None)
            session.context.pop('incident_photo_mime_type', None)

    # Category-owner in-app + WhatsApp notifications are handled by
    # staff.signals.safety_report_notify_assignee_whatsapp on create.


def _looks_like_skip_incident_photo(text: str) -> bool:
    """Staff opts out of sending incident photo evidence."""
    import re

    t = (text or "").strip().lower()
    if not t:
        return False
    if t in {"skip", "no", "later", "non", "pass", "passer", "cancel"}:
        return True
    return bool(
        re.search(
            r"\b(no\s+photo|skip\s+photo|without\s+photo|can'?t\s+(?:send|take)|"
            r"pas\s+de\s+photo|sans\s+photo)\b",
            t,
            re.I,
        )
    )


def _ticket_has_photo(ticket) -> bool:
    from staff.incident_evidence import incident_has_photo_evidence

    return incident_has_photo_evidence(ticket)


def _finish_whatsapp_incident_turn(
    notification_service,
    ticket,
    session,
    raw_body,
    user,
    phone_digits,
    *,
    incident_type: str,
    occurred_at=None,
    R=None,
):
    """
    Finalize incident, notify managers, confirm to staff.
    If no photo is attached yet, prompt for one (awaiting_incident_photo).
    """
    _finalize_whatsapp_incident(
        notification_service,
        ticket,
        session,
        raw_body,
        user,
        phone_digits,
        incident_type=incident_type,
    )
    ticket.refresh_from_db()

    when = occurred_at or getattr(ticket, "occurred_at", None) or timezone.now()
    occurred_str = when.strftime("%Y-%m-%d %H:%M") if when else "—"
    notification_service.send_whatsapp_text(
        phone_digits,
        R(
            user,
            "incident_recorded",
            ticket_id=str(ticket.id)[:8],
            incident_type=ticket.incident_type,
            occurred_at=occurred_str,
        ),
    )

    if not _ticket_has_photo(ticket):
        session.state = "awaiting_incident_photo"
        session.context["incident_ticket_id"] = str(ticket.id)
        session.context.pop("pending_incident", None)
        session.save(update_fields=["state", "context"])
        notification_service.send_whatsapp_text(phone_digits, R(user, "incident_ask_photo"))
        return

    session.state = "idle"
    session.context.pop("pending_incident", None)
    session.context.pop("incident_ticket_id", None)
    session.save(update_fields=["state", "context"])


def _create_safety_concern_from_whatsapp(
    *,
    user,
    description: str,
    incident_type: str | None,
    severity: str | None = None,
    occurred_at=None,
    shift=None,
    audio_evidence=None,
):
    """Create SafetyConcernReport with normalized category, location, and default assignee."""
    from staff.models_task import SafetyConcernReport
    from staff.incident_routing import (
        normalize_incident_category_for_storage,
        resolve_default_assignee_for_incident_type,
    )

    itype = normalize_incident_category_for_storage(incident_type or 'General')
    when = occurred_at or timezone.now()
    loc = extract_incident_location(description) or None
    assignee = resolve_default_assignee_for_incident_type(user.restaurant, itype)
    return SafetyConcernReport.objects.create(
        restaurant=user.restaurant,
        reporter=user,
        is_anonymous=False,
        incident_type=itype,
        title=f"{itype} incident",
        description=(description or '').strip(),
        location=loc,
        severity=severity or infer_severity(description),
        status='OPEN',
        occurred_at=when,
        shift=shift,
        assigned_to=assignee,
        audio_evidence=audio_evidence or [],
    )


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationListView(generics.ListAPIView):
    """List notifications for the authenticated user"""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        user = self.request.user
        # Only show notifications from the last 12 hours (older ones are auto-cleared from the list)
        cutoff = timezone.now() - timedelta(hours=12)
        # Be defensive: eagerly load related sender/recipient and attachments
        # to avoid lazy-loading surprises that can bubble up during serialization
        queryset = (
            Notification.objects
            .filter(recipient=user, created_at__gte=cutoff)
            .select_related('recipient', 'sender')
            .prefetch_related('attachments')
            .order_by('-created_at')
        )
        
        # Filter by read status
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(read_at__isnull=False)
            else:
                queryset = queryset.filter(read_at__isnull=True)
        
        # Filter by notification type
        notification_type = self.request.query_params.get('type')
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)
        
        # Filter by priority
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        
        return queryset

    def list(self, request, *args, **kwargs):
        """Ensure the endpoint never 500s; return an empty, paginated payload on error."""
        try:
            return super().list(request, *args, **kwargs)
        except Exception as e:
            # We deliberately avoid exposing internals to the client. This preserves
            # dashboard stability if a bad row or attachment causes a serialization error.
            return Response({
                'count': 0,
                'next': None,
                'previous': None,
                'results': [],
                'success': False,
                'error': 'notifications_unavailable'
            }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_notification_read(request, notification_id):
    """Mark a specific notification as read"""
    try:
        notification = get_object_or_404(
            Notification, 
            id=notification_id, 
            recipient=request.user
        )
        
        if not notification.read_at:
            notification.mark_as_read()
            
        return Response({
            'success': True,
            'message': 'Notification marked as read',
            'read_at': notification.read_at
        })
        
    except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAdminOrManager])
@parser_classes([MultiPartParser, FormParser])
def create_announcement(request):
    """Create and send announcement to all restaurant staff"""
    try:
        ctx = build_tenant_context(request)
        if not ctx:
            return Response({'success': False, 'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = AnnouncementCreateSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create notifications for all staff inside a transaction
        with transaction.atomic():
            notifications = serializer.create_notifications(sender=request.user)
            # Handle attachments if provided
            files = request.FILES.getlist('attachments')
            if files:
                for notification in notifications:
                    for f in files:
                        att = NotificationAttachment(
                            notification=notification,
                            file=f,
                            original_name=getattr(f, 'name', ''),
                            content_type=getattr(f, 'content_type', ''),
                            file_size=getattr(f, 'size', 0),
                        )
                        att.save()
        targeted = bool(
            serializer.validated_data.get('recipients_staff_ids') or 
            serializer.validated_data.get('recipients_departments')
        )

        # Handle scheduling: if schedule_for is set in the future, mark as scheduled and do not send now
        schedule_for = serializer.validated_data.get('schedule_for')
        if schedule_for and schedule_for > timezone.now():
            for notification in notifications:
                notification.delivery_status = {
                    'status': 'SCHEDULED',
                    'scheduled_for': schedule_for.isoformat(),
                }
                notification.save(update_fields=['delivery_status'])
            logger.info("Announcement scheduled for future delivery.")
        else:
            # Send via notification service for immediate delivery with multi-channel support
            # Channels can be provided as list in request.data['channels'] (FormData may
            # send repeated keys — QueryDict.getlist handles that).
            raw_channels = request.data.getlist('channels') if hasattr(request.data, 'getlist') else None
            if raw_channels:
                channels = [str(c).strip() for c in raw_channels if str(c).strip()]
            else:
                single = request.data.get('channels')
                if isinstance(single, (list, tuple)):
                    channels = [str(c).strip() for c in single if str(c).strip()]
                elif single:
                    channels = [str(single).strip()]
                else:
                    channels = ['app', 'whatsapp']
            if not channels:
                channels = ['app', 'whatsapp']
            override = bool(request.data.get('override_preferences', False))
            # If override, include more channels by default
            if override and 'sms' not in channels:
                channels = list(set(channels + ['email', 'push', 'sms']))
            for notification in notifications:
               notification_service.send_custom_notification(
                recipient=notification.recipient,
                notification=notification,            # <── Use existing object
                channels=channels,
                override_preferences=override
                )
            logger.info("Announcement sent via notification service channels=%s", channels)
        
        return Response({
            'success': True,
            'message': (
                f"Announcement sent to {len(notifications)} targeted recipients"
                if targeted else
                f"Announcement sent to {len(notifications)} staff members"
            ),
            'notification_count': len(notifications),
            'title': serializer.validated_data['title'],
            'targeted': targeted
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def acknowledge_announcement(request, notification_id):
    """Explicit acknowledgement endpoint; marks as read and returns status"""
    try:
        notification = get_object_or_404(
            Notification,
            id=notification_id,
            recipient=request.user,
            notification_type='ANNOUNCEMENT'
        )
        if not notification.read_at:
            notification.mark_as_read()
        return Response({
            'success': True,
            'acknowledged_at': notification.read_at,
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def report_delivery_issue(request):
    """Staff can report undelivered announcements or issues"""
    try:
        description = request.data.get('description')
        notification_id = request.data.get('notification_id')
        if not description:
            return Response({'success': False, 'error': 'description is required'}, status=status.HTTP_400_BAD_REQUEST)
        notification = None
        if notification_id:
            try:
                notification = Notification.objects.get(id=notification_id, recipient=request.user)
            except Notification.DoesNotExist:
                notification = None
        issue = NotificationIssue.objects.create(
            reporter=request.user,
            notification=notification,
            description=description
        )
        return Response({'success': True, 'issue_id': issue.id})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def health_check_notifications(request):
    """Run basic configuration health checks for notification delivery"""
    try:
        checks = {}
        # Email
        from django.conf import settings as dj_settings
        checks['email_configured'] = bool(getattr(dj_settings, 'EMAIL_BACKEND', '')) and bool(getattr(dj_settings, 'DEFAULT_FROM_EMAIL', ''))
        # Firebase
        import firebase_admin
        checks['firebase_initialized'] = bool(firebase_admin._apps)
        # WhatsApp
        from core.whatsapp_config import probe_whatsapp_credentials

        probe = probe_whatsapp_credentials()
        checks['whatsapp_configured'] = bool(getattr(dj_settings, 'WHATSAPP_ACCESS_TOKEN', None)) and bool(getattr(dj_settings, 'WHATSAPP_PHONE_NUMBER_ID', None))
        checks['whatsapp_token_valid'] = probe.get('ok', False)
        if not probe.get('ok'):
            checks['whatsapp_probe'] = {
                k: probe[k]
                for k in ('reason', 'message', 'status_code', 'token_length')
                if k in probe
            }
        checks['whatsapp_webhook_configured'] = bool(get_whatsapp_verify_token())
        # SMS/Twilio
        checks['twilio_configured'] = bool(getattr(dj_settings, 'TWILIO_ACCOUNT_SID', None)) and bool(getattr(dj_settings, 'TWILIO_AUTH_TOKEN', None)) and bool(getattr(dj_settings, 'TWILIO_FROM_NUMBER', None))
        # Device tokens count
        checks['device_tokens_count'] = DeviceToken.objects.count()
        # Staff preferences sanity: count users with announcement disabled
        checks['announcement_disabled_count'] = NotificationPreference.objects.filter(announcement_notifications=False).count()
        return Response({'success': True, 'checks': checks})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def whatsapp_activity(request):
    try:
        from django.utils import timezone as _tz
        since_param = request.query_params.get('since')
        since = _tz.now() - _tz.timedelta(days=7)
        try:
            if since_param:
                since = _tz.datetime.fromisoformat(since_param)
        except Exception:
            pass
        sessions = WhatsAppSession.objects.filter(last_interaction_at__gte=since).order_by('-last_interaction_at')[:100]
        incidents = NotificationIssue.objects.filter(created_at__gte=since).order_by('-created_at')[:100]
        from timeclock.models import ClockEvent
        clock_events = ClockEvent.objects.filter(timestamp__gte=since, device_id__iexact='whatsapp').order_by('-timestamp')[:100]
        def safe_user(u):
            if not u:
                return None
            return {'id': str(u.id), 'name': u.get_full_name(), 'phone': u.phone}
        return Response({
            'sessions': [{'phone': s.phone, 'user': safe_user(s.user), 'state': s.state, 'last_interaction_at': s.last_interaction_at} for s in sessions],
            'incidents': [{'id': i.id, 'reporter': safe_user(i.reporter), 'description': i.description, 'created_at': i.created_at} for i in incidents],
            'clock_events': [{'id': ce.id, 'staff': safe_user(ce.staff), 'event_type': ce.event_type, 'timestamp': ce.timestamp} for ce in clock_events],
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_all_notifications_read(request):
    """Mark all unread notifications as read for the user"""
    try:
        unread_notifications = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True
        )
        
        count = unread_notifications.count()
        unread_notifications.update(read_at=timezone.now())
        
        return Response({
            'success': True,
            'message': f'{count} notifications marked as read',
            'count': count
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def notification_stats(request):
    """Get notification statistics for the user"""
    try:
        user = request.user
        
        total_count = Notification.objects.filter(recipient=user).count()
        unread_count = Notification.objects.filter(
            recipient=user, 
            read_at__isnull=True
        ).count()
        
        # Count by type
        type_counts = {}
        for notification_type, _ in Notification.NOTIFICATION_TYPES:
            count = Notification.objects.filter(
                recipient=user,
                notification_type=notification_type
            ).count()
            if count > 0:
                type_counts[notification_type] = count
        
        # Count by priority
        priority_counts = {}
        for priority, _ in Notification.PRIORITY_LEVELS:
            count = Notification.objects.filter(
                recipient=user,
                priority=priority
            ).count()
            if count > 0:
                priority_counts[priority] = count
        
        return Response({
            'total_count': total_count,
            'unread_count': unread_count,
            'type_counts': type_counts,
            'priority_counts': priority_counts
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


class NotificationPreferenceView(generics.RetrieveUpdateAPIView):
    """Get and update notification preferences for the authenticated user"""
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        preference, created = NotificationPreference.objects.get_or_create(
            user=self.request.user
        )
        return preference


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def register_device_token(request):
    """Register or update a device token for push notifications"""
    try:
        token = request.data.get('token')
        device_type = request.data.get('device_type', 'UNKNOWN')
        device_name = request.data.get('device_name', '')
        
        if not token:
            return Response({
                'success': False,
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Deactivate existing tokens for this user and device type
        DeviceToken.objects.filter(
            user=request.user,
            device_type=device_type
        ).update(is_active=False)
        
        # Create or update the token
        device_token, created = DeviceToken.objects.update_or_create(
            user=request.user,
            token=token,
            defaults={
                'device_type': device_type,
                'device_name': device_name,
                'is_active': True,
                'last_used': timezone.now()
            }
        )
        
        return Response({
            'success': True,
            'message': 'Device token registered successfully',
            'token_id': device_token.id,
            'created': created
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


def _miya_whatsapp_enabled() -> bool:
    return get_miya_whatsapp_enabled()


@api_view(['GET', 'POST'])
@permission_classes([permissions.AllowAny])
def whatsapp_webhook(request):
    try:
        if request.method == 'GET':
            token = request.query_params.get('hub.verify_token') or request.GET.get('hub.verify_token')
            challenge = request.query_params.get('hub.challenge') or request.GET.get('hub.challenge')
            from core.whatsapp_config import get_whatsapp_verify_token
            from django.http import HttpResponse

            if token and challenge is not None and token == get_whatsapp_verify_token():
                # Meta requires the raw challenge string (not JSON / not int-cast).
                return HttpResponse(str(challenge), content_type="text/plain")
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        payload = request.data

        miya_wa = _miya_whatsapp_enabled()

        entries = payload.get('entry', [])
        
        def lang_for(user):
            try:
                return (user.restaurant.language or 'en').split('-')[0]
            except Exception:
                return 'en'
                
        RESP = {
            'en': {
                'help': 'Welcome to Mizan. Reply with: "clock in", "clock out", "tasks", "order" (guest order), or "report" (incident).',
                'clockin_prompt': 'Please share your live location to clock in.',
                'clockin_tap_location': 'Tap Share Location above to clock in.',
                'clockin_ok': 'Clock-in successful at {time}.',
                'clockin_failed': 'Clock-in failed. You are {distance}m away from the location.',
                'clockout_ok': 'Clock-out recorded. Duration: {duration} hours.',
                'clockout_no': 'You are not currently clocked in.',
                'link_phone': 'Please link your phone number in your profile to use this feature.',
                'tasks_none': 'No active tasks assigned to you.',
                'tasks_list_suffix': 'Reply "complete <number>" to mark a task as done.',
                'task_completed': 'Task marked as completed.',
                'task_verify_photo': 'Please send a photo as evidence to complete this task.',
                'task_verify_done': 'Task completed with photo evidence.',
                'incident_prompt': 'Please describe the incident. Include: type (Safety/Maintenance/HR/Service/Other), what happened, and when it occurred. You can send text, a voice note, or a photo (with optional caption showing the damage).',
                'incident_clarify_audio': 'Thanks — I couldn’t clearly understand that voice note. Please resend it, or reply with: incident type, a brief description, and the time it occurred.',
                'incident_clarify_missing': 'Thanks — before I log this, please clarify: {missing}.',
                'incident_recorded': (
                    'Thank you for taking the time to tell us what happened — we’ve recorded what you shared.\n\n'
                    'Your safety and dignity matter here. The right people on your team will see this and can follow up '
                    'as needed. If you remember anything else, or the situation changes, you can reach out again anytime.\n\n'
                    'If you ever feel unsafe in the moment, use your local emergency number or ask a manager on the floor '
                    'for immediate help.'
                ),
                'incident_ask_photo': (
                    '📷 If you can, please send a photo of the incident so we can attach it to the report. '
                    'Reply *skip* if you can\'t right now.'
                ),
                'incident_photo_attached': '📷 Photo attached — thank you. Management has the full report.',
                'incident_photo_skipped': 'No problem — your report is already logged. Tell a manager on duty if the situation gets worse.',
                'incident_failed': 'Failed to record incident. Please try again.',
                'order_voice_prompt': (
                    '📋 *Guest order*\n\n'
                    'Send a *voice note* or type the order: items and quantities, guest name if you have it, '
                    'phone (helpful for takeout/delivery — optional for dine-in), table or pickup, allergens, '
                    'and special requests.\n\n'
                    'You can send a short note — we’ll save it and you can add details in the app. Reply *cancel* to abort.'
                ),
                'order_recorded': (
                    '✅ *Order saved* — we’ve got it for the team.\n\n'
                    '{preview}\n'
                    '{followup}'
                    'Open *Today’s Orders* on the dashboard anytime to review or add details.'
                ),
                'order_followup_no_phone': (
                    '📌 *Heads up:* No guest phone was detected — the order is still saved. '
                    'Add a number or anything else in *Today’s Orders* whenever you like.\n\n'
                ),
                'order_followup_delivery_no_phone': (
                    '📌 *Delivery:* No phone was mentioned — the order is saved. '
                    'Please add the guest phone in *Today’s Orders* so the team can reach them.\n\n'
                ),
                'order_clarify_audio': (
                    'I couldn’t catch that clearly. Please resend the voice note or type the order '
                    '(items and quantities; add guest name, table, or phone if you have them).'
                ),
                'order_cancelled': 'Order entry cancelled. Send *order* when you want to log a guest order.',
                'order_failed': 'Could not save the order. Please try again or use the app.',
                'unrecognized': 'Unrecognized command. Reply with "help" to see available options.',
                'no_restaurant_linked': "Your account isn't linked to a restaurant yet. Please contact your manager.",
                'location_unreadable': "We couldn't read your location from this message. Please tap Share Location / Current Location and try again.",
                'location_check_error': 'Something went wrong checking your location. Please try again in a moment.',
                'no_geofence_configured': 'Location check is not set up for your restaurant. Please contact your manager to clock in.',
                'outside_geofence': 'You are not within any approved location zone. Please move closer and try again.',
                'share_location_prompt': 'Share your location to clock in.',
                'already_clocked_in': "You're already clocked in (since {time}). Have a great shift {name}!",
                'clockin_recorded': 'Clock-in recorded. Have a great shift {name}!',
                'generic_error': 'Something went wrong. Please try again in a moment.',
                'checklist_invalid_reply': "Hmm, I didn't quite catch that — reply *Yes*, *No*, or *N/A* for this step.",
                'checklist_photo_needed': 'Please send a photo to complete this step. You can complete other tasks later if needed.',
                'checklist_not_ready': (
                    "No checklist is ready yet. Say *start checklist* when you're ready "
                    "(clock-in is optional). Ask your manager to assign a process if needed. "
                    "If it still fails, ask your manager to assign this process to you in Processes & Tasks."
                ),
                'checklist_already_complete': 'Your checklist is already complete. Have a productive shift!',
                'checklist_closed_shift_ended': 'This checklist was closed because your shift ended. Contact your manager if you need to update it.',
                'checklist_in_progress_reply_prompt': "You're in a checklist. Please reply Yes, No, or N/A to the last message.",
                'checklist_start_failed': 'No tasks are assigned for this shift, or something went wrong. Please try again or contact your manager.',
                'incident_manager_notify': (
                    "Heads up — {name} just submitted an incident report ({type}, {date}).\n\n"
                    "First details: {preview}\n\n"
                    "Please review in the dashboard when you can."
                ),
            },
            'ar': {
                'help': 'مرحبًا بكم في ميزان. أجب بـ: "دخول"، "خروج"، "مهام"، أو "بلاغ".',
                'clockin_prompt': 'يرجى مشاركة موقعك المباشر لتسجيل الدخول.',
                'clockin_tap_location': 'انقر فوق مشاركة الموقع أعلاه لتسجيل الدخول.',
                'clockin_ok': 'تم تسجيل الدخول بنجاح في {time}.',
                'clockin_failed': 'فشل تسجيل الدخول. أنت على بعد {distance} متر من الموقع.',
                'clockout_ok': 'تم تسجيل الخروج. المدة: {duration} ساعة.',
                'clockout_no': 'لم يتم تسجيل دخولك حاليًا.',
                'link_phone': 'يرجى ربط رقم هاتفك في ملفك الشخصي لاستخدام هذه الميزة.',
                'tasks_none': 'لا توجد مهام نشطة معينة لك.',
                'tasks_list_suffix': 'أجب بـ "إتمام <رقم>" لتمييز المهمة كمكتملة.',
                'task_completed': 'تم تمييز المهمة كمكتملة.',
                'task_verify_photo': 'يرجى إرسال صورة كدليل لإكمال هذه المهمة.',
                'task_verify_done': 'اكتملت المهمة مع دليل الصور.',
                'incident_prompt': 'يرجى وصف الحادث أو المشكلة. يمكنك إرسال نص أو ملاحظة صوتية.',
                'incident_clarify_audio': 'شكراً — لم أفهم الملاحظة الصوتية بوضوح. يرجى إعادة إرسالها أو الرد بالنص: نوع الحادث، وصف موجز، ووقت الحدوث.',
                'incident_clarify_missing': 'شكراً — قبل التسجيل، يرجى توضيح: {missing}.',
                'incident_recorded': (
                    'شكراً لثقتك ولمشاركة ما حدث — تم حفظ ما أبلغت به.\n\n'
                    'سلامتك واحترامك يهمّنا. سيطلع الفريق المعني على هذا وسيمكن المتابعة عند الحاجة. '
                    'إذا تذكرت تفاصيل إضافية أو تغيّر الوضع، يمكنك التواصل مرة أخرى في أي وقت.\n\n'
                    'إذا شعرت بخطر مباشر، اتصل بخدمات الطوارئ في منطقتك أو أبلغ مديراً في المكان فوراً.'
                ),
                'incident_ask_photo': (
                    '📷 إن أمكن، أرسل صورة للحادث لنرفقها بالبلاغ. '
                    'رد *تخطي* إذا لم تستطع الآن.'
                ),
                'incident_photo_attached': '📷 تم إرفاق الصورة — شكراً. الإدارة لديها التقرير الكامل.',
                'incident_photo_skipped': 'لا مشكلة — تم تسجيل بلاغك. أبلغ مديراً في المكان إذا ساء الوضع.',
                'incident_failed': 'فشل تسجيل الحادث. يرجى المحاولة مرة أخرى.',
                'order_voice_prompt': (
                    '📋 *طلب زبون*\n\n'
                    'أرسل *ملاحظة صوتية* أو اكتب الطلب: الأصناف والكميات، اسم الزبون إن وجد، '
                    'الهاتف (مفيد للسفري/التوصيل — اختياري للجلوس)، الطاولة أو الاستلام، الحساسيات، والملاحظات.\n\n'
                    'يمكنك إرسال ملخص قصير — سنحفظه ويمكنك إكمال التفاصيل من التطبيق. أرسل *إلغاء* للخروج.'
                ),
                'order_recorded': (
                    '✅ *تم حفظ الطلب* — الفريق سيراه.\n\n'
                    '{preview}\n'
                    '{followup}'
                    'يمكنك فتح *طلبات اليوم* من لوحة التحكم في أي وقت للمراجعة أو إضافة تفاصيل.'
                ),
                'order_followup_no_phone': (
                    '📌 *تنبيه:* لم يُكتشف رقم هاتف الزبون — الطلب محفوظ. '
                    'يمكنك إضافة الرقم أو أي تفاصيل في *طلبات اليوم* لاحقاً.\n\n'
                ),
                'order_followup_delivery_no_phone': (
                    '📌 *توصيل:* لم يُذكر رقم — الطلب محفوظ. '
                    'يرجى إضافة هاتف الزبون في *طلبات اليوم* ليتمكن الفريق من التواصل.\n\n'
                ),
                'order_clarify_audio': (
                    'لم أسمع بوضوح. أعد الملاحظة الصوتية أو اكتب الطلب (الأصناف؛ وأضف الاسم أو الطاولة أو الهاتف إن وجدت).'
                ),
                'order_cancelled': 'تم إلغاء إدخال الطلب. أرسل *طلب* عندما تريد تسجيل طلب زبون.',
                'order_failed': 'تعذّر حفظ الطلب. حاول مرة أخرى أو استخدم التطبيق.',
                'unrecognized': 'أمر غير معروف. أجب بـ "مساعدة" لرؤية الخيارات المتاحة.',
                'no_restaurant_linked': 'حسابك غير مرتبط بمطعم بعد. يرجى التواصل مع مديرك.',
                'location_unreadable': 'تعذّر قراءة موقعك من هذه الرسالة. يرجى الضغط على مشاركة الموقع / الموقع الحالي والمحاولة مرة أخرى.',
                'location_check_error': 'حدث خطأ أثناء التحقق من موقعك. يرجى المحاولة مرة أخرى بعد قليل.',
                'no_geofence_configured': 'لم يتم إعداد التحقق من الموقع لمطعمك. يرجى التواصل مع مديرك لتسجيل الحضور.',
                'outside_geofence': 'أنت لست ضمن أي منطقة معتمدة. اقترب أكثر وحاول مرة أخرى.',
                'share_location_prompt': 'شارك موقعك لتسجيل الحضور.',
                'already_clocked_in': 'أنت مسجل حضورك بالفعل (منذ {time}). وردية موفّقة {name}!',
                'clockin_recorded': 'تم تسجيل الحضور. وردية موفّقة {name}!',
                'generic_error': 'حدث خطأ ما. يرجى المحاولة مرة أخرى بعد قليل.',
                'checklist_invalid_reply': 'لم أفهم ذلك جيداً — أجب بـ *نعم* أو *لا* أو *غير منطبق* لهذه الخطوة.',
                'checklist_photo_needed': 'يرجى إرسال صورة لإكمال هذه الخطوة. يمكنك إكمال المهام الأخرى لاحقاً إذا لزم الأمر.',
                'checklist_not_ready': (
                    'لا توجد قائمة تحقق جاهزة الآن. قل *ابدأ المهام* عندما تكون جاهزاً '
                    '(تسجيل الحضور اختياري). اطلب من مديرك تعيين عملية إذا لزم الأمر. '
                    'إذا استمرت المشكلة، اطلب منه تعيين هذه العملية لك في العمليات والمهام.'
                ),
                'checklist_already_complete': 'قائمة التحقق مكتملة بالفعل. وردية موفّقة!',
                'checklist_closed_shift_ended': 'تم إغلاق قائمة التحقق هذه لأن ورديتك انتهت. تواصل مع مديرك لتحديثها.',
                'checklist_in_progress_reply_prompt': 'أنت في قائمة تحقق. يرجى الرد بنعم أو لا أو غير منطبق على آخر رسالة.',
                'checklist_start_failed': 'لا توجد مهام معينة لهذه الوردية، أو حدث خطأ ما. يرجى المحاولة مرة أخرى أو التواصل مع مديرك.',
                'incident_manager_notify': (
                    'تنبيه — قام {name} للتو بتقديم بلاغ حادث ({type}، {date}).\n\n'
                    'التفاصيل الأولية: {preview}\n\n'
                    'يرجى مراجعة لوحة التحكم عندما يمكنك ذلك.'
                ),
            },
            'fr': {
                'help': 'Bienvenue chez Mizan. Répondez par : "clock in", "clock out", "tâches", ou "rapport".',
                'clockin_prompt': 'Veuillez partager votre position en direct pour pointer.',
                'clockin_tap_location': 'Appuyez sur Partager la position ci-dessus pour pointer.',
                'clockin_ok': 'Pointage d\'entrée réussi à {time}.',
                'clockin_failed': 'Échec du pointage. Vous êtes à {distance}m de l\'emplacement.',
                'clockout_ok': 'Pointage de sortie enregistré. Durée : {duration} heures.',
                'clockout_no': 'Vous n\'êtes pas actuellement pointé.',
                'link_phone': 'Veuillez lier votre numéro de téléphone dans votre profil pour utiliser cette fonctionnalité.',
                'tasks_none': 'Aucune tâche assignée.',
                'tasks_list_suffix': 'Répondez "terminer <nombre>" pour marquer une tâche comme terminée.',
                'task_completed': 'Tâche terminée.',
                'task_verify_photo': 'Veuillez envoyer une photo.',
                'task_verify_done': 'Tâche terminée avec photo.',
                'incident_prompt': 'Décrivez l\'incident (texte ou voix).',
                'incident_clarify_audio': 'Merci — je n\'ai pas bien compris le message vocal. Veuillez le renvoyer ou répondre par texte : type d\'incident, description brève, et heure.',
                'incident_clarify_missing': 'Merci — avant d\'enregistrer, veuillez préciser : {missing}.',
                'incident_recorded': (
                    'Merci d’avoir pris le temps de nous dire ce qui s’est passé — nous avons bien enregistré ce que vous avez partagé.\n\n'
                    'Votre sécurité et votre dignité comptent. Les bonnes personnes dans votre équipe verront cela et pourront '
                    'faire le suivi si nécessaire. Si vous vous souvenez d’autres détails ou si la situation évolue, vous pouvez '
                    'nous écrire à tout moment.\n\n'
                    'En cas de danger immédiat, contactez les secours locaux ou parlez immédiatement à un responsable sur place.'
                ),
                'incident_ask_photo': (
                    '📷 Si possible, envoyez une photo de l\'incident pour l\'ajouter au rapport. '
                    'Répondez *passer* si vous ne pouvez pas maintenant.'
                ),
                'incident_photo_attached': '📷 Photo ajoutée — merci. La direction a le rapport complet.',
                'incident_photo_skipped': 'Pas de souci — votre signalement est déjà enregistré. Prévenez un responsable si la situation empire.',
                'incident_failed': 'Échec de l\'enregistrement. Veuillez réessayer.',
                'order_voice_prompt': (
                    '📋 *Commande invité*\n\n'
                    'Envoyez une *note vocale* ou saisissez la commande : articles et quantités, nom du client si vous l’avez, '
                    'téléphone (utile pour emporter/livraison — optionnel sur place), table ou retrait, allergènes, consignes.\n\n'
                    'Un court message suffit — vous pourrez compléter dans l’app. Répondez *annuler* pour quitter.'
                ),
                'order_recorded': (
                    '✅ *Commande enregistrée* — c’est noté pour l’équipe.\n\n'
                    '{preview}\n'
                    '{followup}'
                    'Ouvrez *Commandes du jour* sur le tableau de bord quand vous voulez vérifier ou compléter.'
                ),
                'order_followup_no_phone': (
                    '📌 *Info :* aucun téléphone invité détecté — la commande est bien enregistrée. '
                    'Ajoutez le numéro ou d’autres détails dans *Commandes du jour* quand vous voulez.\n\n'
                ),
                'order_followup_delivery_no_phone': (
                    '📌 *Livraison :* aucun téléphone mentionné — la commande est enregistrée. '
                    'Ajoutez le téléphone de l’invité dans *Commandes du jour* pour que l’équipe puisse le joindre.\n\n'
                ),
                'order_clarify_audio': (
                    'Je n’ai pas bien entendu. Renvoyez la note vocale ou saisissez la commande '
                    '(articles ; nom, table ou téléphone si vous les avez).'
                ),
                'order_cancelled': 'Saisie annulée. Envoyez *commande* pour enregistrer une commande invité.',
                'order_failed': 'Impossible d’enregistrer la commande. Réessayez ou utilisez l’app.',
                'unrecognized': 'Commande non reconnue. Répondez "aide".',
                'no_restaurant_linked': "Votre compte n'est pas encore lié à un restaurant. Contactez votre responsable.",
                'location_unreadable': "Nous n'avons pas pu lire votre position dans ce message. Appuyez sur Partager la position / Position actuelle et réessayez.",
                'location_check_error': 'Un problème est survenu lors de la vérification de votre position. Réessayez dans un instant.',
                'no_geofence_configured': "La vérification de position n'est pas configurée pour votre restaurant. Contactez votre responsable pour pointer.",
                'outside_geofence': "Vous n'êtes dans aucune zone approuvée. Rapprochez-vous et réessayez.",
                'share_location_prompt': 'Partagez votre position pour pointer.',
                'already_clocked_in': 'Vous êtes déjà pointé (depuis {time}). Bon service {name} !',
                'clockin_recorded': 'Pointage enregistré. Bon service {name} !',
                'generic_error': 'Un problème est survenu. Réessayez dans un instant.',
                'checklist_invalid_reply': "Hmm, je n'ai pas compris — répondez *Oui*, *Non* ou *N/A* pour cette étape.",
                'checklist_photo_needed': 'Veuillez envoyer une photo pour terminer cette étape. Vous pourrez faire les autres tâches plus tard si besoin.',
                'checklist_not_ready': (
                    "Aucune checklist n'est prête pour l'instant. Dites *démarrer la checklist* quand vous êtes prêt "
                    "(le pointage est optionnel). Demandez à votre responsable d'assigner un processus si besoin. "
                    "Si ça ne fonctionne toujours pas, demandez-lui de vous assigner ce processus dans Processus & Tâches."
                ),
                'checklist_already_complete': 'Votre checklist est déjà terminée. Bon service !',
                'checklist_closed_shift_ended': 'Cette checklist a été clôturée car votre service est terminé. Contactez votre responsable pour la mettre à jour.',
                'checklist_in_progress_reply_prompt': 'Vous êtes dans une checklist. Répondez Oui, Non ou N/A au dernier message.',
                'checklist_start_failed': "Aucune tâche n'est assignée pour ce service, ou une erreur est survenue. Réessayez ou contactez votre responsable.",
                'incident_manager_notify': (
                    "Attention — {name} vient de soumettre un signalement d'incident ({type}, {date}).\n\n"
                    "Premiers détails : {preview}\n\n"
                    "Merci de vérifier le tableau de bord dès que possible."
                ),
            },
        }
        
        def R(user, key, **kwargs):
            lang = lang_for(user)
            # fallback to English if key missing in lang
            tmpl = RESP.get(lang, RESP['en']).get(key, RESP['en'].get(key, ''))
            if not tmpl:
                return ''
            # Only substitute placeholders present in the template (human copy may omit ticket IDs, etc.)
            names = set(re.findall(r"\{(\w+)\}", tmpl))
            if not names:
                return tmpl
            safe = {k: v for k, v in kwargs.items() if k in names}
            return tmpl.format(**safe)

        def order_recorded_followup(uid_user, order_obj):
            """Confirm with staff when guest phone was not parsed — order is still saved."""
            phone = (getattr(order_obj, "customer_phone", None) or "").strip()
            ot = (getattr(order_obj, "order_type", "") or "").upper()
            if phone:
                return ""
            if ot == "DELIVERY":
                return R(uid_user, "order_followup_delivery_no_phone")
            return R(uid_user, "order_followup_no_phone")

        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                # ------------------------------------------------------------------
                # HANDLE STATUS UPDATES (DELIVERY RECEIPTS)
                # ------------------------------------------------------------------
                statuses = value.get('statuses', [])
                for status_obj in statuses:
                    wamid = status_obj.get('id')
                    status_str = status_obj.get('status')
                    
                    status_map = {
                        'sent': 'SENT',
                        'delivered': 'DELIVERED',
                        'read': 'READ',
                        'failed': 'FAILED'
                    }
                    mapped_status = status_map.get(status_str)
                    if mapped_status:
                        from .models import NotificationLog
                        log = NotificationLog.objects.filter(external_id=wamid).first()
                        if log:
                            log.status = mapped_status
                            update_fields = ['status', 'delivered_at']
                            if mapped_status == 'DELIVERED' or mapped_status == 'READ':
                                if not log.delivered_at:
                                    log.delivered_at = timezone.now()
                            if mapped_status == 'FAILED':
                                # Meta puts the real delivery failure here
                                # (blocked, invalid, undeliverable, etc.).
                                # Without this the Staff Messages feed stays
                                # on SENT forever even when the phone never
                                # got the bubble.
                                errors = status_obj.get('errors') or []
                                if errors:
                                    err0 = errors[0] if isinstance(errors, list) else errors
                                    msg = (
                                        (err0.get('title') or err0.get('message') or '')
                                        if isinstance(err0, dict)
                                        else str(err0)
                                    )
                                    code = (
                                        err0.get('code')
                                        if isinstance(err0, dict)
                                        else None
                                    )
                                    detail = f"{code}: {msg}".strip(": ") if code else msg
                                    if detail:
                                        log.error_message = detail[:500]
                                        update_fields.append('error_message')
                            log.save(update_fields=update_fields)

                            # Also update the parent notification if needed
                            notif = log.notification
                            if notif:
                                if mapped_status == 'READ' and not notif.read_at:
                                    notif.read_at = timezone.now()
                                    notif.is_read = True
                                    notif.save(update_fields=['read_at', 'is_read'])

                            # Bust the dashboard "Staff Messages" feed so
                            # ✓✓ / 🅱 read-receipt transitions appear
                            # immediately on the manager's next poll.
                            # Best-effort — never raise from the webhook.
                            try:
                                rid = (
                                    getattr(getattr(notif, 'recipient', None), 'restaurant_id', None)
                                    if notif else None
                                )
                                if rid:
                                    from dashboard.api.staff_messages import (
                                        _invalidate_recent_cache,
                                    )

                                    _invalidate_recent_cache(rid)
                            except Exception:
                                pass

                messages = value.get('messages', [])

                for msg in messages:
                    wamid = msg.get('id')
                    if wamid and WhatsAppMessageProcessed.objects.filter(wamid=wamid).exists():
                        continue  # Idempotency: already processed successfully
                    from_phone = msg.get('from')
                    try:
                        msg_type = msg.get('type')
                        text_body = (msg.get('text') or {}).get('body') if msg_type == 'text' else None
                        
                        # Normalize phone (Meta sends digits; Morocco national → 212… for DB/session consistency)
                        phone_digits = ''.join(filter(str.isdigit, str(from_phone or '')))
                        phone_digits = normalize_activation_phone_inbound(phone_digits) or phone_digits
                        logger.info(
                            "WhatsApp inbound wamid=%s type=%s phone=%s preview=%s",
                            wamid,
                            msg_type,
                            phone_digits,
                            (text_body or "")[:80],
                        )
                        # ONE-TAP activation: on first inbound message, match NOT_ACTIVATED staff by phone and activate
                        activated_user = None
                        try:
                            activated_user = try_activate_staff_on_inbound_message(phone_digits)
                        except Exception:
                            logger.exception(
                                "ONE-TAP activation raised unexpectedly phone=%s",
                                phone_digits,
                            )
                            _safe_whatsapp_text_send(
                                phone_digits,
                                "I couldn't finish activating your account just now. "
                                "Please send the same activation message again in a moment.",
                                log_ctx="activation_exception",
                            )
                            continue
                        if activated_user:
                            session, _ = WhatsAppSession.objects.update_or_create(
                                phone=phone_digits,
                                defaults={'user': activated_user, 'state': 'idle'}
                            )
                            # Welcome is sent inside try_activate. Pure activation
                            # phrases stop here; otherwise continue Miya with the
                            # linked/created user (including already-existing accounts).
                            _act_text = (text_body or "").strip().lower()
                            _is_activation_phrase = (
                                "ready to activate" in _act_text
                                or "activate my account" in _act_text
                            )
                            if _is_activation_phrase:
                                continue
                            user = activated_user
                        else:
                            # Resolve user: prefer session's user; else match by phone
                            session = WhatsAppSession.objects.filter(phone=phone_digits).first()
                            user = session.user if (session and session.user_id) else None
                        if not user:
                            from accounts.services import _find_active_user_by_phone
                            user = _find_active_user_by_phone(phone_digits)
                        if not user:
                            qs = CustomUser.objects.filter(phone__isnull=False).filter(phone__regex=r'\d')
                            if session and session.user_id and getattr(session.user, 'restaurant_id', None):
                                qs = qs.filter(restaurant_id=session.user.restaurant_id)
                            user = qs.filter(phone__icontains=phone_digits[-9:]).first()
                        if not session:
                            session = WhatsAppSession.objects.create(phone=phone_digits, user=user)
                        elif user and not session.user_id:
                            session.user = user
                            session.save(update_fields=['user'])
                        if user and session.user is None:
                            session.user = user
                            session.save(update_fields=['user'])
    
                        # Tenant WhatsApp automations (before Miya)
                        _automation_stop_miya = False
                        _auto_restaurant = None
                        if user:
                            _auto_restaurant = getattr(user, 'restaurant', None)
                            if not _auto_restaurant:
                                try:
                                    from miya.services.tenant import resolve_active_tenant
                                    _auto_restaurant = resolve_active_tenant(user)
                                except Exception:
                                    _auto_restaurant = None
                        if _auto_restaurant and phone_digits and msg_type == 'text' and text_body:
                            try:
                                from automations.services.engine import run_automations_for_whatsapp_message
                                _is_first = bool(
                                    session
                                    and (getattr(session, 'context', None) or {}).get('message_count', 0) == 0
                                )
                                if session:
                                    _ctx = dict(getattr(session, 'context', None) or {})
                                    _ctx['message_count'] = int(_ctx.get('message_count') or 0) + 1
                                    session.context = _ctx
                                    session.save(update_fields=['context'])
                                _auto_result = run_automations_for_whatsapp_message(
                                    restaurant=_auto_restaurant,
                                    phone_digits=phone_digits,
                                    user=user,
                                    session=session,
                                    message_text=text_body,
                                    is_first_message=_is_first,
                                )
                                _automation_stop_miya = bool(_auto_result.get('stop_miya'))
                            except Exception:
                                logger.exception('WhatsApp automation run failed')
                        
                        from accounts.utils import calculate_distance
    
                        # When Mastra/Miya handles WhatsApp, Django only processes messages
                        # for flows that Miya cannot handle (location sharing, photo uploads).
                        # Checklists are now fully Miya-driven (she sends tasks & records responses).
                        _active_django_states = {
                            'awaiting_clock_in_location',
                            'awaiting_task_photo', 'awaiting_feedback',
                            'awaiting_incident_text', 'awaiting_incident_clarification',
                            'awaiting_incident_photo',
                            'awaiting_order_voice',
                            'awaiting_order_clarification',
                        }
                        # Image and location messages are always handled by Django (Mastra
                        # cannot download WhatsApp media). Django processes incident photos,
                        # verification photos, and clock-in locations directly.
                        _django_only_msg_types = {'image', 'location', 'audio', 'voice'}
                        _text_is_voice_placeholder = (
                            msg_type == 'text'
                            and text_body
                            and _looks_like_voice_ui_placeholder(text_body)
                        )
                        _text_is_staff_escalation = False
                        _interactive_is_staff_escalation = False
                        _interactive_is_clock = False
                        _text_is_incident_report = False
                        _text_is_my_shifts = False
                        _text_is_dashboard_task_reply = False
                        _text_is_checklist_start = False
                        _text_is_clock_float_recovery = False
                        _text_is_clock_in = (
                            msg_type == "text"
                            and text_body
                            and _normalize_clock_in_intent(text_body)
                        )
                        _awaiting_clock_in_gps = bool(
                            session and getattr(session, "state", None) == "awaiting_clock_in_location"
                        )
                        _awaiting_incident = bool(
                            session
                            and (
                                getattr(session, "state", None)
                                in (
                                    "awaiting_incident_text",
                                    "awaiting_incident_clarification",
                                    "awaiting_incident_photo",
                                )
                                or (getattr(session, "context", None) or {}).get("incident_photo_media_id")
                            )
                        )
                        try:
                            from staff.whatsapp_escalation import (
                                is_cancel_send_reply,
                                is_confirm_send_reply,
                                is_explicit_confirm_send_reply,
                                looks_like_cash_clock_in_followup,
                                looks_like_staff_manager_escalation,
                                session_has_staff_escalation_context,
                            )
                            from staff.whatsapp_my_shifts import looks_like_my_shifts_query
                            from notifications.dashboard_task_whatsapp import (
                                looks_like_dashboard_task_status_reply,
                            )
    
                            if msg_type == 'text' and text_body:
                                _text_is_staff_escalation = looks_like_staff_manager_escalation(text_body)
                                if not _text_is_staff_escalation:
                                    _text_is_staff_escalation = is_explicit_confirm_send_reply(
                                        text_body
                                    ) or (
                                        (is_cancel_send_reply(text_body) or is_confirm_send_reply(text_body))
                                        and session_has_staff_escalation_context(session)
                                    )
                                _text_is_incident_report = looks_like_whatsapp_incident_report(text_body)
                                if not _text_is_incident_report and session:
                                    ctx = getattr(session, "context", None) or {}
                                    if ctx.get("incident_photo_media_id"):
                                        _text_is_incident_report = True
                                _text_is_my_shifts = looks_like_my_shifts_query(text_body)
                                _text_is_dashboard_task_reply = looks_like_dashboard_task_status_reply(
                                    text_body
                                )
                                _text_is_clock_float_recovery = looks_like_cash_clock_in_followup(text_body)
                                _text_is_checklist_start = _normalize_start_checklist_intent(text_body)
                            if msg_type == 'interactive':
                                inter = msg.get('interactive') or {}
                                if inter.get('type') == 'button_reply':
                                    btn = inter.get('button_reply') or {}
                                    btn_id = (btn.get('id') or '').strip()
                                    btn_title = (btn.get('title') or '').strip()
                                    if btn_id in ('clock_in_now', 'clock_out_now') or _normalize_clock_in_intent(
                                        btn_title
                                    ):
                                        _interactive_is_clock = True
                                    _interactive_is_staff_escalation = (
                                        looks_like_staff_manager_escalation(btn_title)
                                        or is_explicit_confirm_send_reply(btn_title)
                                        or (
                                            (is_cancel_send_reply(btn_title) or is_confirm_send_reply(btn_title))
                                            and session_has_staff_escalation_context(session)
                                        )
                                    )
                                elif inter.get('type') == 'location_reply':
                                    _interactive_is_clock = True
                        except Exception:
                            pass

                        if (
                            miya_wa
                            and session
                            and session.state not in _active_django_states
                            and msg_type not in _django_only_msg_types
                            and not _gps_clock_in_applies_to_whatsapp_message(msg, session)
                            and not _text_is_voice_placeholder
                            and not _text_is_staff_escalation
                            and not _interactive_is_staff_escalation
                            and not _interactive_is_clock
                            and not _text_is_clock_in
                            and not _awaiting_clock_in_gps
                            and not _awaiting_incident
                            and not _text_is_incident_report
                            and not _text_is_my_shifts
                            and not _text_is_dashboard_task_reply
                            and not _text_is_checklist_start
                            and not _text_is_clock_float_recovery
                            and not _automation_stop_miya
                        ):
                            if miya_wa and text_body:
                                from miya.services.whatsapp import enqueue_miya_whatsapp_turn

                                # Unknown numbers still get an invite/help reply from Miya.
                                if enqueue_miya_whatsapp_turn(
                                    user=user,
                                    phone_digits=phone_digits,
                                    message_text=text_body,
                                    session=session,
                                ):
                                    continue
                        
                        # ------------------------------------------------------------------
                        # 1. HANDLE INTERACTIVE (Buttons)
                        # ------------------------------------------------------------------
                        if msg_type == 'interactive':
                            interactive = msg.get('interactive', {})
                            int_type = interactive.get('type')
                            
                            if int_type == 'button_reply':
                                btn_reply = interactive.get('button_reply', {})
                                btn_id = btn_reply.get('id')
                                btn_title = (btn_reply.get('title') or '').strip()
    
                                if _process_whatsapp_staff_escalation(
                                    notification_service,
                                    user,
                                    phone_digits,
                                    session,
                                    btn_title or btn_id or '',
                                    wamid=wamid or '',
                                    msg=msg,
                                ):
                                    continue
    
                                if btn_id == 'clock_in_now':
                                    if not user:
                                        notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                        continue
                                    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                                    if last_event and last_event.event_type == 'in':
                                        first_name = getattr(user, "first_name", None) or "Team Member"
                                        local_time = timezone.localtime(last_event.timestamp).strftime("%H:%M")
                                        notification_service.send_whatsapp_text(
                                            phone_digits,
                                            R(user, "already_clocked_in", time=local_time, name=first_name),
                                        )
                                        continue
                                    rest = getattr(user, 'restaurant', None)
                                    if not restaurant_has_clockin_geofence(rest):
                                        notification_service.send_whatsapp_text(
                                            phone_digits,
                                            R(user, "no_geofence_configured"),
                                        )
                                        continue
                                    session.state = 'awaiting_clock_in_location'
                                    session.save(update_fields=['state'])
                                    notification_service.send_whatsapp_location_request(
                                        phone_digits,
                                        "Share your location to clock in.",
                                    )
                                    continue
    
    
                                elif btn_id == 'clock_out_now':
                                    if user:
                                        last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                                        if last_event and last_event.event_type == 'in':
                                            duration = (timezone.now() - last_event.timestamp).total_seconds() / 3600
                                            restaurant = user.restaurant
                                            notes = "WhatsApp clock-out without location - unverified"
                                            lat, lon, within_geofence = None, None, False
                                            if restaurant and restaurant.latitude and restaurant.longitude and restaurant.radius:
                                                loc_msg = msg.get('location') or (msg.get('interactive', {}).get('location') if msg.get('type') == 'interactive' else None)
                                                if loc_msg:
                                                    lat = loc_msg.get('latitude')
                                                    lon = loc_msg.get('longitude')
                                                if lat is not None and lon is not None:
                                                    from accounts.utils import calculate_distance
                                                    dist = calculate_distance(
                                                        float(restaurant.latitude), float(restaurant.longitude),
                                                        float(lat), float(lon)
                                                    )
                                                    radius = float(restaurant.radius or 100)
                                                    within_geofence = dist <= radius
                                                    notes = f"WhatsApp clock-out | distance={dist:.0f}m, geofence={'OK' if within_geofence else 'OUTSIDE'}"
                                            ClockEvent.objects.create(
                                                staff=user,
                                                event_type='out',
                                                device_id='whatsapp',
                                                latitude=lat,
                                                longitude=lon,
                                                notes=notes,
                                                location_encrypted='PRECISE_GPS' if within_geofence else 'UNVERIFIED',
                                            )
                                            summary_msg = (
                                                f"✅ *Clock-out successful!*\n\n"
                                                f"⏱️ Duration: *{duration:.2f} hours*"
                                            )
                                            notification_service.send_whatsapp_text(phone_digits, summary_msg)
                                            session.state = 'idle'
                                            session.save(update_fields=['state'])
                                        else:
                                            notification_service.send_whatsapp_text(phone_digits, R(user, 'clockout_no'))
                                    else:
                                        notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                    continue
    
                                # =====================================================
                                # HANDLE CHECKLIST BUTTON RESPONSES (Yes/No/N/A)
                                # =====================================================
                                elif btn_id in ['yes', 'no', 'n_a', 'Yes', 'No', 'N/A'] and session.state == 'in_checklist':
                                    response_value = btn_id.lower().replace('/', '_')
                                    if _handle_checklist_response(notification_service, session, user, phone_digits, response_value):
                                        continue
    
                                elif session.state == 'checklist_followup' and btn_id in ['need_help', 'delay', 'skip']:
                                    from scheduling.models import ShiftTask
                                    checklist = session.context.get('checklist', {})
                                    pending_task_id = checklist.get('pending_task_id')
                                    task = ShiftTask.objects.filter(id=pending_task_id).first() if pending_task_id else None
                                    if not task:
                                        session.state = 'in_checklist'
                                        session.save(update_fields=['state'])
                                        continue
                                    if btn_id == 'need_help':
                                        session.state = 'checklist_help_text'
                                        session.save(update_fields=['state'])
                                        notification_service.send_whatsapp_text(phone_digits, f"Tell me what you need help with for:\n\n*{task.title}*")
                                        continue
                                    if btn_id == 'delay':
                                        session.state = 'checklist_delay_eta'
                                        session.save(update_fields=['state'])
                                        eta_msg = "When do you expect to complete it?"
                                        eta_buttons = [
                                            {"id": "eta_10m", "title": "10 min"},
                                            {"id": "eta_30m", "title": "30 min"},
                                            {"id": "eta_1h", "title": "1 hour"},
                                            {"id": "eta_later", "title": "Later"}
                                        ]
                                        notification_service.send_whatsapp_buttons(phone_digits, eta_msg, eta_buttons)
                                        continue
                                    if btn_id == 'skip':
                                        task.status = 'CANCELLED'
                                        task.notes = (task.notes or '') + f"\nSkipped by staff ({timezone.now().strftime('%H:%M')})"
                                        task.save(update_fields=['status', 'notes'])
                                        checklist.pop('pending_task_id', None)
                                        session.context['checklist'] = checklist
                                        session.state = 'in_checklist'
                                        session.save(update_fields=['state', 'context'])
                                        # Send next task
                                        task_ids = checklist.get('tasks', [])
                                        pending = list(ShiftTask.objects.filter(id__in=task_ids).exclude(status__in=['COMPLETED', 'CANCELLED']))
                                        if not pending:
                                            _sync_checklist_progress_complete(checklist.get('shift_id'), user)
                                            session.context.pop('checklist', None)
                                            session.state = 'idle'
                                            session.save(update_fields=['state', 'context'])
                                            notification_service.send_whatsapp_text(phone_digits, "Great job! Your opening checklist is complete. Have a productive shift!")
                                        else:
                                            next_id = None
                                            for tid in task_ids:
                                                if str(tid) in {str(t.id) for t in pending}:
                                                    next_id = str(tid)
                                                    break
                                            next_id = next_id or str(pending[0].id)
                                            checklist['current_task_id'] = next_id
                                            session.context['checklist'] = checklist
                                            _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)
                                            session.save(update_fields=['context'])
                                            nxt = ShiftTask.objects.filter(id=next_id).first()
                                            if nxt:
                                                idx = (task_ids.index(next_id) + 1) if next_id in task_ids else 1
                                                notification_service._send_task_step_to_whatsapp(phone_digits, nxt, idx, len(task_ids), session)
                                        continue
    
                                elif session.state == 'checklist_delay_eta' and btn_id in ['eta_10m', 'eta_30m', 'eta_1h', 'eta_later']:
                                    from scheduling.models import ShiftTask
                                    checklist = session.context.get('checklist', {})
                                    pending_task_id = checklist.get('pending_task_id')
                                    task = ShiftTask.objects.filter(id=pending_task_id).first() if pending_task_id else None
                                    if task:
                                        mapping = {'eta_10m': '10 minutes', 'eta_30m': '30 minutes', 'eta_1h': '1 hour', 'eta_later': 'later'}
                                        eta_txt = mapping.get(btn_id, 'later')
                                        task.notes = (task.notes or '') + f"\nDelayed (ETA: {eta_txt}) at {timezone.now().strftime('%H:%M')}"
                                        task.save(update_fields=['notes'])
                                    checklist.pop('pending_task_id', None)
                                    session.context['checklist'] = checklist
                                    session.state = 'in_checklist'
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(phone_digits, "Thanks — marked as delayed. Continuing.")
                                    continue

                            elif int_type == 'location_reply':
                                if not user:
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                    continue
                                loc, lat_raw, lon_raw = _extract_whatsapp_inbound_location(msg)
                                lat_c, lon_c = _coerce_whatsapp_location_lat_lon(lat_raw, lon_raw)
                                if lat_c is None or lon_c is None:
                                    notification_service.send_whatsapp_location_request(
                                        phone_digits,
                                        R(user, "share_location_prompt"),
                                    )
                                    continue
                                try:
                                    _process_whatsapp_clock_in_from_gps(
                                        user, phone_digits, session, lat_c, lon_c, loc or {}, R
                                    )
                                except Exception:
                                    logger.exception("WhatsApp location_reply clock-in failed phone=%s", phone_digits)
                                    _safe_whatsapp_text_send(
                                        phone_digits,
                                        R(user, "generic_error"),
                                        log_ctx="whatsapp_location_reply",
                                    )
                                continue

                        # ------------------------------------------------------------------
                        # 2. HANDLE IMAGE (Verification)
                        # ------------------------------------------------------------------
                        if msg_type in ('image', 'document'):
                            if (
                                miya_wa
                                and user
                                and session
                                and session.state == 'idle'
                            ):
                                from miya.services.whatsapp_attachments import try_miya_whatsapp_attachment

                                if try_miya_whatsapp_attachment(
                                    user=user,
                                    phone_digits=phone_digits,
                                    msg=msg,
                                    session=session,
                                ):
                                    continue

                        if msg_type == 'image':
                            # Dashboard Task photo proof (Miya-assigned ops tasks)
                            pending_dash_proof = (session.context or {}).get(
                                "awaiting_dashboard_task_proof_id"
                            )
                            if pending_dash_proof and user:
                                try:
                                    from dashboard.models import Task as DashTask
                                    from notifications.media_persist import (
                                        FOLDER_TASK_PROOFS,
                                        MEDIA_CATEGORY_TASK_PROOFS,
                                        persist_whatsapp_media,
                                    )
    
                                    dash_task = DashTask.objects.filter(
                                        id=pending_dash_proof,
                                        restaurant=getattr(user, "restaurant", None),
                                    ).first()
                                    if dash_task and (
                                        not dash_task.assigned_to_id
                                        or dash_task.assigned_to_id == user.id
                                    ):
                                        image_obj = msg.get("image") or {}
                                        media_id = image_obj.get("id")
                                        caption = (image_obj.get("caption") or "").strip()
                                        durable_url = None
                                        if media_id:
                                            durable_url, _, _ = persist_whatsapp_media(
                                                media_id,
                                                folder=FOLDER_TASK_PROOFS,
                                                filename_hint=f"task_proof_{dash_task.id}.jpg",
                                                restaurant_id=getattr(dash_task, "restaurant_id", None)
                                                or getattr(user, "restaurant_id", None),
                                                media_category=MEDIA_CATEGORY_TASK_PROOFS,
                                            )
                                        if durable_url:
                                            dash_task.proof_media_url = durable_url[:1000]
                                            dash_task.proof_caption = caption[:2000]
                                            dash_task.proof_submitted_at = timezone.now()
                                            dash_task.proof_submitted_by = user
                                            update_fields = [
                                                "proof_media_url",
                                                "proof_caption",
                                                "proof_submitted_at",
                                                "proof_submitted_by",
                                                "updated_at",
                                            ]
                                            should_complete = bool(
                                                (session.context or {}).get(
                                                    "awaiting_dashboard_task_proof_complete"
                                                )
                                            )
                                            if should_complete:
                                                dash_task.status = "COMPLETED"
                                                dash_task.completed_at = timezone.now()
                                                dash_task.completed_by = user
                                                update_fields.extend(
                                                    ["status", "completed_at", "completed_by"]
                                                )
                                            elif dash_task.status == "PENDING":
                                                dash_task.status = "IN_PROGRESS"
                                                update_fields.append("status")
                                            dash_task.save(update_fields=update_fields)
                                            session.context.pop(
                                                "awaiting_dashboard_task_proof_id", None
                                            )
                                            session.context.pop(
                                                "awaiting_dashboard_task_proof_complete", None
                                            )
                                            session.state = "idle"
                                            session.save(update_fields=["state", "context"])
                                            if should_complete:
                                                notification_service.send_whatsapp_text(
                                                    phone_digits,
                                                    f"Photo saved — marked *{dash_task.title}* as completed. Thanks!",
                                                )
                                                try:
                                                    from notifications.dashboard_task_whatsapp import (
                                                        _notify_managers_completed,
                                                    )
    
                                                    _notify_managers_completed(dash_task, user)
                                                except Exception:
                                                    logger.exception(
                                                        "dashboard proof complete notify failed"
                                                    )
                                            else:
                                                notification_service.send_whatsapp_text(
                                                    phone_digits,
                                                    f"Photo proof saved for *{dash_task.title}*. Reply *done* when finished.",
                                                )
                                            continue
                                except Exception:
                                    logger.exception(
                                        "dashboard task proof image handler failed"
                                    )
    
                            if session.context.get('awaiting_verification_for_task_id'):
                                task_id = session.context.get('awaiting_verification_for_task_id')
                                try:
                                    from scheduling.models import ShiftTask, TaskVerificationRecord
                                    task = (
                                        ShiftTask.objects.filter(id=task_id, assigned_to=user).first()
                                        or ShiftTask.objects.filter(id=task_id).first()
                                    )
                                    if not task:
                                        raise ShiftTask.DoesNotExist()
                                    # Lock: reject photo if checklist was closed (e.g. auto clock-out)
                                    if task.shift_id and user:
                                        prog = ShiftChecklistProgress.objects.filter(shift_id=task.shift_id, staff=user).first()
                                        if prog and prog.status in ('INCOMPLETE_SHIFT_END', 'CANCELLED'):
                                            notification_service.send_whatsapp_text(
                                                phone_digits,
                                                "This checklist was closed because your shift ended. Contact your manager if you need to update it."
                                            )
                                            session.context.pop('awaiting_verification_for_task_id', None)
                                            session.state = 'idle'
                                            session.save(update_fields=['state', 'context'])
                                            continue
                                    image_obj = msg.get('image') or {}
                                    media_id = image_obj.get('id')
                                    mime_type = image_obj.get('mime_type')
                                    caption = image_obj.get('caption')
    
                                    durable_url = None
                                    if media_id:
                                        try:
                                            from notifications.media_persist import (
                                                FOLDER_CHECKLIST_EVIDENCE,
                                                MEDIA_CATEGORY_CHECKLIST_EVIDENCE,
                                                persist_whatsapp_media,
                                            )
                                            restaurant_id = getattr(user, "restaurant_id", None)
                                            if not restaurant_id and getattr(task, "shift_id", None):
                                                restaurant_id = getattr(
                                                    getattr(task, "shift", None),
                                                    "restaurant_id",
                                                    None,
                                                )
                                            durable_url, persisted_mime, _ = persist_whatsapp_media(
                                                media_id,
                                                folder=FOLDER_CHECKLIST_EVIDENCE,
                                                filename_hint=f"checklist_{task.id}.jpg",
                                                restaurant_id=restaurant_id,
                                                media_category=MEDIA_CATEGORY_CHECKLIST_EVIDENCE,
                                            )
                                            mime_type = mime_type or persisted_mime
                                        except Exception:
                                            logger.warning(
                                                "Failed to persist checklist photo evidence for task=%s media_id=%s",
                                                task.id,
                                                media_id,
                                                exc_info=True,
                                            )
    
                                    record, created = TaskVerificationRecord.objects.get_or_create(
                                        task=task,
                                        submitted_by=user,
                                        defaults={'photo_evidence': []}
                                    )
                                    photos = list(record.photo_evidence or [])
                                    submitted_at = timezone.now().isoformat()
                                    photos.append({
                                        'url': durable_url,
                                        'media_id': media_id,
                                        'mime_type': mime_type,
                                        'caption': caption,
                                        'submitted_at': submitted_at,
                                        'timestamp': submitted_at,  # backwards compatible
                                        'staff_id': str(user.id),
                                        'user_id': str(user.id),
                                        'shift_id': str(task.shift_id),
                                        'task_id': str(task.id),
                                    })
                                    record.photo_evidence = photos
                                    cr = dict(record.checklist_responses or {})
                                    cr.update({
                                        "response": "yes",
                                        "photo_received": True,
                                        "checklist_item_id": str(task.id),
                                        "shift_id": str(task.shift_id),
                                        "awaiting_photo": False,
                                    })
                                    record.checklist_responses = cr
                                    record.save(update_fields=["photo_evidence", "checklist_responses"])
                                    
                                    task.status = 'COMPLETED'
                                    task.completed_at = timezone.now()
                                    task.save(update_fields=['status', 'completed_at'])
    
                                    # Sync ShiftChecklistProgress (Miya + legacy Live Board)
                                    try:
                                        from scheduling.checklist_completion import (
                                            finalize_shift_checklist_completion,
                                        )

                                        prog = ShiftChecklistProgress.objects.filter(
                                            shift_id=task.shift_id, staff=user
                                        ).first()
                                        if prog:
                                            responses = dict(prog.responses or {})
                                            responses[str(task.id)] = "yes"
                                            prog.responses = responses
                                            task_ids = list(prog.task_ids or [])
                                            if not task_ids:
                                                task_ids = list(
                                                    (session.context.get("checklist") or {}).get("tasks") or []
                                                )
                                            next_id = None
                                            for tid in task_ids:
                                                if str(tid) == str(task.id):
                                                    continue
                                                if str(tid) in responses:
                                                    continue
                                                cand = ShiftTask.objects.filter(id=tid).first()
                                                if cand and cand.status not in ("COMPLETED", "CANCELLED"):
                                                    next_id = str(tid)
                                                    break
                                            if next_id:
                                                prog.current_task_id = next_id
                                                prog.status = "IN_PROGRESS"
                                                prog.save(
                                                    update_fields=[
                                                        "responses",
                                                        "current_task_id",
                                                        "status",
                                                        "updated_at",
                                                    ]
                                                )
                                            else:
                                                finalize_shift_checklist_completion(prog, user)
                                    except Exception:
                                        logger.exception(
                                            "checklist photo: progress sync failed task=%s", task.id
                                        )
    
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        "Got the photo — thanks!",
                                    )
                                except Exception:
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'unrecognized'))
                                
                                session.context.pop('awaiting_verification_for_task_id', None)
                                # If we're in a shift checklist, resume it automatically
                                checklist = session.context.get('checklist')
                                if checklist:
                                    session.state = 'in_checklist'
                                    session.save(update_fields=['context', 'state'])
                                    try:
                                        from scheduling.models import ShiftTask
                                        from scheduling.checklist_photo import task_requires_photo
    
                                        # Prefer progress task_ids when session checklist is sparse (Miya path)
                                        prog = None
                                        if user and task.shift_id:
                                            prog = ShiftChecklistProgress.objects.filter(
                                                shift_id=task.shift_id, staff=user
                                            ).first()
                                        task_ids = list(
                                            (prog.task_ids if prog else None)
                                            or checklist.get('tasks', [])
                                            or []
                                        )
                                        responses = dict(
                                            (prog.responses if prog else None)
                                            or checklist.get('responses', {})
                                            or {}
                                        )
                                        responses[str(task.id)] = "yes"
                                        pending = [
                                            t
                                            for t in ShiftTask.objects.filter(id__in=task_ids).exclude(
                                                status__in=['COMPLETED', 'CANCELLED']
                                            )
                                            if str(t.id) not in responses or str(t.id) == str(task.id)
                                        ]
                                        # Exclude the just-completed task
                                        pending = [t for t in pending if str(t.id) != str(task.id)]
                                        if pending:
                                            next_id = None
                                            pending_ids = {str(t.id) for t in pending}
                                            for tid in task_ids:
                                                if str(tid) in pending_ids:
                                                    next_id = str(tid)
                                                    break
                                            next_id = next_id or str(pending[0].id)
                                            checklist['tasks'] = [str(x) for x in task_ids]
                                            checklist['current_task_id'] = next_id
                                            checklist['responses'] = responses
                                            if task.shift_id:
                                                checklist['shift_id'] = str(task.shift_id)
                                            session.context['checklist'] = checklist
                                            session.save(update_fields=['context'])
                                            nxt = ShiftTask.objects.filter(id=next_id).first()
                                            if nxt:
                                                idx = (task_ids.index(next_id) + 1) if next_id in task_ids else 1
                                                # One natural prompt (avoid double-send with buttons helper)
                                                photo_hint = (
                                                    "\n\nWhen done, reply *Yes* — I'll ask for a photo as proof."
                                                    if task_requires_photo(nxt)
                                                    else "\n\nReply *Yes*, *No*, or *N/A*."
                                                )
                                                body = (
                                                    f"Next up — *Task {idx}/{len(task_ids)}:* {nxt.title}"
                                                    + (f"\n{nxt.description}" if nxt.description else "")
                                                    + photo_hint
                                                )
                                                notification_service.send_whatsapp_text(phone_digits, body)
                                        else:
                                            if user and task.shift_id:
                                                prog = ShiftChecklistProgress.objects.filter(
                                                    shift_id=task.shift_id, staff=user
                                                ).first()
                                                if prog:
                                                    responses = dict(prog.responses or {})
                                                    responses[str(task.id)] = "yes"
                                                    prog.responses = responses
                                                    prog.save(update_fields=["responses", "updated_at"])
                                                    from scheduling.checklist_completion import (
                                                        finalize_shift_checklist_completion,
                                                    )
                                                    finalize_shift_checklist_completion(prog, user)
                                            notification_service.send_whatsapp_text(
                                                phone_digits,
                                                "Nice work — checklist complete. Have a great shift!",
                                            )
                                            session.context.pop('checklist', None)
                                            session.state = 'idle'
                                            session.save(update_fields=['context', 'state'])
                                    except Exception:
                                        logger.exception("checklist photo: resume next task failed")
                                else:
                                    session.state = 'idle'
                                    session.save(update_fields=['context', 'state'])
                            elif user and session.state == 'awaiting_incident_photo':
                                from staff.models_task import SafetyConcernReport
    
                                image_obj = msg.get('image') or {}
                                document_obj = msg.get('document') or {}
                                media_id_img = image_obj.get('id') or document_obj.get('id')
                                mime_type_img = image_obj.get('mime_type') or document_obj.get('mime_type')
                                filename_img = document_obj.get('filename')
                                ticket_id = (session.context or {}).get('incident_ticket_id')
                                ticket = None
                                if ticket_id:
                                    ticket = SafetyConcernReport.objects.filter(
                                        id=ticket_id, reporter=user
                                    ).first()
                                if ticket and media_id_img:
                                    caption_img = (image_obj.get('caption') or '').strip()
                                    _attach_whatsapp_media_to_incident(
                                        notification_service,
                                        ticket,
                                        media_id_img,
                                        mime_type_img,
                                        filename_img,
                                        caption=caption_img,
                                        user=user,
                                    )
                                    session.state = 'idle'
                                    session.context.pop('incident_ticket_id', None)
                                    session.context.pop('incident_photo_media_id', None)
                                    session.context.pop('incident_photo_mime_type', None)
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits, R(user, 'incident_photo_attached')
                                    )
                                else:
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, 'incident_ask_photo'),
                                    )
                                continue
                            elif user and (session.state == 'awaiting_incident_text' or session.state == 'awaiting_incident_clarification'):
                                # Incident report with photo (text + photo or photo with caption)
                                image_obj = msg.get('image') or {}
                                media_id_img = image_obj.get('id')
                                mime_type_img = image_obj.get('mime_type')
                                caption = (image_obj.get('caption') or '').strip()
                                if media_id_img:
                                    session.context['incident_photo_media_id'] = media_id_img
                                    session.context['incident_photo_mime_type'] = mime_type_img
                                if session.state == 'awaiting_incident_text':
                                    if caption:
                                        raw_body = caption
                                        from scheduling.models import AssignedShift
                                        def _infer_shift_img(u, when_dt):
                                            try:
                                                qs = AssignedShift.objects.filter(
                                                    staff=u, shift_date=when_dt.date(),
                                                    status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                                )
                                                overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                                return overlap or qs.order_by('start_time').first()
                                            except Exception:
                                                return None
                                        now = timezone.now()
                                        incident_type = infer_incident_type(raw_body)
                                        occurred_at = extract_occurred_at(raw_body, now)
                                        missing = []
                                        if not incident_type:
                                            missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                                        if not occurred_at:
                                            missing.append("time of occurrence (e.g., today 3pm)")
                                        if missing and not incident_type:
                                            session.state = 'awaiting_incident_clarification'
                                            session.context['pending_incident'] = {'source': 'image_caption', 'transcript': raw_body}
                                            session.save(update_fields=['state', 'context'])
                                            notification_service.send_whatsapp_text(
                                                phone_digits,
                                                R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                            )
                                            continue
                                        occurred_at = occurred_at or now
                                        shift_obj = _infer_shift_img(user, occurred_at) if occurred_at else None
                                        severity = infer_severity(raw_body)
                                        try:
                                            ticket = _create_safety_concern_from_whatsapp(
                                                user=user,
                                                description=raw_body,
                                                incident_type=incident_type or 'General',
                                                severity=severity,
                                                occurred_at=occurred_at,
                                                shift=shift_obj,
                                            )
                                            _finish_whatsapp_incident_turn(
                                                notification_service,
                                                ticket,
                                                session,
                                                raw_body,
                                                user,
                                                phone_digits,
                                                incident_type=ticket.incident_type,
                                                occurred_at=occurred_at,
                                                R=R,
                                            )
                                        except Exception as e:
                                            logger.exception("Failed to create incident from image caption: %s", e)
                                            notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_failed'))
                                    else:
                                        session.save(update_fields=['context'])
                                        notification_service.send_whatsapp_text(
                                            phone_digits,
                                            "📷 Got the photo. Please describe what happened (e.g. broken chair at the bar, when it occurred)."
                                        )
                                    continue
                                else:
                                    pending = session.context.get('pending_incident') or {}
                                    base_text = (pending.get('transcript') or '').strip()
                                    combined_text = (base_text + "\n\n" + caption).strip() if caption else base_text
                                    if not combined_text:
                                        session.save(update_fields=['context'])
                                        notification_service.send_whatsapp_text(
                                            phone_digits,
                                            "📷 Got the photo. Please also send a short description (what happened, when)."
                                        )
                                        continue
                                    from scheduling.models import AssignedShift
                                    def _infer_shift_cl(u, when_dt):
                                        try:
                                            qs = AssignedShift.objects.filter(
                                                staff=u, shift_date=when_dt.date(),
                                                status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                            )
                                            overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                            return overlap or qs.order_by('start_time').first()
                                        except Exception:
                                            return None
                                    now = timezone.now()
                                    incident_type = infer_incident_type(combined_text)
                                    occurred_at = extract_occurred_at(combined_text, now)
                                    if not incident_type:
                                        session.context['pending_incident'] = {**pending, 'transcript': combined_text}
                                        session.save(update_fields=['context'])
                                        notification_service.send_whatsapp_text(
                                            phone_digits,
                                            R(user, 'incident_clarify_missing', missing="incident type (Safety/Maintenance/HR/Service/Other)")
                                        )
                                        continue
                                    occurred_at = occurred_at or now
                                    shift_obj = _infer_shift_cl(user, occurred_at) if occurred_at else None
                                    severity = infer_severity(combined_text)
                                    try:
                                        ticket = _create_safety_concern_from_whatsapp(
                                            user=user,
                                            description=combined_text,
                                            incident_type=incident_type,
                                            severity=severity,
                                            occurred_at=occurred_at,
                                            shift=shift_obj,
                                            audio_evidence=[pending.get('audio_url')] if pending.get('audio_url') else [],
                                        )
                                        _finish_whatsapp_incident_turn(
                                            notification_service,
                                            ticket,
                                            session,
                                            combined_text,
                                            user,
                                            phone_digits,
                                            incident_type=ticket.incident_type,
                                            occurred_at=occurred_at,
                                            R=R,
                                        )
                                    except Exception as e:
                                        logger.exception("Failed to create incident from clarification+photo: %s", e)
                                        notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_failed'))
                                continue
                            elif user and not session.context.get('awaiting_verification_for_task_id'):
                                # Idle photo (not checklist evidence): treat as incident evidence.
                                # Clear stale clock-in so we never answer an incident photo with
                                # "try clocking in again".
                                image_obj_fb = msg.get('image') or {}
                                caption_fb = (image_obj_fb.get('caption') or '').strip()
                                media_id_fb = image_obj_fb.get('id')
                                mime_type_fb = image_obj_fb.get('mime_type')
                                if session.state == 'awaiting_clock_in_location':
                                    session.state = 'idle'
                                if caption_fb:
                                    from scheduling.models import AssignedShift
                                    now = timezone.now()
                                    incident_type_fb = infer_incident_type(caption_fb) or 'General'
    
                                    def _infer_shift_fb(u, when_dt):
                                        try:
                                            qs = AssignedShift.objects.filter(
                                                staff=u, shift_date=when_dt.date(),
                                                status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                            )
                                            overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                            return overlap or qs.order_by('start_time').first()
                                        except Exception:
                                            return None
    
                                    shift_obj_fb = _infer_shift_fb(user, now)
                                    try:
                                        if media_id_fb:
                                            session.context['incident_photo_media_id'] = media_id_fb
                                            session.context['incident_photo_mime_type'] = mime_type_fb
                                        ticket_fb = _create_safety_concern_from_whatsapp(
                                            user=user,
                                            description=caption_fb,
                                            incident_type=incident_type_fb,
                                            severity=infer_severity(caption_fb),
                                            occurred_at=now,
                                            shift=shift_obj_fb,
                                        )
                                        _finish_whatsapp_incident_turn(
                                            notification_service,
                                            ticket_fb,
                                            session,
                                            caption_fb,
                                            user,
                                            phone_digits,
                                            incident_type=ticket_fb.incident_type,
                                            occurred_at=now,
                                            R=R,
                                        )
                                        continue
                                    except Exception as e:
                                        logger.exception("Failed to create incident from image+caption fallback: %s", e)
                                        notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_failed'))
                                        continue
                                # Photo without caption — keep media and ask for a short description.
                                if media_id_fb:
                                    session.context['incident_photo_media_id'] = media_id_fb
                                    session.context['incident_photo_mime_type'] = mime_type_fb
                                session.state = 'awaiting_incident_text'
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    '📷 Got the photo. Please describe what happened '
                                    '(e.g. "Broken glass at table 44").',
                                )
                                continue
                            else:
                                # Unlinked phone — cannot create a ticket yet.
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
    
                        # ------------------------------------------------------------------
                        # 3. HANDLE AUDIO — guest order (Today’s Orders) OR incidents
                        # ------------------------------------------------------------------
                        if msg_type in ('audio', 'voice'):
                            audio = msg.get('audio') or msg.get('voice') or {}
                            media_id = audio.get('id')
                            media_url, mime_type = notification_service.fetch_whatsapp_media_url(media_id) if media_id else (None, None)
                            audio_bytes = notification_service.download_media_bytes(media_url) if media_url else None
                            transcript = notification_service.transcribe_audio_bytes(audio_bytes, input_mime_type=mime_type) if audio_bytes else None
    
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue

                            # Miya (Django path): transcribed voice → same agent pipeline; reply with voice note.
                            if (
                                miya_wa
                                and session
                                and session.state not in _active_django_states
                            ):
                                from accounts.rbac_enforce import user_can_use_miya
                                from miya.services.whatsapp import enqueue_miya_whatsapp_turn

                                tstrip_miya = (transcript or '').strip()
                                if user_can_use_miya(user) and tstrip_miya:
                                    if enqueue_miya_whatsapp_turn(
                                        user=user,
                                        phone_digits=phone_digits,
                                        message_text=tstrip_miya,
                                        session=session,
                                        voice_reply=True,
                                    ):
                                        continue
    
                            # --- Guest order (staff texted "order" / similar first, or clarification follow-up) ---
                            if session.state in ('awaiting_order_voice', 'awaiting_order_clarification'):
                                rest_o = getattr(user, 'restaurant', None)
                                if not rest_o:
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        "Your account has no restaurant context. Contact your manager.",
                                    )
                                    continue
    
                                tstrip = (transcript or '').strip()
                                if session.state == 'awaiting_order_clarification':
                                    pending_o = session.context.get('pending_order') or {}
                                    prior_o = (pending_o.get('transcript') or '').strip()
                                    merged = (prior_o + "\n\n" + tstrip).strip() if prior_o else tstrip
                                    text_to_store = merged if merged else tstrip
                                else:
                                    text_to_store = tstrip
    
                                if not text_to_store or len(text_to_store) < 8:
                                    session.state = 'awaiting_order_clarification'
                                    session.context['pending_order'] = {
                                        'source': 'voice',
                                        'audio_url': media_url,
                                        'media_id': media_id,
                                        'transcript': tstrip or '',
                                    }
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'order_clarify_audio'))
                                    continue
    
                                try:
                                    order = _create_staff_captured_order_parsed(rest_o, user, text_to_store, "VOICE")
                                    preview = text_to_store[:400] + ('…' if len(text_to_store) > 400 else '')
                                    session.state = 'idle'
                                    session.context.pop('pending_order', None)
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(
                                            user,
                                            'order_recorded',
                                            order_id=str(order.id)[:8],
                                            preview=f"Details:\n{preview}",
                                            followup=order_recorded_followup(user, order),
                                        ),
                                    )
                                except Exception as e:
                                    logger.exception("WhatsApp guest order (voice) failed: %s", e)
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'order_failed'))
                                continue
    
                            # Voice that sounds like a guest order / pickup (not an incident). Avoids
                            # misclassification: e.g. "customer" + time → Service incident via infer_incident_type.
                            if looks_like_guest_order_intent(transcript or ''):
                                rest_o = getattr(user, 'restaurant', None)
                                if not rest_o:
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        "Your account has no restaurant context. Contact your manager.",
                                    )
                                    continue
                                tstrip = (transcript or '').strip()
                                if not tstrip or len(tstrip) < 8:
                                    session.state = 'awaiting_order_clarification'
                                    session.context['pending_order'] = {
                                        'source': 'voice',
                                        'audio_url': media_url,
                                        'media_id': media_id,
                                        'transcript': tstrip or '',
                                    }
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'order_clarify_audio'))
                                    continue
                                try:
                                    order = _create_staff_captured_order_parsed(rest_o, user, tstrip, "VOICE")
                                    preview = tstrip[:400] + ('…' if len(tstrip) > 400 else '')
                                    session.state = 'idle'
                                    session.context.pop('pending_order', None)
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(
                                            user,
                                            'order_recorded',
                                            order_id=str(order.id)[:8],
                                            preview=f"Details:\n{preview}",
                                            followup=order_recorded_followup(user, order),
                                        ),
                                    )
                                except Exception as e:
                                    logger.exception("WhatsApp guest order (voice, order-intent) failed: %s", e)
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'order_failed'))
                                continue
    
                            # Default: staff voice that is NOT clearly an incident → Today's Orders (guest capture).
                            # This avoids infer_incident_type("customer") → Service ticket when staff are taking orders.
                            if not should_route_whatsapp_voice_to_incident(transcript or ''):
                                rest_o = getattr(user, 'restaurant', None)
                                if not rest_o:
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        "Your account has no restaurant context. Contact your manager.",
                                    )
                                    continue
                                tstrip = (transcript or '').strip()
                                if not tstrip or len(tstrip) < 8:
                                    session.state = 'awaiting_order_clarification'
                                    session.context['pending_order'] = {
                                        'source': 'voice',
                                        'audio_url': media_url,
                                        'media_id': media_id,
                                        'transcript': tstrip or '',
                                    }
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'order_clarify_audio'))
                                    continue
                                try:
                                    order = _create_staff_captured_order_parsed(rest_o, user, tstrip, "VOICE")
                                    preview = tstrip[:400] + ('…' if len(tstrip) > 400 else '')
                                    session.state = 'idle'
                                    session.context.pop('pending_order', None)
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(
                                            user,
                                            'order_recorded',
                                            order_id=str(order.id)[:8],
                                            preview=f"Details:\n{preview}",
                                            followup=order_recorded_followup(user, order),
                                        ),
                                    )
                                except Exception as e:
                                    logger.exception("WhatsApp guest order (voice, default order path) failed: %s", e)
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'order_failed'))
                                continue
    
                            # If transcription failed / unclear, ask for clarification BEFORE creating a ticket
                            if not transcript or len((transcript or '').strip()) < 8:
                                session.state = 'awaiting_incident_clarification'
                                session.context['pending_incident'] = {
                                    'source': 'voice',
                                    'audio_url': media_url,
                                    'media_id': media_id,
                                    'transcript': transcript,
                                }
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_clarify_audio'))
                                continue
    
                            # Extract structured incident details (no ticket if critical details are missing)
                            from staff.models_task import SafetyConcernReport
                            from scheduling.models import AssignedShift
    
                            def _infer_shift(u, when_dt):
                                try:
                                    qs = AssignedShift.objects.filter(
                                        staff=u,
                                        shift_date=when_dt.date(),
                                        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                    )
                                    # Prefer shifts that overlap the occurred time, else first shift that day.
                                    overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                    return overlap or qs.order_by('start_time').first()
                                except Exception:
                                    return None
    
                            now = timezone.now()
                            incident_type = infer_incident_type(transcript)
                            occurred_at = extract_occurred_at(transcript, now)
    
                            missing = []
                            if not incident_type:
                                missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                            if not occurred_at:
                                missing.append("time of occurrence (e.g., today 3pm)")
    
                            if missing:
                                # Only require clarification if we couldn't infer an incident type.
                                if not incident_type:
                                    session.state = 'awaiting_incident_clarification'
                                    session.context['pending_incident'] = {
                                        'source': 'voice',
                                        'audio_url': media_url,
                                        'media_id': media_id,
                                        'transcript': transcript,
                                    }
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                    )
                                    continue
                                # If only time is missing, default to "now" so the incident is still recorded.
                                occurred_at = occurred_at or now
    
                            shift_obj = _infer_shift(user, occurred_at) if occurred_at else None
                            severity = infer_severity(transcript)
    
                            ticket = _create_safety_concern_from_whatsapp(
                                user=user,
                                description=transcript,
                                incident_type=incident_type,
                                severity=severity,
                                occurred_at=occurred_at,
                                shift=shift_obj,
                                audio_evidence=[media_url] if media_url else [],
                            )
                            _finish_whatsapp_incident_turn(
                                notification_service,
                                ticket,
                                session,
                                transcript,
                                user,
                                phone_digits,
                                incident_type=ticket.incident_type,
                                occurred_at=occurred_at,
                                R=R,
                            )
    
                            # Notify Manager (best-effort)
                            try:
                                manager = CustomUser.objects.filter(restaurant=user.restaurant, role__in=['MANAGER', 'ADMIN']).order_by('id').first()
                                if manager and getattr(manager, 'phone', None):
                                    notif_msg = R(
                                        manager,
                                        "incident_manager_notify",
                                        name=user.get_full_name(),
                                        type=incident_type,
                                        date=occurred_str,
                                        preview=f"{transcript[:200]}{'…' if len(transcript or '') > 200 else ''}",
                                    )
                                    notification_service.send_whatsapp_text(manager.phone, notif_msg)
                            except Exception:
                                pass
                            continue
    
                        # ------------------------------------------------------------------
                        # 4. GPS CLOCK-IN (location message, location_reply interactive, BSP quirks)
                        # ------------------------------------------------------------------
                        if _gps_clock_in_applies_to_whatsapp_message(msg, session):
                            loc, lat_raw, lon_raw = _extract_whatsapp_inbound_location(msg)

                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            lat_c, lon_c = _coerce_whatsapp_location_lat_lon(lat_raw, lon_raw)
                            if (lat_c is None or lon_c is None) and msg_type == "text" and text_body:
                                tb_pair = _parse_lat_lon_from_clock_in_text(text_body.strip())
                                if tb_pair:
                                    lat_c, lon_c = tb_pair[0], tb_pair[1]
                                    loc = {}
                            if lat_c is None or lon_c is None:
                                notification_service.send_whatsapp_location_request(
                                    phone_digits,
                                    R(user, "share_location_prompt"),
                                )
                                continue
                            try:
                                _process_whatsapp_clock_in_from_gps(user, phone_digits, session, lat_c, lon_c, loc, R)
                            except Exception:
                                logger.exception(
                                    "WhatsApp GPS clock-in failed phone=%s msg_type=%s",
                                    phone_digits,
                                    msg.get("type"),
                                )
                                _safe_whatsapp_text_send(
                                    phone_digits,
                                    R(user, "generic_error"),
                                    log_ctx="whatsapp_clock_in_gps_outer_err",
                                )
                            continue
    
                        # ------------------------------------------------------------------
                        # 5. HANDLE TEXT COMMANDS & STATES
                        # ------------------------------------------------------------------
                        raw_body = (text_body or '').strip() if text_body else ''
                        body = raw_body.lower() if raw_body else ''
    
                        if msg_type == 'text' and session and session.state == 'awaiting_clock_in_location':
                            coord_pair = _parse_lat_lon_from_clock_in_text(raw_body)
                            if coord_pair and user:
                                lat_g, lon_g = coord_pair
                                try:
                                    _process_whatsapp_clock_in_from_gps(user, phone_digits, session, lat_g, lon_g, {}, R)
                                except Exception:
                                    logger.exception(
                                        "WhatsApp text GPS clock-in failed phone=%s",
                                        phone_digits,
                                    )
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, "generic_error"),
                                    )
                                continue
    
                        if not body:
                            continue
    
                        # Recover from Space/LLM opening-float detours — always re-prompt GPS.
                        try:
                            from staff.whatsapp_escalation import looks_like_cash_clock_in_followup
    
                            if user and looks_like_cash_clock_in_followup(raw_body):
                                if session.state != 'awaiting_clock_in_location':
                                    session.state = 'awaiting_clock_in_location'
                                    session.save(update_fields=['state'])
                                notification_service.send_whatsapp_location_request(
                                    phone_digits,
                                    "Share your location to clock in.",
                                )
                                continue
                        except Exception:
                            logger.exception("WhatsApp cash-float recovery failed phone=%s", phone_digits)
    
                        # Staff → manager escalations (wages, payslip, HR docs) — Django-owned.
                        if _process_whatsapp_staff_escalation(
                            notification_service,
                            user,
                            phone_digits,
                            session,
                            raw_body,
                            wamid=wamid or '',
                            msg=msg,
                        ):
                            continue
    
                        # My shifts / schedule — Django-owned (never let Space invent fetch failures).
                        try:
                            from staff.whatsapp_my_shifts import process_whatsapp_my_shifts
    
                            if process_whatsapp_my_shifts(
                                notification_service, user, phone_digits, raw_body
                            ):
                                continue
                        except Exception:
                            logger.exception("WhatsApp my-shifts handler failed phone=%s", phone_digits)
    
                        # Dashboard.Task lifecycle — accept / start / done / unable (never Mastra).
                        try:
                            from notifications.dashboard_task_whatsapp import (
                                handle_dashboard_task_whatsapp_reply,
                            )
    
                            if handle_dashboard_task_whatsapp_reply(
                                notification_service=notification_service,
                                user=user,
                                phone_digits=phone_digits,
                                text_body=raw_body,
                                session=session,
                            ):
                                continue
                        except Exception:
                            logger.exception(
                                "WhatsApp dashboard-task handler failed phone=%s", phone_digits
                            )
    
                        # Voice surfaced as placeholder text (no transcript): do not fall through to Mastra or incident heuristics.
                        if _looks_like_voice_ui_placeholder(raw_body):
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            if not getattr(user, 'restaurant', None):
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    "Your account has no restaurant context. Contact your manager.",
                                )
                                continue
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'order_clarify_audio'))
                            continue
    
                        body_clean = body.strip()
    
                        # ------------------------------------------------------------------
                        # Guest order — clarification follow-up (typed after unclear voice)
                        # ------------------------------------------------------------------
                        if session.state == 'awaiting_order_clarification' and body_clean in (
                            'cancel', 'annuler', 'exit', 'stop', 'quit', 'إلغاء', 'الغاء',
                        ):
                            session.state = 'idle'
                            session.context.pop('pending_order', None)
                            session.save(update_fields=['state', 'context'])
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'order_cancelled'))
                            continue
    
                        if session.state == 'awaiting_order_clarification':
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            rest_o = getattr(user, 'restaurant', None)
                            if not rest_o:
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    "Your account has no restaurant context. Contact your manager.",
                                )
                                continue
                            pending_o = session.context.get('pending_order') or {}
                            prior_o = (pending_o.get('transcript') or '').strip()
                            combined_o = (prior_o + "\n\n" + raw_body).strip() if prior_o else raw_body.strip()
                            if len(combined_o) < 8:
                                session.context['pending_order'] = {**pending_o, 'transcript': combined_o}
                                session.save(update_fields=['context'])
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'order_clarify_audio'))
                                continue
                            try:
                                order = _create_staff_captured_order_parsed(rest_o, user, combined_o, "VOICE")
                                preview = combined_o[:400] + ('…' if len(combined_o) > 400 else '')
                                session.state = 'idle'
                                session.context.pop('pending_order', None)
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(
                                        user,
                                        'order_recorded',
                                        order_id=str(order.id)[:8],
                                        preview=f"Details:\n{preview}",
                                        followup=order_recorded_followup(user, order),
                                    ),
                                )
                            except Exception as e:
                                logger.exception("WhatsApp guest order (clarification text) failed: %s", e)
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'order_failed'))
                            continue
    
                        # Cancel guest order entry
                        if session.state == 'awaiting_order_voice' and body_clean in (
                            'cancel', 'annuler', 'exit', 'stop', 'quit', 'إلغاء', 'الغاء',
                        ):
                            session.state = 'idle'
                            session.context.pop('pending_order', None)
                            session.save(update_fields=['state', 'context'])
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'order_cancelled'))
                            continue
    
                        # Typed order instead of voice (after "order" prompt)
                        if session.state == 'awaiting_order_voice' and len(raw_body.strip()) >= 8:
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            rest_o = getattr(user, 'restaurant', None)
                            if not rest_o:
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    "Your account has no restaurant context. Contact your manager.",
                                )
                                continue
                            try:
                                order = _create_staff_captured_order_parsed(rest_o, user, raw_body.strip(), "TEXT")
                                preview = raw_body.strip()[:400] + ('…' if len(raw_body.strip()) > 400 else '')
                                session.state = 'idle'
                                session.save(update_fields=['state'])
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(
                                        user,
                                        'order_recorded',
                                        order_id=str(order.id)[:8],
                                        preview=f"Details:\n{preview}",
                                        followup=order_recorded_followup(user, order),
                                    ),
                                )
                            except Exception as e:
                                logger.exception("WhatsApp guest order (text) failed: %s", e)
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'order_failed'))
                            continue
    
                        # Checklist: accept typed yes/no/n/a as well as button replies
                        if session.state == 'in_checklist':
                            # Let "start checklist" / "clock out" intents pass through
                            if _normalize_start_checklist_intent(raw_body or body) or body in ['clock out', 'clock-out', 'clockout']:
                                pass  # Fall through to the handlers below
                            else:
                                body_clean = body.strip()
                                if body_clean in ('yes', 'y'):
                                    response_value = 'yes'
                                elif body_clean == 'no':
                                    response_value = 'no'
                                elif body_clean in ('n/a', 'na', 'n a'):
                                    response_value = 'n_a'
                                else:
                                    response_value = None
                                if response_value and _handle_checklist_response(notification_service, session, user, phone_digits, response_value):
                                    continue
                                notification_service.send_whatsapp_text(phone_digits, R(user, "checklist_invalid_reply"))
                                continue
                        if session.state == 'awaiting_task_photo':
                            notification_service.send_whatsapp_text(
                                phone_digits,
                                R(user, "checklist_photo_needed"),
                            )
                            continue
    
                        # Checklist help free-text (after user taps "Need help")
                        if session.state == 'checklist_help_text':
                            try:
                                from scheduling.models import ShiftTask
                                checklist = session.context.get('checklist', {})
                                pending_task_id = checklist.get('pending_task_id')
                                task = ShiftTask.objects.filter(id=pending_task_id).first() if pending_task_id else None
                                if task:
                                    task.notes = (task.notes or '') + f"\nHelp requested: {raw_body} ({timezone.now().strftime('%H:%M')})"
                                    task.save(update_fields=['notes'])
                                checklist.pop('pending_task_id', None)
                                session.context['checklist'] = checklist
                                session.state = 'in_checklist'
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(phone_digits, "Thanks — noted. Continuing with the next task.")
    
                                # Send next pending task immediately
                                task_ids = checklist.get('tasks', [])
                                pending = list(ShiftTask.objects.filter(id__in=task_ids).exclude(status__in=['COMPLETED', 'CANCELLED']))
                                if not pending:
                                    _sync_checklist_progress_complete(checklist.get('shift_id'), user)
                                    session.context.pop('checklist', None)
                                    session.state = 'idle'
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(phone_digits, "Great job! Your opening checklist is complete. Have a productive shift!")
                                else:
                                    pending_ids = {str(t.id) for t in pending}
                                    next_id = None
                                    for tid in task_ids:
                                        if str(tid) in pending_ids:
                                            next_id = str(tid)
                                            break
                                    next_id = next_id or str(pending[0].id)
                                    checklist['current_task_id'] = next_id
                                    session.context['checklist'] = checklist
                                    _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)
                                    session.save(update_fields=['context'])
                                    nxt = ShiftTask.objects.filter(id=next_id).first()
                                    if nxt:
                                        idx = (task_ids.index(next_id) + 1) if next_id in task_ids else 1
                                        notification_service._send_task_step_to_whatsapp(phone_digits, nxt, idx, len(task_ids), session)
                            except Exception:
                                session.state = 'in_checklist'
                                session.save(update_fields=['state'])
                            continue
    
                        # Optional photo evidence after a text/voice incident was logged
                        if session.state == 'awaiting_incident_photo':
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            from notifications.utils import looks_like_all_clear_ops_check

                            # Skip photo, or manager clarifying "no incident / all clear".
                            if _looks_like_skip_incident_photo(raw_body) or looks_like_all_clear_ops_check(raw_body):
                                session.state = 'idle'
                                session.context.pop('incident_ticket_id', None)
                                session.context.pop('pending_incident', None)
                                session.save(update_fields=['state', 'context'])
                                if _looks_like_skip_incident_photo(raw_body) and not looks_like_all_clear_ops_check(raw_body):
                                    notification_service.send_whatsapp_text(
                                        phone_digits, R(user, 'incident_photo_skipped')
                                    )
                                    continue
                                # All-clear / correction → fall through to Miya for the real ask.
                            else:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_ask_photo'))
                                continue
    
                        # Handle clarification flow for incidents (voice or incomplete report)
                        if session.state == 'awaiting_incident_clarification':
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
    
                            pending = session.context.get('pending_incident') or {}
                            base_text = (pending.get('transcript') or '').strip()
                            combined_text = (base_text + ("\n\nClarification: " + raw_body if raw_body else "")).strip()
    
                            from staff.models_task import SafetyConcernReport
                            from scheduling.models import AssignedShift
    
                            def _infer_shift(u, when_dt):
                                try:
                                    qs = AssignedShift.objects.filter(
                                        staff=u,
                                        shift_date=when_dt.date(),
                                        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                    )
                                    overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                    return overlap or qs.order_by('start_time').first()
                                except Exception:
                                    return None
    
                            now = timezone.now()
                            incident_type = infer_incident_type(combined_text)
                            occurred_at = extract_occurred_at(combined_text, now)
    
                            missing = []
                            if not incident_type:
                                missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                            if not occurred_at:
                                missing.append("time of occurrence (e.g., today 3pm)")
    
                            if missing:
                                # If we still don't know what kind of incident this is, keep clarifying.
                                if not incident_type:
                                    session.context['pending_incident'] = {**pending, 'transcript': combined_text}
                                    session.save(update_fields=['context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                    )
                                    continue
                                # Otherwise, default missing time to "now" so we still log the ticket.
                                occurred_at = occurred_at or now
    
                            shift_obj = _infer_shift(user, occurred_at) if occurred_at else None
                            severity = infer_severity(combined_text)
    
                            ticket = _create_safety_concern_from_whatsapp(
                                user=user,
                                description=combined_text,
                                incident_type=incident_type,
                                severity=severity,
                                occurred_at=occurred_at,
                                shift=shift_obj,
                                audio_evidence=[pending.get('audio_url')] if pending.get('audio_url') else [],
                            )
                            _finish_whatsapp_incident_turn(
                                notification_service,
                                ticket,
                                session,
                                combined_text,
                                user,
                                phone_digits,
                                incident_type=ticket.incident_type,
                                occurred_at=occurred_at,
                                R=R,
                            )
                            continue
                        
    
                        if body in ['hi', 'hello', 'menu', 'help']:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'help'))
                            continue
    
                        # Re-prompt only when we still have no parseable coordinates in this text turn.
                        if session.state == 'awaiting_clock_in_location':
                            coord_again = _parse_lat_lon_from_clock_in_text(raw_body or "")
                            if coord_again and user:
                                lat_g, lon_g = coord_again
                                try:
                                    _process_whatsapp_clock_in_from_gps(user, phone_digits, session, lat_g, lon_g, {}, R)
                                except Exception:
                                    logger.exception(
                                        "WhatsApp awaiting_clock_in re-prompt path GPS failed phone=%s",
                                        phone_digits,
                                    )
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, "generic_error"),
                                    )
                                continue
                            notification_service.send_whatsapp_location_request(
                                phone_digits,
                                R(user, "share_location_prompt"),
                            )
                            continue
    
                        # Clock-in workflow trigger: case-insensitive, handles "clock in", "clockin", "I want to clock in", etc.
                        if _normalize_clock_in_intent(raw_body or body):
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                            if last_event and last_event.event_type == 'in':
                                first_name = getattr(user, "first_name", None) or "Team Member"
                                local_time = timezone.localtime(last_event.timestamp).strftime("%H:%M")
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, "already_clocked_in", time=local_time, name=first_name),
                                )
                                continue
                            rest = getattr(user, 'restaurant', None)
                            if not restaurant_has_clockin_geofence(rest):
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, "no_geofence_configured"),
                                )
                                continue
                            session.state = 'awaiting_clock_in_location'
                            session.save(update_fields=['state'])
                            notification_service.send_whatsapp_location_request(
                                phone_digits,
                                R(user, "share_location_prompt"),
                            )
                            continue
    
                        if body in ['clock out', 'clock-out', 'clockout']:
                            if user:
                                last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                                if last_event and last_event.event_type == 'in':
                                    # Calculate duration
                                    duration = (timezone.now() - last_event.timestamp).total_seconds() / 3600
                                    ClockEvent.objects.create(
                                        staff=user, 
                                        event_type='out', 
                                        device_id='whatsapp',
                                        location_encrypted="PRECISE_GPS" # Placeholder
                                    )
                                    summary_msg = (
                                        f"✅ *Clock-out successful!*\n\n"
                                        f"⏱️ Duration: *{duration:.2f} hours*"
                                    )
                                    notification_service.send_whatsapp_text(phone_digits, summary_msg)
                                    session.state = 'idle'
                                    session.save(update_fields=['state'])
                                else:
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'clockout_no'))
                            else:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                            continue
    
                        # Manual "start checklist" trigger (backup for Miya): validate then start or resume
                        if _normalize_start_checklist_intent(raw_body or body):
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            active_shift = _get_shift_for_checklist(user, allow_standing=True)
                            if not active_shift:
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, "checklist_not_ready"),
                                )
                                continue
                            prog = ShiftChecklistProgress.objects.filter(
                                shift=active_shift, staff=user
                            ).first()
                            if prog and prog.status == 'COMPLETED':
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, "checklist_already_complete"),
                                )
                                continue
                            if prog and prog.status in ('INCOMPLETE_SHIFT_END', 'CANCELLED'):
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, "checklist_closed_shift_ended"),
                                )
                                continue
                            if prog and prog.status == 'IN_PROGRESS':
                                # Resume: restore session and re-send current step
                                task_ids = prog.task_ids or []
                                responses = prog.responses or {}
                                current_id = prog.current_task_id or (task_ids[0] if task_ids else None)
                                if not current_id or not task_ids:
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, "checklist_in_progress_reply_prompt"),
                                    )
                                    continue
                                session.context['checklist'] = {
                                    'shift_id': str(active_shift.id),
                                    'tasks': task_ids,
                                    'current_task_id': current_id,
                                    'responses': responses,
                                    'started_at': getattr(prog, 'created_at', timezone.now()).isoformat(),
                                }
                                session.state = 'in_checklist'
                                session.save(update_fields=['state', 'context'])
                                nxt = ShiftTask.objects.filter(id=current_id).first()
                                if nxt:
                                    idx = (task_ids.index(current_id) + 1) if current_id in task_ids else 1
                                    notification_service._send_task_step_to_whatsapp(phone_digits, nxt, idx, len(task_ids), session)
                                else:
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, "checklist_in_progress_reply_prompt"),
                                    )
                                continue
                            # Not started: start checklist
                            started = notification_service.start_conversational_checklist_after_clock_in(
                                user, active_shift, phone_digits=phone_digits
                            )
                            if started:
                                pass  # First step already sent by service
                            else:
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, "checklist_start_failed"),
                                )
                            continue
    
                        body_clean = (body or '').strip()
                        order_triggers = {
                            'order',
                            'guest order',
                            'take order',
                            'new order',
                            'nouvelle commande',
                            'commande',
                            'commande client',
                            'طلب',
                            'طلبية',
                        }
                        if body_clean.lower() in order_triggers or body_clean in order_triggers:
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            if not getattr(user, 'restaurant', None):
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    "Your account has no restaurant context. Contact your manager.",
                                )
                                continue
                            session.state = 'awaiting_order_voice'
                            session.save(update_fields=['state'])
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'order_voice_prompt'))
                            continue
    
                        incident_triggers = {'report', 'incident', 'issue', 'rapport', 'signalement', 'بلاغ'}
                        if body_clean.lower() in incident_triggers or body_clean in incident_triggers:
                            session.state = 'awaiting_incident_text'
                            session.save(update_fields=['state'])
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_prompt'))
                            continue
                            
                        if session.state == 'awaiting_incident_text':
                            if not user:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue
                            # Use the same structured extraction + clarification rules as voice
                            from scheduling.models import AssignedShift
    
                            def _infer_shift(u, when_dt):
                                try:
                                    qs = AssignedShift.objects.filter(
                                        staff=u,
                                        shift_date=when_dt.date(),
                                        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                    )
                                    overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                    return overlap or qs.order_by('start_time').first()
                                except Exception:
                                    return None
    
                            now = timezone.now()
                            incident_type = infer_incident_type(raw_body)
                            occurred_at = extract_occurred_at(raw_body, now)
    
                            missing = []
                            if not incident_type:
                                missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                            if not occurred_at:
                                missing.append("time of occurrence (e.g., today 3pm)")
    
                            if missing:
                                # If we couldn't infer any incident type, ask for clarification.
                                if not incident_type:
                                    session.state = 'awaiting_incident_clarification'
                                    session.context['pending_incident'] = {'source': 'text', 'transcript': raw_body}
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                    )
                                    continue
                                # If we only lack a precise time, default to "now" and still record the report.
                                occurred_at = occurred_at or now
                            else:
                                # We have both type and time; default occurred_at if somehow still missing
                                occurred_at = occurred_at or now
    
                            shift_obj = _infer_shift(user, occurred_at) if occurred_at else None
                            severity = infer_severity(raw_body)
    
                            try:
                                ticket = _create_safety_concern_from_whatsapp(
                                    user=user,
                                    description=raw_body,
                                    incident_type=incident_type or 'General',
                                    severity=severity,
                                    occurred_at=occurred_at,
                                    shift=shift_obj,
                                )
                                _finish_whatsapp_incident_turn(
                                    notification_service,
                                    ticket,
                                    session,
                                    raw_body,
                                    user,
                                    phone_digits,
                                    incident_type=ticket.incident_type,
                                    occurred_at=occurred_at,
                                    R=R,
                                )
                            except Exception as e:
                                logger.exception("Failed to create incident from text: %s", e)
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_failed'))
                            continue
    
                        # Fallback: if the message looks like an incident description, log it directly.
                        if user:
                            from scheduling.models import AssignedShift
    
                            incident_type = infer_incident_type(raw_body)
                            if incident_type or (session.context or {}).get('incident_photo_media_id'):
                                now = timezone.now()
                                occurred_at = now
                                incident_type = incident_type or 'General'
    
                                def _infer_shift_text(u, when_dt):
                                    try:
                                        qs = AssignedShift.objects.filter(
                                            staff=u,
                                            shift_date=when_dt.date(),
                                            status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                        )
                                        overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                        return overlap or qs.order_by('start_time').first()
                                    except Exception:
                                        return None
    
                                shift_obj = _infer_shift_text(user, occurred_at)
                                severity = infer_severity(raw_body)
    
                                try:
                                    ticket = _create_safety_concern_from_whatsapp(
                                        user=user,
                                        description=raw_body,
                                        incident_type=incident_type,
                                        severity=severity,
                                        occurred_at=occurred_at,
                                        shift=shift_obj,
                                    )
                                    _finish_whatsapp_incident_turn(
                                        notification_service,
                                        ticket,
                                        session,
                                        raw_body,
                                        user,
                                        phone_digits,
                                        incident_type=ticket.incident_type,
                                        occurred_at=occurred_at,
                                        R=R,
                                    )
                                    continue
                                except Exception:
                                    logger.exception("Failed to create incident from fallback text")
                                    # Fall through to generic unrecognized response if anything fails
                                    pass
    
                        # Final fallback — Miya handles free-form ops chat on shared Mizan number
                        if miya_wa and raw_body and session:
                            from miya.services.whatsapp import enqueue_miya_whatsapp_turn

                            if enqueue_miya_whatsapp_turn(
                                user=user,
                                phone_digits=phone_digits,
                                message_text=raw_body,
                                session=session,
                            ):
                                continue

                        notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_failed' if 'chair' in raw_body.lower() or 'broken' in raw_body.lower() else 'unrecognized'))
    
                    except Exception:
                        logger.exception(
                            "WhatsApp inbound turn failed wamid=%s", wamid
                        )
                        try:
                            if phone_digits:
                                # locals().get avoids a NameError if the exception hit before
                                # `user` was resolved for this turn — R() already falls back
                                # to English for a None user.
                                _safe_whatsapp_text_send(
                                    phone_digits,
                                    R(locals().get('user'), "generic_error"),
                                    log_ctx="whatsapp_turn_error",
                                )
                        except Exception:
                            pass
                    finally:
                        _mark_whatsapp_message_processed(wamid)
        return Response({'success': True})
    except Exception as e:
        logger.error("Webhook error: %s", e, exc_info=True)
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def unregister_device_token(request):
    """Unregister a device token"""
    try:
        token = request.data.get('token')
        
        if not token:
            return Response({
                'success': False,
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        DeviceToken.objects.filter(
            user=request.user,
            token=token
        ).update(is_active=False)
        
        return Response({
            'success': True,
            'message': 'Device token unregistered successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def user_device_tokens(request):
    """List active device tokens for the user"""
    try:
        tokens = DeviceToken.objects.filter(
            user=request.user,
            is_active=True
        ).order_by('-last_used')
        
        serializer = DeviceTokenSerializer(tokens, many=True)
        
        return Response({
            'success': True,
            'tokens': serializer.data
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def send_test_notification(request):
    """Send a test notification to the user (for testing purposes)"""
    try:
        message = request.data.get('message', 'This is a test notification')
        channels = request.data.get('channels', ['app'])
        
        notification_service.send_custom_notification(
            recipient=request.user,
            message=message,
            notification_type='SYSTEM_ALERT',
            channels=channels
        )
        
        return Response({
            'success': True,
            'message': 'Test notification sent successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def delete_notification(request, notification_id):
    """Delete a specific notification"""
    try:
        notification = get_object_or_404(
            Notification, 
            id=notification_id, 
            recipient=request.user
        )
        
        notification.delete()
        
        return Response({
            'success': True,
            'message': 'Notification deleted successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_notification_actions(request):
    """Perform bulk actions on notifications"""
    try:
        action = request.data.get('action')  # 'mark_read', 'delete'
        notification_ids = request.data.get('notification_ids', [])
        
        if not action or not notification_ids:
            return Response({
                'success': False,
                'error': 'Action and notification_ids are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        notifications = Notification.objects.filter(
            id__in=notification_ids,
            recipient=request.user
        )
        
        if action == 'mark_read':
            count = notifications.filter(read_at__isnull=True).update(
                read_at=timezone.now()
            )
            message = f'{count} notifications marked as read'
            
        elif action == 'delete':
            count = notifications.count()
            notifications.delete()
            message = f'{count} notifications deleted'
            
        else:
            return Response({
                'success': False,
                'error': 'Invalid action'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': True,
            'message': message,
            'count': count
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationListView(generics.ListAPIView):
    """List notifications for the authenticated user"""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        user = self.request.user
        # Only show notifications from the last 12 hours (older ones are auto-cleared from the list).
        # IMPORTANT: this class is the effective NotificationListView because a
        # previous class of the same name is declared earlier in this file and
        # gets shadowed at import time. Keep the same eager-loading here so the
        # notifications panel (which polls continuously) does not fan out into
        # per-row sender + attachment queries.
        cutoff = timezone.now() - timedelta(hours=12)
        queryset = (
            Notification.objects
            .filter(recipient=user, created_at__gte=cutoff)
            .select_related('recipient', 'sender')
            .prefetch_related('attachments')
            .order_by('-created_at')
        )

        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(read_at__isnull=False)
            else:
                queryset = queryset.filter(read_at__isnull=True)

        notification_type = self.request.query_params.get('type')
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)

        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)

        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)

        return queryset
