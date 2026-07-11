"""
Create StaffRequest rows from WhatsApp staff escalations (Django-owned path).
"""

from __future__ import annotations

import logging
from typing import Optional

from django.utils import timezone

from accounts.models import CustomUser
from dashboard.category_routing import (
    ensure_dashboard_widgets_for_managers,
    primary_widget_for_category,
    widget_lane_label,
)
from notifications.services import notification_service

from .intent_router import classify_request, staff_request_category
from .models import StaffRequest, StaffRequestComment
from .request_routing import resolve_default_assignee_for_category
from .views_agent import (
    _invalidate_staff_requests_cache,
    _notify_managers_of_staff_request,
    _normalize_category,
    _short_ref,
)

logger = logging.getLogger(__name__)


def _staff_facing_message(category: str) -> str:
    if category == "PAYROLL":
        return (
            "Thanks — I've passed your unpaid wages / payroll note on to your manager. "
            "They'll see it under *Human Resources* (Pending) and get back to you as soon as they can."
        )
    if category in ("HR", "DOCUMENT"):
        return (
            "Thanks — I've passed that on to your manager. "
            "They'll see it under *Human Resources* and get back to you as soon as they can."
        )
    return (
        "Thanks — I've passed that on to your manager. "
        "They'll get back to you as soon as they can."
    )


def ingest_staff_escalation_from_whatsapp(
    *,
    user: CustomUser,
    phone_digits: str,
    subject: str,
    description: str,
    agent_category: Optional[str] = None,
    external_id: str = "",
) -> str:
    """
    Persist a staff escalation and notify managers. Returns staff-facing WhatsApp text.
    """
    restaurant = getattr(user, "restaurant", None)
    if not restaurant:
        return "Your account has no restaurant context. Please contact your manager."

    subject = (subject or "").strip() or (description or "")[:80]
    description = (description or "").strip()
    if not description:
        return "Please say what you'd like your manager to know in one short message."

    decision = classify_request(
        subject=subject,
        description=description,
        agent_category=agent_category,
    )
    if decision.is_incident():
        category = _normalize_category(agent_category)
    else:
        # MEETING (task/calendar) → OPERATIONS for StaffRequest lanes
        category = _normalize_category(staff_request_category(decision.category))

    priority = "MEDIUM"
    if category == "MAINTENANCE":
        priority = "HIGH"

    staff = user
    staff_name = ""
    try:
        staff_name = staff.get_full_name() or f"{staff.first_name} {staff.last_name}".strip()
    except Exception:
        staff_name = f"{getattr(staff, 'first_name', '')} {getattr(staff, 'last_name', '')}".strip()
    staff_phone = getattr(staff, "phone", "") or phone_digits
    if not staff_phone:
        email = getattr(staff, "email", "") or ""
        if email.lower().startswith("wa_") and "@" in email:
            digits = "".join(c for c in email.split("@", 1)[0][3:] if c.isdigit())
            if len(digits) >= 8:
                staff_phone = f"+{digits}"
    if not staff_name and staff_phone:
        staff_name = staff_phone if str(staff_phone).startswith("+") else f"+{staff_phone}"

    assignee = resolve_default_assignee_for_category(restaurant, category)
    auto_assigned = assignee is not None

    ext = (external_id or "").strip()
    if ext:
        existing = (
            StaffRequest.objects.filter(restaurant=restaurant, external_id=ext)
            .order_by("-created_at")
            .first()
        )
        if existing:
            logger.info(
                "whatsapp_escalation_ingest: idempotent hit restaurant=%s request=%s ext=%s",
                restaurant.id,
                existing.id,
                ext,
            )
            return _staff_facing_message(existing.category or category)

    inbox_metadata = {
        "source_context": "whatsapp_escalation_webhook",
        "intent_router": {
            "category": decision.category,
            "confidence": decision.confidence,
            "matched_terms": list(decision.matched_terms),
            "agent_category": (agent_category or "OTHER"),
            "auto_categorised": decision.category != (agent_category or "OTHER").upper(),
        },
    }

    req = StaffRequest.objects.create(
        restaurant=restaurant,
        staff=staff,
        staff_name=staff_name,
        staff_phone=staff_phone,
        category=category,
        priority=priority,
        status="PENDING",
        subject=subject,
        description=description,
        assignee=assignee,
        source="whatsapp",
        external_id=ext,
        metadata=inbox_metadata,
        follow_up_enabled=True,
        follow_up_max=2,
    )
    _invalidate_staff_requests_cache(restaurant.id)

    StaffRequestComment.objects.create(
        request=req,
        author=None,
        kind="system",
        body="Request received via WhatsApp",
        metadata={"source": "whatsapp", "phone": phone_digits},
    )

    if assignee:
        StaffRequestComment.objects.create(
            request=req,
            author=None,
            kind="system",
            body=(
                f"Auto-assigned to {assignee.get_full_name() or assignee.email} "
                f"(category owner for {category.lower()})"
            ),
            metadata={
                "assignee_id": str(assignee.id),
                "auto_assigned": auto_assigned,
                "category": category,
            },
        )
        owner_phone = getattr(assignee, "phone", "") or ""
        if owner_phone:
            try:
                wa_ok, _ = notification_service.send_whatsapp_text(
                    owner_phone,
                    (
                        f"📩 New {category.lower()} request from "
                        f"{staff_name or 'a staff member'}: "
                        f"\"{subject[:80]}\". Open the inbox to review."
                    ),
                )
                if wa_ok:
                    req.whatsapp_notified_at = timezone.now()
                    req.save(update_fields=["whatsapp_notified_at", "updated_at"])
            except Exception as exc:
                logger.warning("StaffRequest assignee WhatsApp ping failed: %s", exc)

    _notify_managers_of_staff_request(req)
    ensure_dashboard_widgets_for_managers(restaurant, category=category)

    ref = _short_ref(req.id)
    lane = widget_lane_label(primary_widget_for_category(category))
    logger.info(
        "whatsapp_escalation_ingest: restaurant=%s request=%s category=%s ref=%s lane=%s",
        restaurant.id,
        req.id,
        category,
        ref,
        lane,
    )

    return _staff_facing_message(category)
