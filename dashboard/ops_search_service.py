"""Shared ops search used by dashboard UI and Miya agent tools."""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Q

from dashboard.views_ops_memory import _is_user_absent

logger = logging.getLogger(__name__)


def run_ops_search(
    restaurant,
    *,
    q: str,
    module: str = "all",
    status_filter: str = "",
    category_filter: str = "",
    assignee_id: str = "",
    date_from: str = "",
    date_to: str = "",
    user=None,
) -> dict[str, Any]:
    """Return unified search payload for staff, tasks, requests, invoices, etc."""
    from accounts.models import CustomUser
    from dashboard.models import Task
    from staff.models import StaffRequest

    q = (q or "").strip()
    module = (module or "all").strip().lower()
    include = lambda key: module in ("", "all", key)

    staff_hits = []
    if include("staff"):
        staff_hits = list(
            CustomUser.objects.filter(restaurant=restaurant)
            .filter(
                Q(first_name__icontains=q)
                | Q(last_name__icontains=q)
                | Q(email__icontains=q)
                | Q(phone__icontains=q)
            )[:15]
        )

    tasks = []
    if include("tasks"):
        tqs = Task.objects.filter(restaurant=restaurant).filter(
            Q(title__icontains=q) | Q(description__icontains=q)
        )
        if status_filter:
            tqs = tqs.filter(status__iexact=status_filter)
        if category_filter:
            tqs = tqs.filter(category__iexact=category_filter)
        if assignee_id:
            tqs = tqs.filter(assigned_to_id=assignee_id)
        if date_from:
            tqs = tqs.filter(due_date__gte=date_from)
        if date_to:
            tqs = tqs.filter(due_date__lte=date_to)
        tasks = list(tqs.select_related("assigned_to")[:20])

    requests_hits = []
    if include("requests") or include("staff_requests"):
        rqs = StaffRequest.objects.filter(restaurant=restaurant).filter(
            Q(subject__icontains=q) | Q(description__icontains=q)
        )
        if status_filter:
            rqs = rqs.filter(status__iexact=status_filter)
        if category_filter:
            rqs = rqs.filter(category__iexact=category_filter)
        if assignee_id:
            rqs = rqs.filter(assignee_id=assignee_id)
        requests_hits = list(rqs.select_related("assignee", "staff")[:20])

    invoices = []
    if include("invoices"):
        try:
            from finance.models import Invoice

            iqs = Invoice.objects.filter(restaurant=restaurant).filter(
                Q(vendor_name__icontains=q)
                | Q(invoice_number__icontains=q)
                | Q(notes__icontains=q)
                | Q(category__icontains=q)
            )
            if status_filter:
                iqs = iqs.filter(status__iexact=status_filter)
            if date_from:
                iqs = iqs.filter(due_date__gte=date_from)
            if date_to:
                iqs = iqs.filter(due_date__lte=date_to)
            invoices = list(iqs[:15])
        except Exception:
            logger.exception("ops-search invoices failed")

    incidents = []
    if include("incidents"):
        try:
            from staff.models import SafetyConcernReport

            incidents = list(
                SafetyConcernReport.objects.filter(restaurant=restaurant)
                .filter(
                    Q(title__icontains=q)
                    | Q(description__icontains=q)
                    | Q(location__icontains=q)
                    | Q(incident_type__icontains=q)
                )
                .order_by("-created_at")[:15]
            )
        except Exception:
            try:
                from staff.models import IncidentReport

                incidents = list(
                    IncidentReport.objects.filter(restaurant=restaurant)
                    .filter(Q(title__icontains=q) | Q(description__icontains=q))
                    .order_by("-created_at")[:15]
                )
            except Exception:
                logger.debug("ops-search: no incident model available", exc_info=True)

    reminders = []
    if include("reminders"):
        try:
            from scheduling.memory_models import PersonalReminder

            reminders = list(
                PersonalReminder.objects.filter(restaurant=restaurant)
                .filter(Q(title__icontains=q) | Q(body__icontains=q))
                .select_related("owner")
                .order_by("-due_at")[:15]
            )
        except Exception:
            logger.debug("ops-search: reminders unavailable", exc_info=True)

    meetings = []
    if include("meetings") and user is not None:
        try:
            from django.utils import timezone as dj_tz
            from dashboard.api.meetings_reminders import (
                MeetingsRemindersView,
                _get_valid_access_token,
            )

            access_token, _gcal = _get_valid_access_token(restaurant)
            if access_token:
                view = MeetingsRemindersView()
                events = view._fetch_events(access_token, user, dj_tz.now())
                q_l = q.lower()
                for e in events or []:
                    title = e.get("title") or ""
                    if q_l in title.lower():
                        meetings.append(
                            {
                                "id": e.get("id"),
                                "title": title,
                                "start": e.get("start"),
                                "end": e.get("end"),
                                "status": e.get("status"),
                                "owner_label": e.get("owner_label"),
                                "href": e.get("html_link"),
                            }
                        )
                        if len(meetings) >= 10:
                            break
        except Exception:
            logger.debug("ops-search: meetings unavailable", exc_info=True)

    staff_payload = []
    for u in staff_hits:
        assigned = list(
            Task.objects.filter(
                restaurant=restaurant,
                assigned_to=u,
                status__in=["PENDING", "ACCEPTED", "IN_PROGRESS"],
            ).values("id", "title", "status", "priority")[:10]
        )
        staff_payload.append(
            {
                "id": str(u.id),
                "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                "phone": u.phone or "",
                "role": getattr(u, "role", "") or "",
                "is_absent": _is_user_absent(u, restaurant),
                "open_tasks": [
                    {"id": str(t["id"]), "title": t["title"], "status": t["status"]}
                    for t in assigned
                ],
            }
        )

    def _task_row(t: Task):
        absent = _is_user_absent(t.assigned_to, restaurant) if t.assigned_to_id else False
        return {
            "id": str(t.id),
            "title": t.title,
            "status": t.status,
            "category": t.category,
            "assigned_to": (
                f"{t.assigned_to.first_name} {t.assigned_to.last_name}".strip()
                if t.assigned_to_id
                else None
            ),
            "assignee_absent": absent,
            "requires_manager_validation": t.requires_manager_validation,
            "validation_label": (
                None
                if not t.requires_manager_validation
                else ("validated" if t.manager_validated_at else "not validated by manager")
            ),
            "has_photo_proof": bool(t.proof_media_url),
            "proof_media_url": t.proof_media_url or None,
            "href": f"/dashboard?task={t.id}",
        }

    return {
        "success": True,
        "module": module,
        "staff": staff_payload,
        "tasks": [_task_row(t) for t in tasks],
        "staff_requests": [
            {
                "id": str(r.id),
                "subject": r.subject,
                "category": r.category,
                "status": r.status,
                "assignee": (
                    f"{r.assignee.first_name} {r.assignee.last_name}".strip()
                    if r.assignee_id
                    else None
                ),
                "assignee_absent": _is_user_absent(r.assignee, restaurant)
                if r.assignee_id
                else False,
                "href": f"/dashboard/staff-requests/{r.id}",
            }
            for r in requests_hits
        ],
        "invoices": [
            {
                "id": str(inv.id),
                "vendor_name": inv.vendor_name,
                "invoice_number": getattr(inv, "invoice_number", "") or "",
                "amount": str(inv.amount),
                "currency": getattr(inv, "currency", "") or "",
                "status": inv.status,
                "due_date": inv.due_date.isoformat() if inv.due_date else None,
                "href": f"/dashboard/staff-requests/{inv.id}?kind=invoice",
            }
            for inv in invoices
        ],
        "incidents": [
            {
                "id": str(inc.id),
                "title": getattr(inc, "title", "") or "",
                "status": getattr(inc, "status", "") or "",
                "href": f"/dashboard/staff-requests?kind=incident&id={inc.id}",
            }
            for inc in incidents
        ],
        "reminders": [
            {
                "id": str(rem.id),
                "title": rem.title,
                "due_at": rem.due_at.isoformat() if rem.due_at else None,
                "status": rem.status,
                "has_attachment": bool(
                    getattr(rem, "attachment_url", None) or getattr(rem, "attachment", None)
                ),
            }
            for rem in reminders
        ],
        "meetings": meetings,
    }
