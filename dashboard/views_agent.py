"""
Agent endpoints for the dashboard's Tasks & Demands surface.

Exposes HTTP endpoints that Miya / the Mastra agent can call (using
`Authorization: Bearer <MIYA_MASTRA_API_KEY>` OR a user JWT) to create a
dashboard.Task, assign it to a staff member, and send a WhatsApp
notification to that staff member in the same call.

This is the backend half of the Miya capability "Create a task for
Ahmed and tell him on WhatsApp". The frontend piece is the ordinary
Tasks & Demands widget, which polls `/api/dashboard/tasks-demands/`
every 60s and will pick up the new row automatically.

Design notes
------------
- Reuses `_resolve_restaurant_for_agent` from scheduling.views_agent so
  the same "X-Restaurant-Id header | body restaurant_id | JWT
  restaurant | agent-key + sessionId" resolution chain is honoured.
- Assignee resolution is deliberately forgiving so Miya can pass any of
  `user_id`, `email`, `phone`, or a free-text `name` ("Ahmed") and we
  do the fuzzy lookup here instead of making the LLM do it.
- WhatsApp send uses `notification_service.send_whatsapp_text` directly
  (not the preference-gated `send_custom_notification` WhatsApp path),
  because the manager's intent is explicit: they told Miya to notify
  the staff member, so we send.
- An in-app Notification row is also created so the staff member sees
  it in their bell + inbox even if the WhatsApp send fails (e.g.
  missing phone, Meta API down).
- The whole thing is wrapped in `transaction.atomic()` so a failing
  WhatsApp send does NOT leave an orphan Task — we attempt the send
  AFTER the DB commit so the task survives even if WhatsApp fails.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from datetime import date, datetime, timedelta
from difflib import SequenceMatcher
from typing import Any

from django.db import transaction
from django.db.models import Case, IntegerField, Q, Value, When
from django.db.models.functions import Concat
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from accounts.models import CustomUser

from .models import Task
from .serializers import DashboardTaskCompactSerializer

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


_VALID_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}

# Mirrors ``Task.TASK_CATEGORY`` — kept in sync by hand because importing
# the choices tuple from ``models`` would create the same circular-style
# coupling the old code worked hard to avoid.
_VALID_TASK_CATEGORIES = {
    "DOCUMENT", "HR", "SCHEDULING", "PAYROLL", "FINANCE",
    "OPERATIONS", "MAINTENANCE", "RESERVATIONS", "INVENTORY",
    "MEETING", "OTHER",
}


def _resolve_task_category(*, raw: object, title: str, description: str) -> str | None:
    """Return the canonical ``Task.category`` for a Miya-created task.

    The agent may pass an explicit ``category`` (e.g. "FINANCE") which we
    accept verbatim if it's valid. Otherwise we run the deterministic
    intent router on title + description so the task lands in the right
    dashboard widget bucket without the LLM having to know about widget
    ids. Returns ``None`` if neither path produced a known category — the
    caller will leave the column NULL rather than mislabel the task.
    """
    if raw is not None and str(raw).strip():
        cat = str(raw).strip().upper()
        # Aliases for the common cases Miya emits.
        aliases = {
            "INVOICE": "FINANCE", "INVOICES": "FINANCE",
            "BILL": "FINANCE", "BILLS": "FINANCE",
            "TAX": "FINANCE", "TAXES": "FINANCE",
            "ACCOUNTING": "FINANCE", "FINANCES": "FINANCE",
            "MEETINGS": "MEETING", "REMINDER": "MEETING",
            "REMINDERS": "MEETING", "CALENDAR": "MEETING",
            "DOCUMENTS": "DOCUMENT",
            "STOCK": "INVENTORY", "SUPPLIES": "INVENTORY",
            "RESERVATION": "RESERVATIONS", "BOOKING": "RESERVATIONS",
            "BOOKINGS": "RESERVATIONS",
            "REPAIR": "MAINTENANCE", "EQUIPMENT": "MAINTENANCE",
        }
        cat = aliases.get(cat, cat)
        if cat in _VALID_TASK_CATEGORIES:
            return cat

    try:
        from staff.intent_router import classify_request
    except Exception:  # pragma: no cover - defensive
        return None

    try:
        decision = classify_request(subject=title or "", description=description or "")
    except Exception:  # pragma: no cover - defensive
        return None

    cat = (decision.category or "").upper()
    if cat in _VALID_TASK_CATEGORIES:
        return cat
    # Incident classifier returned a SafetyConcernReport sub-category
    # ("Maintenance" / "Safety" / …) — map the obvious ones into our
    # widget buckets so the task still finds a home; otherwise leave
    # the column NULL.
    incident_to_task = {
        "MAINTENANCE": "MAINTENANCE",
        "FOOD SAFETY": "MAINTENANCE",
        "SAFETY": "MAINTENANCE",
        "HR": "HR",
    }
    return incident_to_task.get(cat)


def _norm_name(s: str) -> str:
    """Lowercase, strip diacritics, collapse whitespace."""
    s = (s or "").strip().lower()
    s = unicodedata.normalize("NFKD", s)
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    s = re.sub(r"\s+", " ", s)
    return s


def _strip_titles(s: str) -> str:
    s = (s or "").strip()
    s = re.sub(
        r"^(?:mr\.?|mrs\.?|ms\.?|miss\.?|dr\.?|prof\.?|sir|madam|mx\.?)\s+",
        "",
        s,
        flags=re.IGNORECASE,
    )
    return s.strip()


def _get_first(data: dict, *keys: str) -> Any:
    """Return the first truthy value among the given keys."""
    for k in keys:
        v = data.get(k)
        if v is not None and v != "":
            return v
    return None


def _resolve_assignee(data: dict, restaurant) -> tuple[CustomUser | None, str | None]:
    """
    Find the staff member Miya wants to assign the task to.

    Accepts (in order of preference):
      - user_id / assignee_user_id / assigned_to
      - email / assignee_email
      - phone / assignee_phone / whatsapp
      - name / assignee_name / staff_name (fuzzy match inside the
        restaurant; returns None if ambiguous/no match so the agent
        can ask the user for clarification).

    Returns (user, error_message). Exactly one will be non-None.
    """

    # ---- 1) user_id / assignee_id
    uid = _get_first(
        data,
        "user_id",
        "assignee_id",
        "assigneeId",
        "assignee_user_id",
        "assigned_to",
        "userId",
    )
    if uid:
        if isinstance(uid, dict):
            uid = uid.get("id") or uid.get("user_id")
        try:
            user = CustomUser.objects.filter(
                id=str(uid).strip(),
                restaurant=restaurant,
                is_active=True,
            ).first()
        except Exception:
            user = None
        if user:
            return user, None
        # Fall through to other lookup strategies if id didn't match.

    # ---- 2) email
    email = _get_first(data, "email", "assignee_email")
    if email:
        user = CustomUser.objects.filter(
            email__iexact=str(email).strip(),
            restaurant=restaurant,
            is_active=True,
        ).first()
        if user:
            return user, None

    # ---- 3) phone / whatsapp
    phone = _get_first(data, "phone", "assignee_phone", "whatsapp", "staff_phone")
    if phone:
        phone_digits = "".join(filter(str.isdigit, str(phone)))
        if phone_digits:
            patterns = [phone_digits, f"+{phone_digits}"]
            if len(phone_digits) > 10:
                patterns.extend([phone_digits[-10:], f"+{phone_digits[-10:]}"])
            for p in patterns:
                user = CustomUser.objects.filter(
                    phone__icontains=p,
                    restaurant=restaurant,
                    is_active=True,
                ).first()
                if user:
                    return user, None

    # ---- 4) free-text name
    raw_name = _get_first(data, "name", "assignee_name", "staff_name", "assignee")
    if raw_name and not isinstance(raw_name, dict):
        name = _strip_titles(str(raw_name)) or str(raw_name)
        if name.strip():
            qs = CustomUser.objects.filter(restaurant=restaurant, is_active=True)
            tokens = [t for t in re.split(r"\s+", name) if t]

            # AND across tokens, OR across first/last/email.
            filtered = qs
            for tok in tokens:
                filtered = filtered.filter(
                    Q(first_name__icontains=tok)
                    | Q(last_name__icontains=tok)
                    | Q(email__icontains=tok)
                )

            # If nothing, try matching "First Last" as a single string.
            if not filtered.exists() and name:
                filtered = qs.annotate(
                    full_name=Concat("first_name", Value(" "), "last_name"),
                ).filter(full_name__icontains=name)

            matches = list(filtered[:5])
            if len(matches) == 1:
                return matches[0], None
            if len(matches) > 1:
                # Rank by fuzzy ratio and return the clear winner; else ask
                # the agent to disambiguate.
                q_n = _norm_name(name)
                scored = []
                for u in matches:
                    full = _norm_name(f"{u.first_name or ''} {u.last_name or ''}".strip())
                    score = max(
                        SequenceMatcher(None, q_n, full).ratio(),
                        SequenceMatcher(None, q_n, _norm_name(u.email or "")).ratio(),
                    )
                    scored.append((score, u))
                scored.sort(key=lambda x: x[0], reverse=True)
                best, runner = scored[0], (scored[1] if len(scored) > 1 else (0, None))
                # If the best score is meaningfully better than the runner-up,
                # take it; otherwise report ambiguity so Miya can ask.
                if best[0] >= 0.8 and best[0] - runner[0] >= 0.15:
                    return best[1], None
                candidates = ", ".join(
                    f"{u.first_name} {u.last_name}".strip() or u.email
                    for _, u in scored[:5]
                )
                return None, (
                    f"Multiple staff match '{raw_name}': {candidates}. "
                    "Please specify the user_id, email, or full name."
                )

            # Fuzzy fallback: low-threshold full scan (name might be typoed).
            q_n = _norm_name(name)
            candidates: list[tuple[float, CustomUser]] = []
            for u in qs[:300]:
                full_a = _norm_name(f"{u.first_name or ''} {u.last_name or ''}")
                full_b = _norm_name(f"{u.last_name or ''} {u.first_name or ''}")
                score = max(
                    SequenceMatcher(None, q_n, full_a).ratio(),
                    SequenceMatcher(None, q_n, full_b).ratio(),
                    SequenceMatcher(None, q_n, _norm_name(u.email or "")).ratio(),
                )
                if score >= 0.6:
                    candidates.append((score, u))
            candidates.sort(key=lambda x: x[0], reverse=True)
            if candidates and (
                len(candidates) == 1
                or (
                    candidates[0][0] >= 0.8
                    and (len(candidates) < 2 or candidates[0][0] - candidates[1][0] >= 0.15)
                )
            ):
                return candidates[0][1], None
            if candidates:
                names = ", ".join(
                    f"{u.first_name} {u.last_name}".strip() or u.email
                    for _, u in candidates[:5]
                )
                return None, (
                    f"No exact match for '{raw_name}'. Did you mean: {names}?"
                )

    return None, "Could not identify the staff member. Provide user_id, email, phone, or full name."


def _short_record_ref(record_id) -> str:
    """Human-friendly tail of a UUID for WhatsApp confirmations."""
    digits = str(record_id or "").replace("-", "")
    return (digits[-8:] if len(digits) >= 8 else digits).upper()


def _dashboard_widget_hint(category: str | None) -> str:
    """Tell Miya where the manager should look on the dashboard."""
    from dashboard.category_routing import primary_widget_for_category, widget_lane_label

    widget_id = primary_widget_for_category(category)
    label = widget_lane_label(widget_id)
    return (
        f" Refresh the dashboard — it appears on the {label} widget "
        f"(lane: {widget_id})."
    )


def _parse_due_date(raw: Any) -> tuple[date | None, str | None]:
    """
    Parse `due_date`. Accepts:
      - 'YYYY-MM-DD'
      - 'today', 'tomorrow', 'day after tomorrow'
      - 'in 2 days', 'in 1 week'
      - date / datetime objects
      - None/empty
    Returns (date|None, error|None).
    """
    if raw is None or raw == "":
        return None, None
    if isinstance(raw, datetime):
        return raw.date(), None
    if isinstance(raw, date):
        return raw, None
    s = str(raw).strip().lower()
    if not s:
        return None, None

    today = timezone.now().date()
    if s in ("today", "now"):
        return today, None
    if s == "tomorrow":
        return today + timedelta(days=1), None
    if s in ("day after tomorrow", "the day after tomorrow"):
        return today + timedelta(days=2), None

    m = re.match(r"^in\s+(\d+)\s+(day|days|week|weeks)$", s)
    if m:
        n = int(m.group(1))
        unit = m.group(2)
        days = n * (7 if unit.startswith("week") else 1)
        return today + timedelta(days=days), None

    try:
        return datetime.strptime(s, "%Y-%m-%d").date(), None
    except ValueError:
        pass

    # Last-chance ISO-ish patterns.
    for fmt in ("%Y/%m/%d", "%d/%m/%Y", "%d-%m-%Y"):
        try:
            return datetime.strptime(s, fmt).date(), None
        except ValueError:
            continue

    return None, f"Could not parse due_date '{raw}'. Use YYYY-MM-DD or 'today'/'tomorrow'."


def _coerce_bool(val: Any, default: bool = True) -> bool:
    if val is None:
        return default
    if isinstance(val, bool):
        return val
    s = str(val).strip().lower()
    if s in ("true", "1", "yes", "y", "on"):
        return True
    if s in ("false", "0", "no", "n", "off"):
        return False
    return default


def _format_due(d: date | None) -> str:
    if not d:
        return "no due date"
    today = timezone.now().date()
    delta = (d - today).days
    if delta == 0:
        return "today"
    if delta == 1:
        return "tomorrow"
    if delta == -1:
        return "yesterday"
    if 2 <= delta <= 6:
        return f"in {delta} days ({d.strftime('%a %d %b')})"
    return d.strftime("%a %d %b %Y")


_WHATSAPP_TOKEN_ERROR_HINTS = (
    "access token",
    "access_token",
    "accesstoken",
    "expired token",
    "invalid token",
    "oauth",
    "(#190)",
    "(#102)",
    "(#10)",
    "session has expired",
    "the user is not a confirmed user of the application",
    "permissions error",
    "missing permissions",
    "not authorized",
    "unauthorized",
    "401",
    "403",
)


def _sanitize_whatsapp_error_for_user(raw_error: str | None) -> tuple[str, bool]:
    """
    Take a raw Meta/WhatsApp Cloud API error string and return:
      - user-facing phrase (never leaks "access token", HTTP codes, OAuth, etc.)
      - is_platform_issue boolean (True when this is a tenant-wide outage we should log loudly)

    The agent persona forbids surfacing internal/HTTP/OAuth errors to end users — this is the
    server-side belt to enforce that, in case the model regurgitates `message_for_user`.
    """
    if not raw_error:
        return "", False
    err = str(raw_error).strip()
    err_lower = err.lower()
    if any(hint in err_lower for hint in _WHATSAPP_TOKEN_ERROR_HINTS):
        # Tenant-wide WhatsApp configuration / OAuth problem. The manager can't fix it themselves;
        # the platform team has to rotate the token / reconnect the WABA.
        return (
            "I couldn't reach them on WhatsApp right now — the task is in their inbox and they'll "
            "see the bell notification. Our team is looking at the WhatsApp connection."
        ), True
    # Per-recipient problems (e.g. recipient phone not on WhatsApp, throttling) — keep concise but
    # avoid raw provider strings.
    if "phone" in err_lower and ("invalid" in err_lower or "not a whatsapp" in err_lower or "not registered" in err_lower):
        return (
            "I couldn't reach them on WhatsApp — looks like their phone number isn't a WhatsApp account. "
            "The task is in their inbox."
        ), False
    if "rate" in err_lower and "limit" in err_lower:
        return (
            "WhatsApp is rate-limiting us right now — the task is in their inbox and I'll retry "
            "automatically in a few minutes."
        ), False
    if "template" in err_lower and ("not approved" in err_lower or "not_found" in err_lower):
        return (
            "The WhatsApp message template isn't ready for this case. The task is in their inbox."
        ), False
    # Catch-all: don't leak the upstream string.
    return (
        "I couldn't reach them on WhatsApp this time — the task is in their inbox and they'll see "
        "the bell notification."
    ), False


def _build_whatsapp_body(
    task: Task,
    sender_name: str,
    assignee_first_name: str,
    override: str | None,
) -> str:
    """Human-friendly WhatsApp body. `override` wins if provided."""
    if override and str(override).strip():
        return str(override).strip()

    hello = f"Hi {assignee_first_name}," if assignee_first_name else "Hi,"
    pretty_priority = {
        "URGENT": "URGENT priority",
        "HIGH": "high priority",
        "MEDIUM": "medium priority",
        "LOW": "low priority",
    }.get(task.priority, "")
    lines = [
        f"{hello}",
        "",
        f"New task from {sender_name}: *{task.title}*",
    ]
    if task.description:
        lines.append(f"{task.description}")
    meta_bits = []
    if pretty_priority:
        meta_bits.append(pretty_priority)
    meta_bits.append(f"due {_format_due(task.due_date)}")
    lines.append("")
    lines.append(f"({'; '.join(meta_bits)})")
    lines.append("")
    lines.append("Reply *accept*, *start*, *done*, or *unable* (add #id if you have several).")
    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Main endpoint
# ---------------------------------------------------------------------------


@api_view(["POST"])
@authentication_classes([])  # Bypass JWT auth; we validate manually below.
@permission_classes([permissions.AllowAny])
def agent_create_dashboard_task(request):
    """
    POST /api/dashboard/agent/tasks/create/

    Create a dashboard.Task for a staff member and (optionally) send a
    WhatsApp notification in the same call. Used by Miya when a manager
    says e.g. "Create a task for Ahmed to clean the fryer by tomorrow
    and let him know."

    Auth: `Authorization: Bearer <MIYA_MASTRA_API_KEY>` OR a user JWT
    (same convention as every other agent endpoint under /api/.../agent/).

    Body (all fields accept camelCase or snake_case):
        title              str   (required)
        description        str   (optional)
        priority           str   LOW | MEDIUM | HIGH | URGENT  (default MEDIUM)
        due_date           str   'YYYY-MM-DD' | 'today' | 'tomorrow' | 'in 3 days'
        ai_summary         str   short summary Miya wants shown in green on the card
        restaurant_id      str   (optional; else resolved from header/JWT/session)
        notify_whatsapp    bool  (default true)
        whatsapp_message   str   (optional override for the body sent to staff)

        # Assignee — pass ONE of:
        user_id | assignee_user_id | assigned_to
        email   | assignee_email
        phone   | assignee_phone  | whatsapp | staff_phone
        name    | assignee_name   | staff_name | assignee   # fuzzy match

    Response (201):
        {
          "success": true,
          "task": { DashboardTaskCompactSerializer shape },
          "assignee": {
            "id": "...", "name": "...", "phone": "...", "role": "..."
          },
          "whatsapp": {
            "sent": true | false,
            "skipped_reason": null | "no_phone" | "disabled",
            "error": null | "...",
            "provider_status": 200
          },
          "message_for_user": "Created 'Clean the fryer' for Ahmed (high priority, due tomorrow). WhatsApp notification sent."
        }

    Errors:
        400 invalid payload / can't parse due_date
        401 bad auth
        404 restaurant/assignee not found or ambiguous
        500 unexpected
    """
    # Lazy imports: avoid a circular import (scheduling.views_agent → dashboard).
    from scheduling.views_agent import (
        _resolve_restaurant_for_agent,
        _try_jwt_restaurant_and_user,
    )
    from notifications.services import notification_service

    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response(
                {"success": False, "error": err["error"]},
                status=err["status"],
            )

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}

        title = str(_get_first(data, "title", "task_title") or "").strip()
        if not title:
            return Response(
                {
                    "success": False,
                    "error": "Missing required field: title",
                    "message_for_user": "I need a task title before I can create it.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        title = title[:255]

        description = (str(_get_first(data, "description") or "")).strip()

        priority = str(_get_first(data, "priority") or "MEDIUM").upper().strip()
        if priority not in _VALID_PRIORITIES:
            priority = "MEDIUM"

        due_date, due_err = _parse_due_date(_get_first(data, "due_date", "dueDate", "due"))
        if due_err:
            return Response(
                {
                    "success": False,
                    "error": due_err,
                    "message_for_user": due_err,
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        ai_summary = str(_get_first(data, "ai_summary", "aiSummary", "summary") or "").strip()

        # Acting user (for assign_to_self + audit trail). May already be set
        # by _resolve_restaurant_for_agent via JWT/session; if not, try JWT.
        if not acting_user:
            try:
                _, acting_user = _try_jwt_restaurant_and_user(request)
            except Exception:
                acting_user = None

        # Requester (From column) = WhatsApp/dashboard sender who asked Miya.
        # Distinct from assignee (To). Prefer dedicated requester fields so
        # session user_id is never confused with the person being assigned.
        requester = acting_user
        if not requester:
            req_id = _get_first(
                data,
                "requester_id",
                "created_by_id",
                "sender_user_id",
                "senderUserId",
                "acting_user_id",
                "actingUserId",
                "user_id",
                "userId",
            )
            req_phone = _get_first(
                data,
                "requester_phone",
                "sender_phone",
                "senderPhone",
                "reporter_phone",
                "phone",
            )
            req_email = _get_first(
                data,
                "requester_email",
                "sender_email",
                "senderEmail",
                "acting_user_email",
            )
            if req_id or req_phone or req_email:
                requester, _ = _resolve_assignee(
                    {
                        "user_id": req_id,
                        "phone": req_phone,
                        "email": req_email,
                    },
                    restaurant,
                )
        if requester and not acting_user:
            acting_user = requester

        assign_to_self = _coerce_bool(
            _get_first(
                data,
                "assign_to_self",
                "assignToSelf",
                "assign_to_sender",
                "personal_reminder",
            ),
            default=False,
        )

        # Resolve assignee (To).
        assignee = None
        assignee_err = None
        if assign_to_self:
            if requester and getattr(requester, "restaurant_id", None) == getattr(
                restaurant, "id", None
            ):
                assignee = requester
            else:
                assignee_err = (
                    "I couldn't identify you as a workspace member for this "
                    "personal reminder. Open Miya from your Mizan dashboard while logged in, "
                    "or message from your registered WhatsApp number."
                )
        else:
            # Session user_id is the requester. Strip it when an explicit
            # assignee is provided so From/To don't collapse to the same person.
            assignee_data = dict(data)
            explicit_assignee = _get_first(
                data,
                "assignee_id",
                "assigneeId",
                "assignee_user_id",
                "staff_name",
                "staffName",
                "assignee_name",
                "assigneeName",
                "name",
                "assignee_phone",
                "assigneePhone",
            )
            assign_to_category_hint = str(
                _get_first(
                    data,
                    "assign_to_category",
                    "assign_to_category_owner",
                    "delegate_category",
                    "category_owner",
                )
                or ""
            ).strip()
            if explicit_assignee or assign_to_category_hint:
                for k in ("user_id", "userId"):
                    assignee_data.pop(k, None)
            assignee, assignee_err = _resolve_assignee(assignee_data, restaurant)

        # Manager delegation: "tell HR / payroll to …" without naming a person.
        assign_to_category = str(
            _get_first(
                data,
                "assign_to_category",
                "assign_to_category_owner",
                "delegate_category",
                "category_owner",
            )
            or ""
        ).strip().upper()
        category_routing_result = None
        if (assignee_err or not assignee) and assign_to_category:
            from staff.category_routing_engine import resolve_routing_for_staff_category

            routing = resolve_routing_for_staff_category(restaurant, assign_to_category)
            assignee = routing.primary
            category_routing_result = routing
            if assignee:
                assignee_err = None
            elif requester and getattr(requester, "restaurant_id", None) == getattr(
                restaurant, "id", None
            ):
                # Still create a trackable task when HR/payroll owners aren't
                # configured - assign to the person who asked so the widget
                # isn't empty and they can reassign in the task detail pane.
                assignee = requester
                assignee_err = None
                owner_hint = (
                    f"No {assign_to_category} owner is configured in Settings → "
                    "Who owns what? - assigned to you for now."
                )
                ai_summary = (ai_summary + " · " + owner_hint).strip(" ·") if ai_summary else owner_hint
            else:
                from accounts.models import CustomUser as AssigneeUser

                assignee = (
                    AssigneeUser.objects.filter(
                        restaurant=restaurant,
                        is_active=True,
                        role__in=("OWNER", "ADMIN", "MANAGER"),
                    )
                    .exclude(role="SUPER_ADMIN")
                    .order_by(
                        Case(
                            When(role="OWNER", then=Value(0)),
                            When(role="ADMIN", then=Value(1)),
                            When(role="MANAGER", then=Value(2)),
                            default=Value(3),
                            output_field=IntegerField(),
                        )
                    )
                    .first()
                )
                if assignee:
                    assignee_err = None
                    owner_hint = (
                        f"No {assign_to_category} owner is configured — assigned to "
                        f"{assignee.get_full_name() or assignee.email} for now."
                    )
                    ai_summary = (ai_summary + " · " + owner_hint).strip(" ·") if ai_summary else owner_hint
                else:
                    assignee_err = (
                        f"No one is configured as the owner for {assign_to_category}. "
                        "Add category owners in Settings → General → Who owns what?"
                    )

        if assignee_err or not assignee:
            return Response(
                {
                    "success": False,
                    "error": assignee_err or "Assignee not found",
                    "message_for_user": assignee_err
                    or "I couldn't find that staff member in this workspace.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if acting_user:
            from accounts.rbac_enforce import miya_has_full_tenant_access, user_can_action

            if not miya_has_full_tenant_access(acting_user, restaurant) and not user_can_action(
                acting_user,
                "manage_widgets",
                restaurant=restaurant,
            ):
                if assignee.id != acting_user.id:
                    return Response(
                        {
                            "success": False,
                            "error": "Staff may only create dashboard tasks assigned to themselves.",
                            "message_for_user": (
                                "I can add that to your dashboard task list for you — "
                                "ask your manager if you need it assigned to someone else."
                            ),
                        },
                        status=status.HTTP_403_FORBIDDEN,
                    )

        # From label: WhatsApp/dashboard sender name (never bare "Miya").
        channel = str(_get_first(data, "channel") or "").strip().lower()
        channel_prefix = "WhatsApp" if channel == "whatsapp" else "Miya AI"
        sender_display = ""
        if requester:
            nm = f"{(requester.first_name or '').strip()} {(requester.last_name or '').strip()}".strip()
            sender_display = nm or getattr(requester, "email", None) or ""
        source_label = (
            f"{channel_prefix} · {sender_display}" if sender_display else channel_prefix
        )

        # Pick the dashboard widget bucket (HR / FINANCE / MAINTENANCE /
        # MEETING / …) so this task shows up in the right widget without
        # the manager having to file it manually. ``_resolve_task_category``
        # honours an explicit agent-supplied category first and only then
        # falls back to the keyword-based intent router.
        task_category = _resolve_task_category(
            raw=_get_first(data, "category", "bucket", "widget") or assign_to_category or None,
            title=title,
            description=description,
        )

        from dashboard.custom_widget_routing import (
            custom_widget_hint,
            match_custom_widget_for_task,
        )

        if not acting_user:
            from dashboard.views_widget_layout import _resolve_user_from_agent_payload

            acting_user = _resolve_user_from_agent_payload(data, request)

        matched_custom_widget = match_custom_widget_for_task(
            user=acting_user,
            restaurant=restaurant,
            title=title,
            description=description,
            source_text=str(
                _get_first(
                    data,
                    "source_text",
                    "sourceText",
                    "context",
                    "conversation",
                    "user_message",
                )
                or ""
            ),
            explicit_id=_get_first(
                data,
                "custom_widget_id",
                "customWidgetId",
                "widget_id",
                "widgetId",
            ),
        )

        follow_up_enabled = _coerce_bool(
            _get_first(data, "follow_up_enabled", "followUpEnabled"),
            default=False if assign_to_self else True,
        )
        follow_up_max = int(_get_first(data, "follow_up_max", "followUpMax") or 2)
        follow_up_max = max(0, min(3, follow_up_max))
        follow_up_first_hours_raw = _get_first(
            data,
            "follow_up_first_hours",
            "followUpFirstHours",
            "reminder_hours",
            "reminderHours",
        )
        follow_up_first_hours = None
        if follow_up_first_hours_raw not in (None, "", False):
            try:
                follow_up_first_hours = max(1, min(20, int(follow_up_first_hours_raw)))
            except (TypeError, ValueError):
                follow_up_first_hours = None
        requires_manager_validation = _coerce_bool(
            _get_first(
                data,
                "requires_manager_validation",
                "requiresManagerValidation",
                "manager_validation",
            ),
            default=False,
        )
        require_photo_proof = _coerce_bool(
            _get_first(data, "require_photo_proof", "requirePhotoProof", "photo_proof"),
            default=False,
        )

        # Create the task atomically.
        routing_meta: dict[str, Any] = {}
        if category_routing_result and category_routing_result.primary:
            routing_meta = {
                "category": assign_to_category,
                "strategy": category_routing_result.strategy,
                "slug": category_routing_result.slug,
                "informed_assignee_ids": [
                    str(u.id) for u in (category_routing_result.informed or [])
                ],
                "owner_ids": [str(u.id) for u in (category_routing_result.owners or [])],
            }

        with transaction.atomic():
            task = Task.objects.create(
                restaurant=restaurant,
                assigned_to=assignee,
                # Requester (From) ≠ assignee (To). Staff WhatsApp → created_by=staff.
                created_by=requester if getattr(requester, "pk", None) else None,
                title=title,
                description=description or None,
                priority=priority,
                status="PENDING",
                due_date=due_date,
                source="WHATSAPP" if channel == "whatsapp" else "MIYA",
                source_label=source_label[:120],
                ai_summary=ai_summary,
                category=task_category,
                custom_widget=matched_custom_widget,
                follow_up_enabled=follow_up_enabled,
                follow_up_max=follow_up_max,
                follow_up_first_hours=follow_up_first_hours,
                requires_manager_validation=requires_manager_validation,
                require_photo_proof=require_photo_proof,
                routing_metadata=routing_meta,
            )
            if assignee:
                task.assignees.add(assignee)
                if not routing_meta.get("assignee_ids"):
                    task.routing_metadata = {
                        **routing_meta,
                        "assignee_ids": [str(assignee.id)],
                    }
                    task.save(update_fields=["routing_metadata"])

        logger.info(
            "Miya created Task %s (%r) for user %s in restaurant %s",
            task.id, title, assignee.id, restaurant.id,
        )

        try:
            from dashboard.task_sync import broadcast_tasks_invalidate

            broadcast_tasks_invalidate(restaurant, reason="task_created", task_id=str(task.id))
        except Exception:
            pass

        # In-app + WhatsApp notifications (primary + informed category owners).
        from dashboard.task_assign_notify import notify_task_assignment

        notify_whatsapp = _coerce_bool(
            _get_first(data, "notify_whatsapp", "notifyWhatsapp", "send_whatsapp"),
            default=False if assign_to_self else True,
        )
        wa_override = _get_first(data, "whatsapp_message", "whatsappMessage", "message")
        informed_owners = (
            list(category_routing_result.informed)
            if category_routing_result and category_routing_result.informed
            else []
        )
        notify_result = notify_task_assignment(
            task,
            assignee=assignee,
            sender=acting_user or requester,
            sender_display=sender_display,
            informed_owners=informed_owners,
            notify_whatsapp=notify_whatsapp,
            whatsapp_override=wa_override if isinstance(wa_override, str) else None,
        )
        wa_result: dict[str, Any] = dict(notify_result.get("primary_whatsapp") or {})
        wa_result.setdefault("sent", False)
        wa_result.setdefault("skipped_reason", None)
        wa_result.setdefault("error", None)
        wa_result["informed_notified"] = notify_result.get("informed_notified") or []

        if wa_result.get("error"):
            _, is_platform_issue = _sanitize_whatsapp_error_for_user(wa_result["error"])
            wa_result["raw_error"] = wa_result["error"]
            wa_result["is_platform_issue"] = is_platform_issue
            if is_platform_issue:
                logger.error(
                    "WhatsApp Cloud API token/auth issue detected (task=%s assignee=%s): %s",
                    task.id,
                    assignee.id,
                    wa_result["error"],
                )

        # Build the human-facing confirmation string.
        pretty_priority = priority.lower() if priority != "URGENT" else "URGENT"
        due_phrase = _format_due(task.due_date)
        assignee_display = (
            f"{(assignee.first_name or '').strip()} {(assignee.last_name or '').strip()}".strip()
            or assignee.email
        )
        task_ref = _short_record_ref(task.id)
        if matched_custom_widget:
            widget_hint = custom_widget_hint(matched_custom_widget)
        else:
            widget_hint = _dashboard_widget_hint(task_category)
        message_for_user = (
            f"Task #{task_ref} — Created '{task.title}' for {assignee_display} "
            f"({pretty_priority} priority, due {due_phrase}).{widget_hint}"
        ).strip()

        if matched_custom_widget:
            dashboard_widget = matched_custom_widget.slot_id()
        elif (task_category or "").upper() == "OPERATIONS":
            dashboard_widget = "operations_tasks"
        else:
            dashboard_widget = "tasks_demands"

        # URGENT / CRITICAL → ping managers so Operations Live pressing items
        # don't sit unnoticed. Best-effort; never blocks create.
        manager_alert: dict[str, Any] | None = None
        notify_managers = _coerce_bool(
            _get_first(
                data,
                "notify_managers",
                "notifyManagers",
                "alert_managers",
                "alertManagers",
            ),
            default=priority == "URGENT",
        )
        if notify_managers and priority == "URGENT":
            try:
                from dashboard.api.operations_live import notify_managers_urgent

                manager_alert = notify_managers_urgent(
                    restaurant,
                    message=(
                        f"⚠️ *Urgent on Operations Live*\n"
                        f"*{task.title}* ({task_category or 'OPS'}) — "
                        f"assigned to {assignee_display}.\n"
                        f"Open Operations Live to triage."
                    ),
                    task_id=str(task.id),
                )
                if manager_alert.get("managers_app") or manager_alert.get(
                    "managers_whatsapp"
                ):
                    message_for_user = (
                        f"{message_for_user} Managers were alerted."
                    ).strip()
            except Exception:
                logger.exception(
                    "Miya create_task: manager urgent alert failed for task %s",
                    task.id,
                )

        return Response(
            {
                "success": True,
                "task": DashboardTaskCompactSerializer(task).data,
                "record_id": str(task.id),
                "task_ref": task_ref,
                "dashboard_widget": dashboard_widget,
                "custom_widget_id": (
                    str(matched_custom_widget.id) if matched_custom_widget else None
                ),
                "custom_widget_title": (
                    matched_custom_widget.title if matched_custom_widget else None
                ),
                "assignee": {
                    "id": str(assignee.id),
                    "name": assignee_display,
                    "phone": assignee.phone or "",
                    "role": getattr(assignee, "role", None),
                },
                "whatsapp": wa_result,
                "manager_alert": manager_alert,
                "message_for_user": message_for_user,
            },
            status=status.HTTP_201_CREATED,
        )

    except Exception as exc:  # pragma: no cover - defensive
        logger.exception("agent_create_dashboard_task crashed")
        return Response(
            {
                "success": False,
                "error": str(exc)[:200],
                "message_for_user": "Something went wrong while creating that task. Please try again.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


# ---------------------------------------------------------------------------
# Reassign + status update (Miya WhatsApp parity)
# ---------------------------------------------------------------------------


_VALID_TASK_STATUSES = {
    "PENDING",
    "ACCEPTED",
    "IN_PROGRESS",
    "COMPLETED",
    "UNABLE_TO_COMPLETE",
    "CANCELLED",
}


def _load_dashboard_task_for_agent(data: dict, restaurant) -> tuple[Task | None, str | None]:
    """Resolve a dashboard.Task by UUID, short ref, or title/query text."""
    task_id = str(
        _get_first(data, "task_id", "taskId", "id", "record_id", "recordId", "task_ref") or ""
    ).strip().lstrip("#")
    title_q = str(
        _get_first(data, "title", "q", "query", "task_title", "name", "task_name") or ""
    ).strip()

    if task_id:
        try:
            task = Task.objects.select_related("assigned_to").get(
                pk=task_id, restaurant=restaurant
            )
            return task, None
        except (Task.DoesNotExist, ValueError, TypeError):
            pass

        needle = task_id.replace("-", "").upper()
        if len(needle) >= 6:
            for candidate in Task.objects.filter(restaurant=restaurant).select_related(
                "assigned_to"
            )[:300]:
                ref = _short_record_ref(candidate.id)
                full = str(candidate.id).replace("-", "").upper()
                if ref == needle or full.endswith(needle) or needle.endswith(ref):
                    return candidate, None

        # Managers often pass the task title in task_id ("Payer Dj Zia").
        if len(task_id) >= 3 and not re.fullmatch(r"[0-9a-fA-F-]{8,}", task_id):
            title_q = title_q or task_id

    if title_q:
        qs = (
            Task.objects.filter(restaurant=restaurant)
            .select_related("assigned_to")
            .filter(Q(title__icontains=title_q) | Q(description__icontains=title_q))
            .exclude(status="CANCELLED")
            .order_by("-updated_at")
        )
        # Prefer open/recent rows when several match.
        open_first = list(
            qs.filter(
                status__in=(
                    "PENDING",
                    "ACCEPTED",
                    "IN_PROGRESS",
                    "UNABLE_TO_COMPLETE",
                    "COMPLETED",
                )
            )[:8]
        )
        matches = open_first or list(qs[:8])
        if len(matches) == 1:
            return matches[0], None
        if len(matches) > 1:
            # Prefer exact-ish title match.
            exact = [
                t
                for t in matches
                if title_q.lower() in (t.title or "").lower()
            ]
            if len(exact) == 1:
                return exact[0], None
            names = ", ".join(
                f"#{_short_record_ref(t.id)} {t.title} ({t.status})" for t in matches[:5]
            )
            return None, (
                f"Several tasks match '{title_q}': {names}. "
                "Tell me which task_ref to use."
            )
        return None, f"I couldn't find a task matching '{title_q}'."

    return None, "Missing required field: task_id (or title)"


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_reassign_dashboard_task(request):
    """
    POST /api/dashboard/agent/tasks/reassign/

    Reassign a ``dashboard.Task`` to another staff member. Used by Miya when
    a manager says e.g. "Reassign the kitchen cleaning task to Ahmed."
    """
    from scheduling.views_agent import (
        _resolve_restaurant_for_agent,
        _try_jwt_restaurant_and_user,
    )
    from notifications.services import notification_service

    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response(
                {"success": False, "error": err["error"]},
                status=err["status"],
            )

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        task, task_err = _load_dashboard_task_for_agent(data, restaurant)
        if task_err or not task:
            return Response(
                {
                    "success": False,
                    "error": task_err or "Task not found",
                    "message_for_user": task_err or "I couldn't find that task.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        assignee, assignee_err = _resolve_assignee(data, restaurant)
        if assignee_err or not assignee:
            return Response(
                {
                    "success": False,
                    "error": assignee_err or "Assignee not found",
                    "message_for_user": assignee_err
                    or "I couldn't find that staff member in this workspace.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if not acting_user:
            try:
                _, acting_user = _try_jwt_restaurant_and_user(request)
            except Exception:
                acting_user = None

        old = task.assigned_to
        note = str(_get_first(data, "note", "reason") or "").strip()
        task.assigned_to = assignee
        task.save(update_fields=["assigned_to", "updated_at"])

        try:
            from dashboard.task_sync import broadcast_tasks_invalidate

            broadcast_tasks_invalidate(restaurant, reason="task_reassigned", task_id=str(task.id))
        except Exception:
            pass

        assignee_display = (
            f"{(assignee.first_name or '').strip()} {(assignee.last_name or '').strip()}".strip()
            or assignee.email
        )
        old_display = ""
        if old:
            old_display = (
                f"{(old.first_name or '').strip()} {(old.last_name or '').strip()}".strip()
                or old.email
            )

        # In-app + WhatsApp notify new assignee (best-effort).
        from dashboard.task_assign_notify import notify_task_assignment

        notify_whatsapp = _coerce_bool(
            _get_first(data, "notify_whatsapp", "notifyWhatsapp", "send_whatsapp"),
            default=True,
        )
        wa_override = _get_first(data, "whatsappMessage", "whatsapp_message", "message")
        sender_display = "Your manager"
        if acting_user:
            nm = f"{(acting_user.first_name or '').strip()} {(acting_user.last_name or '').strip()}".strip()
            sender_display = nm or getattr(acting_user, "email", None) or sender_display
        if note and not (isinstance(wa_override, str) and wa_override.strip()):
            wa_override = (
                f"Hi {(assignee.first_name or '').strip() or 'there'},\n"
                f"{sender_display} reassigned a task to you:\n"
                f"*{task.title}*\n"
                + (f"Note: {note}\n" if note else "")
                + "Reply *accept*, *start*, *done*, or *unable*."
            )
        notify_result = notify_task_assignment(
            task,
            assignee=assignee,
            sender=acting_user,
            sender_display=sender_display,
            informed_owners=[],
            notify_whatsapp=notify_whatsapp,
            whatsapp_override=wa_override if isinstance(wa_override, str) else None,
            is_reassignment=True,
        )
        wa_result = dict(notify_result.get("primary_whatsapp") or {})
        wa_result.setdefault("sent", False)
        wa_result.setdefault("skipped_reason", None)
        wa_result.setdefault("error", None)

        task_ref = _short_record_ref(task.id)
        if old_display and old and str(old.id) != str(assignee.id):
            message_for_user = (
                f"Task #{task_ref} — Reassigned '{task.title}' "
                f"from {old_display} to {assignee_display}."
            )
        else:
            message_for_user = (
                f"Task #{task_ref} — Assigned '{task.title}' to {assignee_display}."
            )

        return Response(
            {
                "success": True,
                "task": DashboardTaskCompactSerializer(task).data,
                "record_id": str(task.id),
                "task_ref": task_ref,
                "assignee": {
                    "id": str(assignee.id),
                    "name": assignee_display,
                    "phone": assignee.phone or "",
                    "role": getattr(assignee, "role", None),
                },
                "whatsapp": wa_result,
                "message_for_user": message_for_user,
            }
        )
    except Exception as exc:
        logger.exception("agent_reassign_dashboard_task crashed")
        return Response(
            {
                "success": False,
                "error": str(exc)[:200],
                "message_for_user": "Something went wrong while reassigning that task.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_update_dashboard_task_status(request):
    """
    POST /api/dashboard/agent/tasks/status/

    Update ``dashboard.Task.status`` from Miya / WhatsApp.
    Valid statuses: PENDING | IN_PROGRESS | COMPLETED | CANCELLED.
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    try:
        restaurant, acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response(
                {"success": False, "error": err["error"]},
                status=err["status"],
            )

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        task, task_err = _load_dashboard_task_for_agent(data, restaurant)
        if task_err or not task:
            return Response(
                {
                    "success": False,
                    "error": task_err or "Task not found",
                    "message_for_user": task_err or "I couldn't find that task.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        raw_status = str(
            _get_first(data, "status", "new_status", "task_status") or ""
        ).upper().strip()
        # Soft aliases for natural language from Miya.
        aliases = {
            "DONE": "COMPLETED",
            "COMPLETE": "COMPLETED",
            "FINISHED": "COMPLETED",
            "STARTED": "IN_PROGRESS",
            "START": "IN_PROGRESS",
            "REJECTED": "CANCELLED",
            "UNABLE": "UNABLE_TO_COMPLETE",
            "CANT": "UNABLE_TO_COMPLETE",
            "CANNOT": "UNABLE_TO_COMPLETE",
            "CANCEL": "CANCELLED",
            "REMOVE": "CANCELLED",
            "DELETE": "CANCELLED",
            "ENLEVER": "CANCELLED",
            "SUPPRIMER": "CANCELLED",
        }
        new_status = aliases.get(raw_status, raw_status)
        if new_status not in _VALID_TASK_STATUSES:
            return Response(
                {
                    "success": False,
                    "error": f"Invalid status '{raw_status}'",
                    "message_for_user": (
                        "Status must be Pending, Accepted, In Progress, "
                        "Completed, Unable to Complete, or Cancelled."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        old_status = task.status
        task.status = new_status
        update_fields = ["status", "updated_at"]
        if new_status == "COMPLETED":
            task.completed_at = timezone.now()
            update_fields.append("completed_at")
            if acting_user:
                task.completed_by = acting_user
                update_fields.append("completed_by")
        task.save(update_fields=update_fields)

        try:
            from dashboard.task_sync import broadcast_tasks_invalidate

            broadcast_tasks_invalidate(restaurant, reason="task_status", task_id=str(task.id))
        except Exception:
            pass

        # Notify managers when a task is completed or blocked (best-effort).
        if new_status in ("COMPLETED", "UNABLE_TO_COMPLETE") and old_status != new_status:
            try:
                from notifications.services import notification_service
                from accounts.models import CustomUser as CU

                managers = CU.objects.filter(
                    restaurant=restaurant,
                    is_active=True,
                    role__in=("SUPER_ADMIN", "OWNER", "ADMIN", "MANAGER"),
                ).exclude(pk=getattr(acting_user, "id", None))[:8]
                actor = ""
                if acting_user:
                    actor = (
                        f"{(acting_user.first_name or '').strip()} "
                        f"{(acting_user.last_name or '').strip()}"
                    ).strip() or acting_user.email
                if new_status == "UNABLE_TO_COMPLETE":
                    title = "Task unable to complete"
                    msg = f"{actor or 'Staff'} cannot complete: {task.title}"
                    ntype = "TASK_ASSIGNED"
                else:
                    title = "Task completed"
                    msg = f"{actor or 'Staff'} completed: {task.title}"
                    ntype = "TASK_COMPLETED"
                for mgr in managers:
                    notification_service.send_custom_notification(
                        recipient=mgr,
                        message=msg,
                        title=title,
                        notification_type=ntype,
                        channels=["app", "push"],
                        sender=acting_user,
                    )
            except Exception:
                logger.exception(
                    "Miya task status: manager notify failed for task %s", task.id
                )

        task_ref = _short_record_ref(task.id)
        return Response(
            {
                "success": True,
                "task": DashboardTaskCompactSerializer(task).data,
                "record_id": str(task.id),
                "task_ref": task_ref,
                "old_status": old_status,
                "status": new_status,
                "message_for_user": (
                    f"Task #{task_ref} — Status updated to "
                    f"{new_status.replace('_', ' ').title()}."
                ),
            }
        )
    except Exception as exc:
        logger.exception("agent_update_dashboard_task_status crashed")
        return Response(
            {
                "success": False,
                "error": str(exc)[:200],
                "message_for_user": "Something went wrong while updating that task.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_update_dashboard_task(request):
    """
    POST /api/dashboard/agent/tasks/update/

    Update priority, due_date, title, description, or require_photo_proof on a
    dashboard.Task (Miya / WhatsApp after create).
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    try:
        restaurant, _acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response(
                {"success": False, "error": err["error"]},
                status=err["status"],
            )

        data = request.data if isinstance(getattr(request, "data", None), dict) else {}
        task, task_err = _load_dashboard_task_for_agent(data, restaurant)
        if task_err or not task:
            return Response(
                {
                    "success": False,
                    "error": task_err or "Task not found",
                    "message_for_user": task_err or "I couldn't find that task.",
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        update_fields = ["updated_at"]
        changed = []

        raw_priority = _get_first(data, "priority")
        if raw_priority is not None and str(raw_priority).strip():
            p = str(raw_priority).upper().strip()
            aliases = {"NORMAL": "MEDIUM", "MED": "MEDIUM", "CRITICAL": "URGENT"}
            p = aliases.get(p, p)
            if p not in ("LOW", "MEDIUM", "HIGH", "URGENT"):
                return Response(
                    {
                        "success": False,
                        "error": f"Invalid priority '{raw_priority}'",
                        "message_for_user": "Priority must be Low, Medium, High, or Urgent.",
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            task.priority = p
            update_fields.append("priority")
            changed.append(f"priority={p}")

        if any(k in data for k in ("due_date", "dueDate", "due", "deadline")):
            due_date, due_err = _parse_due_date(
                _get_first(data, "due_date", "dueDate", "due", "deadline")
            )
            if due_err:
                return Response(
                    {
                        "success": False,
                        "error": due_err,
                        "message_for_user": due_err,
                    },
                    status=status.HTTP_400_BAD_REQUEST,
                )
            task.due_date = due_date
            update_fields.append("due_date")
            changed.append(f"due={due_date}" if due_date else "due=cleared")

        title = _get_first(data, "title")
        if title is not None and str(title).strip():
            task.title = str(title).strip()[:200]
            update_fields.append("title")
            changed.append("title")

        description = _get_first(data, "description", "body", "notes")
        if description is not None:
            task.description = str(description).strip()[:5000]
            update_fields.append("description")
            changed.append("description")

        if any(k in data for k in ("require_photo_proof", "requirePhotoProof", "photo_proof")):
            task.require_photo_proof = _coerce_bool(
                _get_first(data, "require_photo_proof", "requirePhotoProof", "photo_proof"),
                default=task.require_photo_proof,
            )
            update_fields.append("require_photo_proof")
            changed.append(f"photo_proof={task.require_photo_proof}")

        if len(changed) == 0:
            return Response(
                {
                    "success": False,
                    "error": "No updatable fields provided",
                    "message_for_user": "Tell me what to change — priority, due date, or title.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        task.save(update_fields=list(dict.fromkeys(update_fields)))
        task_ref = _short_record_ref(task.id)
        return Response(
            {
                "success": True,
                "task": DashboardTaskCompactSerializer(task).data,
                "record_id": str(task.id),
                "task_ref": task_ref,
                "changed": changed,
                "message_for_user": f"Task #{task_ref} updated ({', '.join(changed)}).",
            }
        )
    except Exception as exc:
        logger.exception("agent_update_dashboard_task crashed")
        return Response(
            {
                "success": False,
                "error": str(exc)[:200],
                "message_for_user": "Something went wrong while updating that task.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )


@api_view(["GET", "POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_list_dashboard_tasks(request):
    """
    GET|POST /api/dashboard/agent/tasks/list/

    List dashboard tasks. Supports overdue=true, status, assignee filters.
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    try:
        restaurant, _acting_user, err = _resolve_restaurant_for_agent(request)
        if err:
            return Response(
                {"success": False, "error": err["error"]},
                status=err["status"],
            )

        data = {}
        if request.method == "GET":
            data = {k: v for k, v in request.query_params.items()}
        elif isinstance(getattr(request, "data", None), dict):
            data = request.data

        from dashboard.models import Task

        qs = Task.objects.filter(restaurant=restaurant).select_related(
            "assigned_to", "completed_by", "proof_submitted_by"
        )

        # Default: open board (matches Operations Live "new + in progress").
        # Pass status=ALL / COMPLETED / CANCELLED when the manager wants history.
        overdue = _coerce_bool(_get_first(data, "overdue", "is_overdue"), default=False)
        if overdue:
            today = timezone.localdate()
            qs = qs.filter(due_date__lt=today).exclude(
                status__in=("COMPLETED", "CANCELLED")
            )

        raw_status = _get_first(data, "status", "statuses")
        _OPEN_STATUSES = (
            "PENDING",
            "ACCEPTED",
            "IN_PROGRESS",
            "UNABLE_TO_COMPLETE",
        )
        if raw_status:
            statuses: list[str] = []
            for s in str(raw_status).replace(";", ",").split(","):
                s = s.strip().upper()
                if not s:
                    continue
                if s in ("OPEN", "PENDING_LANE", "EN_ATTENTE", "ACTIVE"):
                    statuses.extend(_OPEN_STATUSES)
                elif s in ("ALL", "*"):
                    statuses = []
                    break
                else:
                    aliases = {
                        "DONE": "COMPLETED",
                        "COMPLETE": "COMPLETED",
                        "STARTED": "IN_PROGRESS",
                        "START": "IN_PROGRESS",
                        "CANCEL": "CANCELLED",
                        "NEW": "PENDING",
                    }
                    statuses.append(aliases.get(s, s))
            if statuses:
                # Deduplicate while preserving order
                statuses = list(dict.fromkeys(statuses))
                qs = qs.filter(status__in=statuses)
        elif not overdue and not _get_first(data, "task_id", "taskId", "id", "task_ref"):
            qs = qs.filter(status__in=_OPEN_STATUSES)

        # Optional text search — "photos pour maxime", "Dj Zia", etc.
        q = str(_get_first(data, "q", "query", "search", "title") or "").strip()
        if q:
            qs = qs.filter(Q(title__icontains=q) | Q(description__icontains=q))

        assignee_id = _get_first(data, "assignee_id", "assignee", "user_id")
        if assignee_id:
            qs = qs.filter(assigned_to_id=assignee_id)

        task_id = str(
            _get_first(data, "task_id", "taskId", "id", "task_ref") or ""
        ).strip().lstrip("#")
        if task_id:
            try:
                qs = qs.filter(pk=task_id)
            except (ValueError, TypeError):
                qs = qs.none()
            if not qs.exists() and len(task_id.replace("-", "")) >= 6:
                needle = task_id.replace("-", "").upper()
                matched_ids = [
                    t.id
                    for t in Task.objects.filter(restaurant=restaurant).only("id")[:200]
                    if _short_record_ref(t.id) == needle
                    or str(t.id).replace("-", "").upper().endswith(needle)
                ]
                qs = Task.objects.filter(restaurant=restaurant, id__in=matched_ids)

        # Align with Operations Live: hide custom-widget-only tiles unless asked.
        include_custom = _coerce_bool(
            _get_first(data, "include_custom_widgets", "includeCustomWidgets"),
            default=False,
        )
        if not include_custom:
            qs = qs.filter(custom_widget__isnull=True)

        try:
            limit = min(int(_get_first(data, "limit") or 20), 50)
        except (TypeError, ValueError):
            limit = 20

        tasks = list(qs.order_by("due_date", "-priority", "-created_at")[:limit])
        payload = DashboardTaskCompactSerializer(tasks, many=True).data
        if overdue:
            label = "overdue tasks"
        elif raw_status and str(raw_status).upper() in ("ALL", "*"):
            label = "tasks"
        else:
            label = "open tasks"
        return Response(
            {
                "success": True,
                "count": len(payload),
                "tasks": payload,
                "overdue": overdue,
                "filter": {
                    "status": raw_status or "OPEN",
                    "q": q or None,
                },
                "message_for_user": (
                    f"Found {len(payload)} {label}."
                    if payload
                    else f"No {label} found."
                ),
            }
        )
    except Exception as exc:
        logger.exception("agent_list_dashboard_tasks crashed")
        return Response(
            {
                "success": False,
                "error": str(exc)[:200],
                "message_for_user": "Something went wrong while listing tasks.",
            },
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )
