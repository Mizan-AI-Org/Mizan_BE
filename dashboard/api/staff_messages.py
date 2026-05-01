"""Admin → Staff WhatsApp messaging surface for the dashboard.

Two views power the dashboard's "Staff Messages" widget:

- ``StaffMessagesRecentView`` (GET): lists recent outbound WhatsApp
  messages from the manager's restaurant, with delivery status
  (SENT / DELIVERED / READ / FAILED) sourced from
  ``NotificationLog`` rows the WhatsApp webhook keeps in sync.
- ``StaffMessagesSendView`` (POST): structured composer endpoint.
  Accepts ``{recipient_user_id, body, priority?, template?}`` and
  fans out via ``notification_service.send_announcement_to_audience``
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
            # Tenant scope flows through the recipient: every Notification
            # row holds a recipient FK with a restaurant. We deliberately
            # use the recipient's restaurant (not the sender's) because
            # multi-restaurant managers may sit at HQ and broadcast to a
            # specific branch — the message belongs to that branch's feed.
            qs = (
                NotificationLog.objects.filter(
                    channel="whatsapp",
                    notification__recipient__restaurant=restaurant,
                    notification__sender__isnull=False,
                )
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

    Body: ``{
        "recipient_user_id": "<uuid>",
        "body": "<message>",
        "priority": "URGENT" | "HIGH" | "NORMAL" | "LOW",
        "template_id": "<optional>"
    }``

    Structured composer entry point — the dashboard's "Send to
    staff" form. Goes through the same audience pipeline Miya uses
    (``send_announcement_to_audience``) so the resulting message
    lands in the same NotificationLog feed and inherits the
    WhatsApp delivery / read receipt tracking.
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
        recipient_id = (data.get("recipient_user_id") or "").strip()
        body = (data.get("body") or "").strip()
        priority = (data.get("priority") or "NORMAL").upper()
        template_id = (data.get("template_id") or "").strip() or None

        if not recipient_id:
            return Response(
                {"error": "recipient_user_id is required"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        if not body:
            return Response(
                {"error": "body is required"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )
        if priority not in _PRIORITY_PREFIX:
            priority = "NORMAL"

        # Resolve + tenant-scope the recipient. We mirror the OR used
        # by the staff list view so multi-restaurant teammates linked
        # via StaffRestaurantLink remain valid targets — escalation
        # and direct messaging share the same "anyone on my team"
        # surface.
        from accounts.models import CustomUser, StaffRestaurantLink
        from django.db.models import Q

        try:
            recipient = CustomUser.objects.get(pk=recipient_id, is_active=True)
        except CustomUser.DoesNotExist:
            return Response(
                {"error": "Recipient not found or inactive."},
                status=http_status.HTTP_404_NOT_FOUND,
            )

        same_tenant = (
            recipient.restaurant_id == restaurant.id
            or StaffRestaurantLink.objects.filter(
                user=recipient,
                restaurant=restaurant,
                is_active=True,
            ).exists()
        )
        if not same_tenant:
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
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        # Compose the final WhatsApp body. We prepend a small priority
        # cue (🚨 URGENT — / ⚠️) for HIGH/URGENT so the staff member
        # sees the urgency in the WhatsApp ribbon preview, then keep
        # the manager's text verbatim — no LLM rewrite from this
        # endpoint (Miya owns smart-formatting; this surface is the
        # structured "send what I typed" alternative).
        prefix = _PRIORITY_PREFIX.get(priority, "")
        final_body = f"{prefix}{body}".strip()

        title = "Message from manager"
        if priority == "URGENT":
            title = "Urgent message from manager"

        service = NotificationService()
        success, count, err, details = service.send_announcement_to_audience(
            restaurant_id=str(restaurant.id),
            title=title,
            message=final_body,
            sender=request.user,
            staff_ids=[str(recipient.id)],
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

        # Bust the "recent messages" feed for this tenant so the new row
        # appears on the manager's next poll without waiting for the TTL.
        # Use recipient's restaurant (the feed is keyed that way above).
        _invalidate_recent_cache(recipient.restaurant_id or restaurant.id)

        whatsapp_sent = (details or {}).get("whatsapp_sent", 0)
        recipients_whatsapp_failed = (details or {}).get(
            "recipients_whatsapp_failed", []
        )

        # Look up the freshly-created NotificationLog so the FE can
        # render the new row in the feed without waiting for the next
        # refetch. We pull the most recent log for this recipient
        # (the audience helper just created it).
        log = (
            NotificationLog.objects.filter(
                channel="whatsapp",
                notification__sender=request.user,
                notification__recipient=recipient,
            )
            .select_related(
                "notification",
                "notification__recipient",
                "notification__sender",
            )
            .order_by("-sent_at")
            .first()
        )

        return Response(
            {
                "success": True,
                "whatsapp_sent": whatsapp_sent,
                "whatsapp_failed": bool(recipients_whatsapp_failed),
                "log": _serialize_log(log) if log else None,
                "template_id": template_id,
            }
        )
