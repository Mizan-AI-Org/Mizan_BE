"""
Agent endpoints for the dashboard's Tasks & Demands surface.

Exposes HTTP endpoints that Miya / the Lua agent can call (using
`Authorization: Bearer <LUA_WEBHOOK_API_KEY>` OR a user JWT) to create a
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
from django.db.models import Q, Value
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

    # ---- 1) user_id
    uid = _get_first(data, "user_id", "assignee_user_id", "assigned_to", "userId")
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
    lines.append("Reply here when it's done or if you have questions.")
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

    Auth: `Authorization: Bearer <LUA_WEBHOOK_API_KEY>` OR a user JWT
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

        # Resolve assignee.
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

        # Acting user (for the `sender` on the notification + audit trail).
        # May already be set by _resolve_restaurant_for_agent via JWT; if
        # not, try a JWT-only pass.
        if not acting_user:
            try:
                _, acting_user = _try_jwt_restaurant_and_user(request)
            except Exception:
                acting_user = None

        sender_display = "Your manager"
        if acting_user:
            nm = f"{(acting_user.first_name or '').strip()} {(acting_user.last_name or '').strip()}".strip()
            sender_display = nm or getattr(acting_user, "email", None) or "Your manager"
        source_label = "Miya AI" + (f" · {sender_display}" if acting_user else "")

        # Create the task atomically.
        with transaction.atomic():
            task = Task.objects.create(
                restaurant=restaurant,
                assigned_to=assignee,
                title=title,
                description=description or None,
                priority=priority,
                status="PENDING",
                due_date=due_date,
                source="MIYA",
                source_label=source_label[:120],
                ai_summary=ai_summary,
            )

        logger.info(
            "Miya created Task %s (%r) for user %s in restaurant %s",
            task.id, title, assignee.id, restaurant.id,
        )

        # In-app notification — create unconditionally so the staff member
        # sees it in their bell even if WhatsApp fails. Best-effort: if
        # this fails we still consider the task created.
        try:
            notification_service.send_custom_notification(
                recipient=assignee,
                message=(
                    f"New task: {task.title}"
                    + (f" (due {_format_due(task.due_date)})" if task.due_date else "")
                ),
                title="New task assigned",
                notification_type="TASK_ASSIGNED",
                channels=["app", "push"],
                sender=acting_user,
            )
        except Exception:
            logger.exception("Miya create_task: in-app notification failed for task %s", task.id)

        # WhatsApp notification.
        notify_whatsapp = _coerce_bool(
            _get_first(data, "notify_whatsapp", "notifyWhatsapp", "send_whatsapp"),
            default=True,
        )
        wa_override = _get_first(data, "whatsapp_message", "whatsappMessage", "message")
        wa_result: dict[str, Any] = {
            "sent": False,
            "skipped_reason": None,
            "error": None,
            "provider_status": None,
        }

        if not notify_whatsapp:
            wa_result["skipped_reason"] = "disabled"
        elif not (assignee.phone or "").strip():
            wa_result["skipped_reason"] = "no_phone"
            wa_result["error"] = (
                f"{assignee.first_name or 'Staff member'} has no phone number on file."
            )
        else:
            body = _build_whatsapp_body(
                task=task,
                sender_name=sender_display,
                assignee_first_name=(assignee.first_name or "").strip(),
                override=wa_override if isinstance(wa_override, str) else None,
            )
            try:
                ok, resp = notification_service.send_whatsapp_text(assignee.phone, body)
                wa_result["sent"] = bool(ok)
                if isinstance(resp, dict):
                    wa_result["provider_status"] = resp.get("status_code")
                    if not ok:
                        err_msg = resp.get("error")
                        if not err_msg and isinstance(resp.get("data"), dict):
                            err_msg = (
                                resp["data"].get("error", {}).get("message")
                                if isinstance(resp["data"].get("error"), dict)
                                else None
                            )
                        wa_result["error"] = err_msg or "WhatsApp send failed"
            except Exception as exc:  # pragma: no cover - defensive
                logger.exception("Miya create_task: WhatsApp send crashed for task %s", task.id)
                wa_result["error"] = str(exc)[:200]

        # Build the human-facing confirmation string.
        pretty_priority = priority.lower() if priority != "URGENT" else "URGENT"
        due_phrase = _format_due(task.due_date)
        assignee_display = (
            f"{(assignee.first_name or '').strip()} {(assignee.last_name or '').strip()}".strip()
            or assignee.email
        )
        if wa_result["sent"]:
            wa_phrase = "WhatsApp notification sent."
        elif wa_result["skipped_reason"] == "no_phone":
            wa_phrase = (
                f"{assignee.first_name or 'They'} has no phone on file, so I couldn't send WhatsApp — "
                "the task is in their inbox."
            )
        elif wa_result["skipped_reason"] == "disabled":
            wa_phrase = "WhatsApp notification skipped (caller asked not to send)."
        elif wa_result["error"]:
            wa_phrase = f"WhatsApp send failed: {wa_result['error']}"
        else:
            wa_phrase = ""
        message_for_user = (
            f"Created '{task.title}' for {assignee_display} "
            f"({pretty_priority} priority, due {due_phrase}). {wa_phrase}"
        ).strip()

        return Response(
            {
                "success": True,
                "task": DashboardTaskCompactSerializer(task).data,
                "assignee": {
                    "id": str(assignee.id),
                    "name": assignee_display,
                    "phone": assignee.phone or "",
                    "role": getattr(assignee, "role", None),
                },
                "whatsapp": wa_result,
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
