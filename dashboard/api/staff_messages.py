"""Admin → Staff WhatsApp messaging surface for the dashboard.

Two views power the dashboard's "Staff Messages" widget:

- ``StaffMessagesRecentView`` (GET): lists recent outbound WhatsApp
  messages from the manager's restaurant, with delivery status
  (SENT / DELIVERED / READ / FAILED) sourced from
  ``NotificationLog`` rows the WhatsApp webhook keeps in sync.
- ``StaffMessagesSendView`` (POST): structured composer endpoint.
  Accepts ``recipient_user_id`` (one), ``recipient_user_ids`` (bulk),
  or ``tags`` / ``departments`` / ``roles`` (with ``body`` and optional
  priority / template) and fans out via
  ``notification_service.send_announcement_to_audience``
  so the new row lands in the same NotificationLog feed Miya's
  ``inform_staff`` tool already uses. Calling this directly (instead
  of going through the Miya chat) is the structured-form alternative
  for managers who want a typed UI rather than a free-text agent
  prompt.
"""

from __future__ import annotations

from typing import Any

from django.utils import timezone
from rest_framework import permissions, status as http_status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache
from core.read_through_cache import get_or_set, safe_cache_delete
from notifications.models import Notification, NotificationLog
from notifications.services import NotificationService

DEFAULT_LIMIT = 10
MAX_LIMIT = 50
# Bulk direct-message cap — matches what the dashboard composer allows
# when a manager picks several teammates at once.
MAX_BULK_RECIPIENT_IDS = 100

# Short Redis TTL on the "recent messages" feed, paired with an HTTP
# ETag/Cache-Control response. The widget on the dashboard refetches
# every 30s; matching the TTL to that cadence means the hot path
# hits RDS at most once per polling-interval per tenant, and the
# browser short-circuits repeated polls with a 304 when the payload
# hasn't changed. Send-writes bust the slice immediately so the UI
# feels instant after a manager hits "Send".
_RECENT_CACHE_TTL = 25


def _recent_cache_key(restaurant_id, limit: int) -> str:
    return f"dashboard:staff_messages:recent:v1:{restaurant_id}:{int(limit)}"


def _invalidate_recent_cache(restaurant_id) -> None:
    """Wipe every limit-slice for this tenant. We only surface three
    limits in practice (10 default, a 25/50 debug override), so an
    exhaustive wipe is cheap and means we never have to propagate the
    widget's current limit into the send handler.
    """
    for lim in (DEFAULT_LIMIT, 25, MAX_LIMIT):
        safe_cache_delete(_recent_cache_key(restaurant_id, lim))


def _coerce_str_list(raw, *, upper: bool = False) -> list[str]:
    if raw is None:
        return []
    if isinstance(raw, str):
        s = raw.strip()
        return [s.upper() if upper else s] if s else []
    if isinstance(raw, (list, tuple)):
        out: list[str] = []
        for item in raw:
            s = str(item).strip()
            if not s:
                continue
            out.append(s.upper() if upper else s)
        return out
    return []


def _dedupe_preserve_order(ids: list[str]) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for i in ids:
        if i in seen:
            continue
        seen.add(i)
        out.append(i)
    return out


def _resolve_send_audience(data: dict) -> tuple[str, dict] | Response:
    """Pick exactly one targeting mode for the composer.

    Returns ``(kind, payload)`` where *kind* is ``single`` | ``bulk`` |
    ``tags`` | ``departments`` | ``roles`` and *payload* holds normalized
    values for that branch, or a DRF ``Response`` error object.
    """
    recipient_id = (data.get("recipient_user_id") or "").strip()
    rids_raw = data.get("recipient_user_ids")

    tags_in = _coerce_str_list(data.get("tags"))
    departments = _coerce_str_list(data.get("departments"))
    roles = _coerce_str_list(data.get("roles"), upper=True)

    bulk_ids: list[str] = []
    if rids_raw is not None:
        if not isinstance(rids_raw, (list, tuple)):
            return Response(
                {
                    "error": "recipient_user_ids must be a list of user IDs.",
                    "code": "invalid_audience",
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        bulk_ids = _dedupe_preserve_order(
            [str(x).strip() for x in rids_raw if str(x).strip()]
        )

    modes: list[str] = []
    if recipient_id:
        modes.append("single")
    if bulk_ids:
        modes.append("bulk")
    if tags_in:
        modes.append("tags")
    if departments:
        modes.append("departments")
    if roles:
        modes.append("roles")

    if not modes:
        return Response(
            {
                "error": (
                    "Specify an audience: recipient_user_id (one person), "
                    "recipient_user_ids (several people), tags, departments, "
                    "or roles."
                ),
                "code": "audience_required",
            },
            status=http_status.HTTP_400_BAD_REQUEST,
        )
    if len(modes) > 1:
        return Response(
            {
                "error": (
                    "Use only one audience type at a time: "
                    "one person, several people, tags, departments, or roles."
                ),
                "code": "audience_conflict",
            },
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    if modes[0] == "single":
        return "single", {"recipient_user_id": recipient_id}
    if modes[0] == "bulk":
        if len(bulk_ids) > MAX_BULK_RECIPIENT_IDS:
            return Response(
                {
                    "error": (
                        f"Too many recipients (max {MAX_BULK_RECIPIENT_IDS}). "
                        "Narrow the list or use tags / department / role."
                    ),
                    "code": "too_many_recipients",
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        return "bulk", {"staff_ids": bulk_ids}

    if modes[0] == "tags":
        from accounts.staff_tags import (
            CANONICAL_STAFF_TAG_SET,
            normalize_tags as _normalize_tags,
        )

        normalised = _normalize_tags(tags_in) or []
        tags = [t for t in normalised if t in CANONICAL_STAFF_TAG_SET]
        if not tags:
            return Response(
                {
                    "error": (
                        "Pick at least one valid team tag "
                        "(e.g. Kitchen, Service, Housekeeping)."
                    ),
                    "code": "no_valid_tags",
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        return "tags", {"tags": tags}

    if modes[0] == "departments":
        return "departments", {"departments": departments}
    return "roles", {"roles": roles}


def _same_tenant(
    user,
    restaurant,
    staff_link_model,
) -> bool:
    return (
        user.restaurant_id == restaurant.id
        or staff_link_model.objects.filter(
            user=user,
            restaurant=restaurant,
            is_active=True,
        ).exists()
    )


def _validate_direct_recipients(
    staff_ids: list[str],
    request_user,
    restaurant,
) -> tuple[list[Any], Response | None]:
    """Load users, enforce tenant / phone / WhatsApp format. *staff_ids*
    should be de-duped. Returns ``(users, None)`` or ``([], error)``."""
    from accounts.models import CustomUser, StaffRestaurantLink
    from notifications.services import normalize_whatsapp_phone

    sender_id = str(request_user.id)
    ids_no_self = [i for i in staff_ids if str(i) != sender_id]
    if not ids_no_self:
        return [], Response(
            {
                "error": (
                    "You can't message only yourself. Add at least one "
                    "teammate to the list."
                ),
                "code": "self_send",
            },
            status=http_status.HTTP_400_BAD_REQUEST,
        )

    wanted = set(ids_no_self)
    users = list(CustomUser.objects.filter(pk__in=wanted, is_active=True))
    found = {str(u.id) for u in users}
    if found != wanted:
        return [], Response(
            {"error": "One or more recipients were not found or are inactive."},
            status=http_status.HTTP_404_NOT_FOUND,
        )

    for recipient in users:
        if not _same_tenant(recipient, restaurant, StaffRestaurantLink):
            return [], Response(
                {"error": "That person isn't part of your team."},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        if not (recipient.phone or "").strip():
            who = recipient.first_name or "This person"
            return [], Response(
                {
                    "error": (
                        f"{who} doesn't have a WhatsApp number on file. "
                        "Add one in their staff profile, then try again."
                    ),
                    "code": "no_phone",
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        _, phone_err = normalize_whatsapp_phone(recipient.phone)
        if phone_err:
            who = recipient.first_name or "This person"
            return [], Response(
                {
                    "error": (
                        f"{who}'s phone number ({recipient.phone}) isn't in a "
                        "WhatsApp-ready format. Open their staff profile and "
                        "save it with the country code "
                        "(e.g. +212 661 234 567)."
                    ),
                    "code": "invalid_phone_format",
                    "phone_error": phone_err,
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

    return users, None

# Priority decorator prefixes — kept short so the staff member sees
# the urgency cue before WhatsApp truncates the bubble preview on the
# notification ribbon. Aligns with the persona's "smart formatting"
# expectation. We don't translate the prefix; the body is already
# in whatever language the manager typed.
_PRIORITY_PREFIX = {
    "URGENT": "🚨 URGENT — ",
    "HIGH": "⚠️ ",
    "NORMAL": "",
    "LOW": "",
}

# Built-in message templates the FE composer surfaces as quick-pick
# chips. Returning the catalog from the backend (instead of hard-
# coding it on the FE) lets us localise / extend them later without
# a frontend release. Each template body is a fill-in-the-blank
# prompt the manager edits before sending — never auto-sent.
TEMPLATE_CATALOG: list[dict[str, str]] = [
    {
        "id": "URGENT_CALL_IN",
        "label": "Urgent call-in",
        "body": "We need you at the restaurant ASAP. Can you come in right now?",
        "priority": "URGENT",
    },
    {
        "id": "SHIFT_REMINDER",
        "label": "Shift reminder",
        "body": "Quick reminder you're on shift today. Please confirm you're on your way.",
        "priority": "NORMAL",
    },
    {
        "id": "THANK_YOU",
        "label": "Thank-you note",
        "body": "Great work today — thank you for your effort. 🙏",
        "priority": "NORMAL",
    },
    {
        "id": "POLICY_HEADS_UP",
        "label": "Policy heads-up",
        "body": "Heads-up on a small policy change starting tomorrow:",
        "priority": "NORMAL",
    },
    {
        "id": "WAITING_ON_REPLY",
        "label": "Waiting on reply",
        "body": "Hi, just following up — could you let me know about ",
        "priority": "NORMAL",
    },
]


_STATUS_RANK = {
    "READ": 4,
    "DELIVERED": 3,
    "SENT": 2,
    "PENDING": 1,
    "FAILED": 0,
}


def _serialize_log(log: NotificationLog) -> dict[str, Any]:
    """Compact shape the dashboard widget renders.

    The widget only needs: who got the message, a short preview of
    what was said, the current delivery status, and the timestamps
    that map to ✓ / ✓✓ / ✓✓-blue ticks. Anything heavier (full
    notification body, full response_data) stays on the detail page.
    """
    notif = log.notification
    recipient = getattr(notif, "recipient", None) if notif else None
    sender = getattr(notif, "sender", None) if notif else None

    body = ((notif.message if notif else "") or "").strip()
    preview = body[:140] + ("…" if len(body) > 140 else "")

    return {
        "id": str(log.id),
        "notification_id": str(notif.id) if notif else None,
        "external_id": log.external_id or "",
        "status": log.status,
        "channel": log.channel,
        "recipient": (
            {
                "id": str(recipient.id),
                "name": (
                    f"{(recipient.first_name or '').strip()} "
                    f"{(recipient.last_name or '').strip()}"
                ).strip()
                or recipient.email
                or log.recipient_address,
                "phone": log.recipient_address or recipient.phone or "",
                "role": getattr(recipient, "role", "") or "",
            }
            if recipient
            else {
                "id": None,
                "name": log.recipient_address or "",
                "phone": log.recipient_address or "",
                "role": "",
            }
        ),
        "sender": (
            {
                "id": str(sender.id),
                "name": (
                    f"{(sender.first_name or '').strip()} "
                    f"{(sender.last_name or '').strip()}"
                ).strip()
                or sender.email,
            }
            if sender
            else None
        ),
        "preview": preview,
        "priority": (notif.priority if notif else "MEDIUM") or "MEDIUM",
        "sent_at": log.sent_at.isoformat() if log.sent_at else None,
        "delivered_at": (
            log.delivered_at.isoformat() if log.delivered_at else None
        ),
        "error_message": log.error_message or "",
    }


class StaffMessagesRecentView(APIView):
    """GET /api/dashboard/staff-messages/recent/?limit=10

    Returns the most recent outbound WhatsApp messages for this
    manager's restaurant. Scoped to manager-initiated sends (i.e.
    rows whose ``notification.sender`` is set) so the feed is a
    "what did the team send out today?" view rather than the
    auto-fired notification stream (shift assignments, system
    nudges, etc.).
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(request.query_params.get("limit") or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        cache_key = _recent_cache_key(restaurant.id, limit)

        def _compute_recent_payload():
            # Show outbound WhatsApp from managers for *this* workspace's
            # team: primary members (recipient.restaurant == here) OR staff
            # linked here via StaffRestaurantLink (primary restaurant may
            # be another branch — otherwise their messages never appear).
            from accounts.models import StaffRestaurantLink
            from django.db.models import Q

            linked_here = StaffRestaurantLink.objects.filter(
                restaurant=restaurant,
                is_active=True,
            ).values_list("user_id", flat=True)
            recipient_scope = Q(notification__recipient__restaurant=restaurant) | Q(
                notification__recipient_id__in=linked_here
            )
            qs = (
                NotificationLog.objects.filter(
                    channel="whatsapp",
                    notification__sender__isnull=False,
                )
                .filter(recipient_scope)
                .select_related(
                    "notification",
                    "notification__recipient",
                    "notification__sender",
                )
                .order_by("-sent_at")[:limit]
            )

            items = [_serialize_log(log) for log in qs]

            # Counts so the widget can render a tiny "12 sent · 3 read"
            # summary without re-walking the list on the FE side.
            counts = {"SENT": 0, "DELIVERED": 0, "READ": 0, "FAILED": 0}
            for it in items:
                s = it.get("status") or ""
                if s in counts:
                    counts[s] += 1

            return {
                "items": items,
                "counts": counts,
                "templates": TEMPLATE_CATALOG,
            }

        payload = get_or_set(cache_key, _RECENT_CACHE_TTL, _compute_recent_payload)
        # generated_at is computed per-response (not cached) so the UI
        # can still display a "last refreshed" timestamp even when the
        # payload is served from Redis.
        payload = dict(payload)
        payload["generated_at"] = timezone.now().isoformat()

        return json_response_with_cache(
            request,
            payload,
            max_age=_RECENT_CACHE_TTL,
            private=True,
            stale_while_revalidate=10,
        )


class StaffMessagesSendView(APIView):
    """POST /api/dashboard/staff-messages/send/

    Pick **one** audience type:

    - One teammate: ``recipient_user_id``, ``body`` …
    - Several: ``recipient_user_ids`` (max ``MAX_BULK_RECIPIENT_IDS``)
    - By staff tag: ``tags`` (canonical, e.g. ``KITCHEN``)
    - By department: ``departments`` (``StaffProfile.department``, case-insensitive)
    - By job role: ``roles`` (``CustomUser.role``, e.g. ``CHEF``)

    Optional: ``priority``, ``template_id``. All paths use the same
    ``send_announcement_to_audience`` pipeline as Miya's ``inform_staff``.
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        data = request.data or {}
        body = (data.get("body") or "").strip()
        priority = (data.get("priority") or "NORMAL").upper()
        template_id = (data.get("template_id") or "").strip() or None

        if not body:
            return Response(
                {"error": "body is required"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        if priority not in _PRIORITY_PREFIX:
            priority = "NORMAL"

        resolved = _resolve_send_audience(data)
        if isinstance(resolved, Response):
            return resolved
        kind, payload = resolved

        prefix = _PRIORITY_PREFIX.get(priority, "")
        final_body = f"{prefix}{body}".strip()

        title = "Message from manager"
        if priority == "URGENT":
            title = "Urgent message from manager"

        service = NotificationService()
        staff_ids_kw: list[str] | None = None
        roles_kw: list[str] | None = None
        departments_kw: list[str] | None = None
        tags_kw: list[str] | None = None
        audience_meta: dict[str, Any] = {"mode": kind}
        single_recipient = None

        if kind == "single":
            from accounts.models import CustomUser, StaffRestaurantLink

            rid = payload["recipient_user_id"]
            try:
                recipient = CustomUser.objects.get(pk=rid, is_active=True)
            except CustomUser.DoesNotExist:
                return Response(
                    {"error": "Recipient not found or inactive."},
                    status=http_status.HTTP_404_NOT_FOUND,
                )

            if str(recipient.id) == str(request.user.id):
                return Response(
                    {
                        "error": (
                            "You can't message yourself. Pick a teammate from "
                            "the list to send the WhatsApp."
                        ),
                        "code": "self_send",
                    },
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            if not _same_tenant(recipient, restaurant, StaffRestaurantLink):
                return Response(
                    {"error": "That person isn't part of your team."},
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            if not (recipient.phone or "").strip():
                return Response(
                    {
                        "error": (
                            f"{recipient.first_name or 'This person'} doesn't "
                            "have a WhatsApp number on file. Add one in their "
                            "staff profile, then try again."
                        ),
                        "code": "no_phone",
                    },
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            from notifications.services import normalize_whatsapp_phone

            _, phone_err = normalize_whatsapp_phone(recipient.phone)
            if phone_err:
                return Response(
                    {
                        "error": (
                            f"{recipient.first_name or 'This person'}'s phone "
                            f"number ({recipient.phone}) isn't in a WhatsApp-"
                            "ready format. Open their staff profile and save it "
                            "with the country code (e.g. +212 661 234 567)."
                        ),
                        "code": "invalid_phone_format",
                        "phone_error": phone_err,
                    },
                    status=http_status.HTTP_400_BAD_REQUEST,
                )

            staff_ids_kw = [str(recipient.id)]
            single_recipient = recipient
            audience_meta["recipient_user_id"] = str(recipient.id)

        elif kind == "bulk":
            staff_ids_kw = payload["staff_ids"]
            users, err_resp = _validate_direct_recipients(
                staff_ids_kw, request.user, restaurant
            )
            if err_resp:
                return err_resp
            staff_ids_kw = [str(u.id) for u in users]
            audience_meta["recipient_user_ids"] = list(staff_ids_kw)
            audience_meta["recipient_count"] = len(users)

        elif kind == "tags":
            tags_kw = payload["tags"]
            audience_meta["tags"] = list(tags_kw)

        elif kind == "departments":
            departments_kw = payload["departments"]
            audience_meta["departments"] = list(departments_kw)

        else:
            roles_kw = payload["roles"]
            audience_meta["roles"] = list(roles_kw)

        success, count, err, details = service.send_announcement_to_audience(
            restaurant_id=str(restaurant.id),
            title=title,
            message=final_body,
            sender=request.user,
            staff_ids=staff_ids_kw,
            roles=roles_kw,
            departments=departments_kw,
            tags=tags_kw,
            channels=["whatsapp"],
        )

        if not success or count == 0:
            return Response(
                {
                    "success": False,
                    "error": err or "Couldn't send the message.",
                    "details": details or {},
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        _invalidate_recent_cache(restaurant.id)

        if single_recipient:
            rid_primary = getattr(single_recipient, "restaurant_id", None)
            if rid_primary and rid_primary != restaurant.id:
                _invalidate_recent_cache(rid_primary)
        elif kind == "bulk" and staff_ids_kw:
            from accounts.models import CustomUser

            other_restaurants = (
                CustomUser.objects.filter(pk__in=staff_ids_kw)
                .exclude(restaurant_id=restaurant.id)
                .values_list("restaurant_id", flat=True)
                .distinct()
            )
            for rid in other_restaurants:
                if rid:
                    _invalidate_recent_cache(rid)

        whatsapp_sent = (details or {}).get("whatsapp_sent", 0)
        recipients_whatsapp_failed = (details or {}).get(
            "recipients_whatsapp_failed", []
        )
        recipients_without_phone = (details or {}).get(
            "recipients_without_phone", []
        )

        failure_reason = None
        log = None
        if single_recipient:
            log = (
                NotificationLog.objects.filter(
                    channel="whatsapp",
                    notification__sender=request.user,
                    notification__recipient=single_recipient,
                )
                .select_related(
                    "notification",
                    "notification__recipient",
                    "notification__sender",
                )
                .order_by("-sent_at")
                .first()
            )
            if log and log.status == "FAILED":
                raw = (log.error_message or "").strip()
                if raw:
                    try:
                        import json as _json

                        parsed = _json.loads(raw)
                        failure_reason = (
                            (parsed.get("error") or {}).get("message") or raw[:200]
                        )
                    except Exception:
                        failure_reason = raw[:200]

        whatsapp_failed = bool(recipients_whatsapp_failed) or bool(
            recipients_without_phone
        )

        return Response(
            {
                "success": True,
                "whatsapp_sent": whatsapp_sent,
                "whatsapp_failed": whatsapp_failed,
                "failure_reason": failure_reason,
                "notified_count": count,
                "log": _serialize_log(log) if log else None,
                "template_id": template_id,
                "audience": audience_meta,
            }
        )
