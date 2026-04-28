"""
Category-bucketed dashboard widget endpoint.

Powers the Human Resources / Finance / Maintenance / Meetings & Reminders
/ Urgent Top-5 widgets on the manager dashboard. Each widget asks for one
bucket and gets back the top-N most pressing tasks for that bucket,
merged from every system that produces tasks for the manager:

* ``dashboard.Task``  — Miya-created and manually-created tasks. The
  ``category`` column is the canonical bucket. For legacy rows where
  ``category`` is NULL, we fall back to running the deterministic
  ``staff.intent_router`` classifier on title + description so old rows
  still find a home.
* ``staff.StaffRequest`` — WhatsApp/voice/email-ingested requests from
  staff. ``category`` is already populated by the ingest pipeline.

The shape returned mirrors ``DashboardTaskCompactSerializer`` (used by
the existing Tasks & Demands widget) so the same React row component can
render every category card.

Special bucket: ``urgent`` returns the top urgent open items across all
categories — that's the "Urgent TOP 5" card.
"""

from __future__ import annotations

from datetime import timedelta
from typing import Any

from django.db.models import Case, IntegerField, Q, Value, When
from django.utils import timezone
from rest_framework import permissions, status as http_status
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache

from ..models import Task
from ..serializers import DashboardTaskCompactSerializer

# Buckets the widget API exposes. Each maps to one or more
# ``Task.category`` and ``StaffRequest.category`` values.
#
# Order inside each tuple matters only for deciding which slug to show
# in the response — first one wins. Both DOCUMENT and HR feed the
# Human Resources widget because the screenshots-mockup ("Print & sign
# contracts", "Pictures of staff") mixes them in everyday usage.
BUCKET_TO_CATEGORIES: dict[str, tuple[str, ...]] = {
    "human_resources": ("HR", "DOCUMENT"),
    "finance": ("FINANCE", "PAYROLL"),
    "maintenance": ("MAINTENANCE",),
    "meetings": ("MEETING",),
    # Procurement asks — "we need to buy 6 bottles of vodka", "place a
    # PO for 50kg of flour". Distinct from FINANCE (paying invoices)
    # and INVENTORY (state observations). Whoever owns inventory in
    # the tenant's onboarding settings receives a WhatsApp ping when
    # Miya creates one, so the manager who said "we need to buy X"
    # gets confirmation that the right person was notified.
    "purchase_orders": ("PURCHASE_ORDER",),
    # Catch-all lane for anything Miya couldn't confidently route into a
    # named category (intent_router returned ``OTHER``). Surfacing these
    # on the dashboard means general / one-off requests still get seen
    # — they're not silently buried in the inbox.
    "miscellaneous": ("OTHER",),
    # ``urgent`` is special-cased below — it filters by priority instead
    # of category. Keeping it in the dict makes validation a single check.
    "urgent": (),
}

DEFAULT_LIMIT = 5
MAX_LIMIT = 25

# Same priority ordering vocabulary as the Tasks & Demands endpoint so
# urgent items always sit at the top.
_PRIORITY_RANK = Case(
    When(priority="URGENT", then=Value(0)),
    When(priority="HIGH", then=Value(1)),
    When(priority="MEDIUM", then=Value(2)),
    When(priority="LOW", then=Value(3)),
    default=Value(4),
    output_field=IntegerField(),
)
_PRIORITY_RANK_MAP = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}

# StaffRequest.status semantics → widget vocabulary. We treat APPROVED
# rows that haven't been CLOSED as still pending action because most
# Inbox flows mark them APPROVED to mean "manager acknowledged" rather
# than "done".
#
# Two layers of mapping:
#
# 1. ``status`` (kept for back-compat with the existing filter/counter
#    logic in the React widget — it only knows about the four bucket
#    states: PENDING / IN_PROGRESS / COMPLETED / CANCELLED).
# 2. ``pill_status`` — granular state used to render the colored pill on
#    each row. Lets the manager glance at a row and immediately see
#    whether it's been assigned, escalated, parked waiting on someone,
#    or running past its due date. The frontend falls back to ``status``
#    when ``pill_status`` is missing so older deployments still render.
_STAFF_STATUS_TO_WIDGET = {
    "PENDING": "PENDING",
    "ESCALATED": "PENDING",
    "APPROVED": "IN_PROGRESS",
    # Manager has parked the request awaiting an external dependency.
    # Treated like "in progress" for widget aggregation: it's not new
    # but it's not done either, and the SLA sweep keeps an eye on it.
    "WAITING_ON": "IN_PROGRESS",
    "REJECTED": "CANCELLED",
    "CLOSED": "COMPLETED",
}


# How long after creation a row is still considered "fresh" enough to
# wear the NEW pill. Six hours catches the morning batch of WhatsApp
# captures without leaving rows from yesterday claiming to be new.
_NEW_BADGE_HOURS = 6


def _age_label(when, now=None) -> str:
    """Return a compact human-friendly relative-time string.

    Examples: ``"just now"``, ``"12m ago"``, ``"3h ago"``, ``"yesterday"``,
    ``"5d ago"``, ``"3w ago"``. Empty string when ``when`` is falsy.

    Kept tiny on purpose — the widget shows it as a faint subtitle, so
    we want at most ~6 characters in 99% of cases.
    """
    if not when:
        return ""
    now = now or timezone.now()
    delta = now - when
    seconds = int(delta.total_seconds())
    if seconds < 0:
        # Future timestamps shouldn't happen on created_at but defensively
        # still return something readable.
        return "just now"
    if seconds < 60:
        return "just now"
    minutes = seconds // 60
    if minutes < 60:
        return f"{minutes}m ago"
    hours = minutes // 60
    if hours < 24:
        return f"{hours}h ago"
    days = hours // 24
    if days == 1:
        return "yesterday"
    if days < 7:
        return f"{days}d ago"
    weeks = days // 7
    if weeks < 5:
        return f"{weeks}w ago"
    months = days // 30
    if months < 12:
        return f"{months}mo ago"
    years = days // 365
    return f"{years}y ago"


def _task_pill_status(task, *, now=None) -> str:
    """Granular pill state for a ``dashboard.Task`` row.

    Priorities, in order:
    - ``DONE`` if the task is COMPLETED (final).
    - ``CANCELLED`` if the task is CANCELLED (final).
    - ``OVERDUE`` if there's a due date that has passed and the task
      isn't already done — this floats the row visually so the manager
      knows it's slipping.
    - ``IN_PROGRESS`` if the task is being worked on.
    - ``NEW`` if the task is fresh (< 6h) and still PENDING — gives
      newly captured items a distinct visual signal so they're easy
      to spot in a busy widget.
    - ``PENDING`` otherwise (default).
    """
    now = now or timezone.now()
    status = (task.status or "").upper()
    if status == "COMPLETED":
        return "DONE"
    if status == "CANCELLED":
        return "CANCELLED"
    due = getattr(task, "due_date", None)
    if due is not None:
        try:
            today = now.date()
            if due < today:
                return "OVERDUE"
        except Exception:
            pass
    if status == "IN_PROGRESS":
        return "IN_PROGRESS"
    created = getattr(task, "created_at", None)
    if created and (now - created).total_seconds() < _NEW_BADGE_HOURS * 3600:
        return "NEW"
    return "PENDING"


def _staff_request_pill_status(req, *, now=None) -> str:
    """Granular pill state for a ``staff.StaffRequest`` row.

    The manager Inbox uses six raw states (PENDING/APPROVED/REJECTED/
    ESCALATED/CLOSED/WAITING_ON). We map them here to a slightly richer
    vocabulary so the widget pill can distinguish "escalated" from
    "waiting on" at a glance — both used to be folded into PENDING /
    IN_PROGRESS which lost the nuance.
    """
    now = now or timezone.now()
    status = (req.status or "").upper()
    if status == "CLOSED":
        return "DONE"
    if status == "REJECTED":
        return "CANCELLED"
    if status == "ESCALATED":
        return "ESCALATED"
    if status == "WAITING_ON":
        # If WAITING_ON has a follow-up date that's already passed,
        # surface it as OVERDUE so the manager knows the SLA tripped.
        follow_up = getattr(req, "follow_up_date", None)
        if follow_up is not None:
            try:
                if follow_up < now.date():
                    return "OVERDUE"
            except Exception:
                pass
        return "WAITING_ON"
    if status == "APPROVED":
        # APPROVED = manager acknowledged. If an assignee is set we say
        # "ASSIGNED"; otherwise it's just "in progress" on the manager's
        # plate. This matches how the column is used in practice.
        if getattr(req, "assignee_id", None):
            return "ASSIGNED"
        return "IN_PROGRESS"
    # PENDING + anything unexpected falls through here.
    created = getattr(req, "created_at", None)
    if created and (now - created).total_seconds() < _NEW_BADGE_HOURS * 3600:
        return "NEW"
    return "PENDING"


def _invoice_pill_status(inv, *, now=None) -> str:
    """Granular pill state for a ``finance.Invoice`` row.

    Invoices have their own status vocabulary (DRAFT/OPEN/PAID/VOIDED)
    plus an ``is_overdue`` derived flag. We translate them here so the
    Finance widget can render OVERDUE / DUE_SOON / OPEN / PAID / VOIDED
    / DRAFT distinctly. ``DUE_SOON`` (≤3 days, not overdue) gives the
    manager a one-glance heads-up before bills slip.
    """
    inv_status = (inv.status or "").upper()
    if inv_status == "PAID":
        return "PAID"
    if inv_status == "VOIDED":
        return "VOIDED"
    if inv_status == "DRAFT":
        return "DRAFT"
    if getattr(inv, "is_overdue", False):
        return "OVERDUE"
    days_left = getattr(inv, "days_until_due", None)
    if days_left is not None and 0 <= days_left <= 3:
        return "DUE_SOON"
    return "OPEN"


def _assignee_payload(user) -> dict | None:
    if not user:
        return None
    first = (getattr(user, "first_name", None) or "").strip()
    last = (getattr(user, "last_name", None) or "").strip()
    full = f"{first} {last}".strip() or (getattr(user, "email", None) or "")
    initials = (first[:1] + last[:1]).upper() or (full[:2] if full else "").upper()
    return {
        "id": str(user.pk),
        "name": full,
        "initials": initials or "?",
        "role": getattr(user, "role", None),
    }


def _serialize_dashboard_task(task, *, now=None) -> dict[str, Any]:
    data = DashboardTaskCompactSerializer(task).data
    data["kind"] = "dashboard"
    # Granular pill_status + relative age_label so the row can show a
    # clear status indicator without making a separate API call. The
    # frontend falls back to ``status`` when ``pill_status`` is missing.
    data["pill_status"] = _task_pill_status(task, now=now)
    data["age_label"] = _age_label(getattr(task, "created_at", None), now=now)
    return data


def _serialize_staff_request(req, *, now=None) -> dict[str, Any]:
    """Normalise a StaffRequest row into the widget's task shape.

    Picks ``staff_name`` over the ``staff`` user when both are set
    because the request inbox often only has the phone-only display name
    (e.g. WhatsApp captures with no matching user account).
    """
    user_for_avatar = req.staff or req.assignee
    assignee = _assignee_payload(user_for_avatar)
    if assignee is None and (req.staff_name or "").strip():
        # Fallback for phone-only WhatsApp captures.
        full = req.staff_name.strip()
        parts = full.split()
        initials = (
            (parts[0][:1] + (parts[-1][:1] if len(parts) > 1 else "")).upper() or "?"
        )
        assignee = {
            "id": "",
            "name": full,
            "initials": initials,
            "role": None,
        }

    return {
        "id": str(req.id),
        "title": (req.subject or req.description or "").strip()[:255] or "Staff request",
        "description": req.description or "",
        "priority": req.priority,
        "status": _STAFF_STATUS_TO_WIDGET.get(req.status, "PENDING"),
        # Granular state surfaced on the row pill so ESCALATED / ASSIGNED /
        # WAITING_ON aren't all collapsed into a single yellow "Pending".
        "pill_status": _staff_request_pill_status(req, now=now),
        "raw_status": req.status,
        "age_label": _age_label(req.created_at, now=now),
        "due_date": None,
        "source": "WHATSAPP",
        "source_label": "Inbox",
        "ai_summary": "",
        "category": req.category,
        "assignee": assignee,
        "created_at": req.created_at.isoformat() if req.created_at else None,
        "updated_at": req.updated_at.isoformat() if req.updated_at else None,
        "kind": "staff_request",
    }


def _serialize_invoice(inv, *, now=None) -> dict[str, Any]:
    """Normalise a finance.Invoice into the widget's task shape.

    The Finance widget already pulls Task + StaffRequest(category=FINANCE).
    Invoices live in their own table (``finance.Invoice``) and were the
    user-visible black hole behind "Miya says it's in Finance but it's not
    there" — they were never being injected into this widget. We bridge
    them here so any invoice Miya logs (via record_invoice or the photo /
    document router) shows up next to the staff requests automatically.

    Field mapping:
    - ``title``        : "Invoice {number} — {vendor}" (or just "{vendor}" when no number)
    - ``ai_summary``   : human-readable amount + due-date phrase
    - ``priority``     : derived from days_until_due so overdue invoices float to top
    - ``status``       : OPEN/OVERDUE → PENDING; PAID → COMPLETED; VOIDED → CANCELLED
    - ``due_date``     : invoice's due_date (drives the secondary sort)
    - ``assignee``     : the user who created the invoice (or ``None`` for agent-created)
    """
    days_left = inv.days_until_due
    is_overdue = inv.is_overdue

    if inv.status == "PAID":
        widget_status = "COMPLETED"
    elif inv.status == "VOIDED":
        widget_status = "CANCELLED"
    else:  # OPEN or DRAFT
        widget_status = "PENDING"

    if widget_status != "PENDING":
        priority = "MEDIUM"
    elif is_overdue:
        priority = "URGENT"
    elif days_left is not None and days_left <= 1:
        priority = "URGENT"
    elif days_left is not None and days_left <= 3:
        priority = "HIGH"
    elif days_left is not None and days_left <= 7:
        priority = "MEDIUM"
    else:
        priority = "LOW"

    title_parts: list[str] = []
    if inv.invoice_number:
        title_parts.append(f"#{inv.invoice_number}")
    if inv.vendor_name:
        title_parts.append(inv.vendor_name)
    title = " — ".join(title_parts) if title_parts else "Invoice"
    title = title[:255] or "Invoice"

    if days_left is None:
        due_phrase = "no due date"
    elif days_left < 0:
        due_phrase = f"overdue by {abs(days_left)} day{'s' if abs(days_left) != 1 else ''}"
    elif days_left == 0:
        due_phrase = "due today"
    elif days_left == 1:
        due_phrase = "due tomorrow"
    else:
        due_phrase = f"due in {days_left} days"

    summary = f"{inv.amount} {inv.currency} · {due_phrase}"
    if inv.status == "PAID":
        summary = f"{inv.amount} {inv.currency} · paid"
    elif inv.status == "VOIDED":
        summary = f"{inv.amount} {inv.currency} · voided"

    assignee = _assignee_payload(inv.created_by)

    return {
        "id": str(inv.id),
        "title": title,
        "description": (inv.notes or "")[:1000],
        "priority": priority,
        "status": widget_status,
        # Granular pill: PAID / VOIDED / DRAFT / OVERDUE / DUE_SOON / OPEN.
        # The Finance widget renders this directly so the manager sees
        # which bills are slipping without opening the row.
        "pill_status": _invoice_pill_status(inv, now=now),
        "age_label": _age_label(inv.created_at, now=now),
        "due_date": inv.due_date.isoformat() if inv.due_date else None,
        "source": "MIYA",
        "source_label": "Invoice",
        "ai_summary": summary,
        "category": "FINANCE",
        "assignee": assignee,
        "created_at": inv.created_at.isoformat() if inv.created_at else None,
        "updated_at": inv.updated_at.isoformat() if inv.updated_at else None,
        "kind": "invoice",
        # Extra fields the widget can use to render a vendor/amount chip
        # without re-fetching. The frontend ignores unknown keys today —
        # safe to add forward-looking metadata here.
        "vendor_name": inv.vendor_name,
        "invoice_number": inv.invoice_number or "",
        "amount": str(inv.amount),
        "currency": inv.currency,
        "invoice_status": inv.status,
        "is_overdue": is_overdue,
    }


def _sort_key(item: dict[str, Any]) -> tuple:
    """Order: priority asc → due_date asc (nulls last) → created_at desc."""
    prio = _PRIORITY_RANK_MAP.get(item.get("priority") or "", 4)
    due = item.get("due_date") or "9999-99-99"
    created = item.get("created_at") or ""
    return (prio, due, created and "" or "0", )  # placeholder; sorted twice below


def _classify_legacy_task_category(task) -> str | None:
    """Run the intent router on a NULL-category task.

    Only used as a fallback for rows that pre-date the ``category``
    column. We're conservative: if the classifier returns ``OTHER`` we
    leave the task uncategorised so it doesn't bleed into the wrong
    widget.
    """
    try:
        from staff.intent_router import classify_request
    except Exception:
        return None
    try:
        decision = classify_request(
            subject=task.title or "",
            description=task.description or "",
        )
    except Exception:
        return None
    cat = (decision.category or "").upper()
    if cat in {"OTHER", ""}:
        return None
    return cat


class CategoryTasksView(APIView):
    """
    GET /api/dashboard/category-tasks/?bucket=<bucket>&limit=5

    Returns the top-N pressing items for a single dashboard widget bucket
    plus a small ``counts`` payload so the widget can show a header chip
    like "12 pending · 3 done" without a second round-trip.

    Query params:
        bucket  one of ``urgent | human_resources | finance | maintenance
                | meetings``  (required)
        limit   1..25, default 5

    Response shape::

        {
          "bucket": "human_resources",
          "categories": ["HR", "DOCUMENT"],
          "items": [DashboardTaskCompactSerializer-shape, ...],
          "counts": {"open": N, "in_progress": N, "completed": N},
          "generated_at": "..."
        }
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"error": "No workspace associated"},
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        bucket = (request.query_params.get("bucket") or "").strip().lower()
        if bucket not in BUCKET_TO_CATEGORIES:
            return Response(
                {
                    "error": (
                        "bucket must be one of: "
                        + ", ".join(sorted(BUCKET_TO_CATEGORIES.keys()))
                    )
                },
                status=http_status.HTTP_400_BAD_REQUEST,
            )

        try:
            limit = int(request.query_params.get("limit") or DEFAULT_LIMIT)
        except (TypeError, ValueError):
            limit = DEFAULT_LIMIT
        limit = max(1, min(limit, MAX_LIMIT))

        # Horizon: today's open items + anything due in the next 14 days.
        # Completed items are limited to the last 14 days so the "Done"
        # filter stays useful on a live dashboard without growing forever.
        today = timezone.now().date()
        future_cutoff = today + timedelta(days=14)
        completed_floor = today - timedelta(days=14)

        is_urgent = bucket == "urgent"
        wanted_cats = BUCKET_TO_CATEGORIES[bucket]

        # ----- dashboard.Task --------------------------------------------
        db_qs = (
            Task.objects.filter(restaurant=restaurant)
            .select_related("assigned_to")
            .annotate(priority_rank=_PRIORITY_RANK)
        )

        # Misc bucket also includes legacy NULL-category rows — they are
        # uncategorised by definition, so they belong here. We OR that
        # in instead of relying on the read-time classifier (which would
        # otherwise just return OTHER for them anyway and skip them).
        is_misc = bucket == "miscellaneous"

        if is_urgent:
            db_open_qs = db_qs.filter(
                priority="URGENT",
                status__in=("PENDING", "IN_PROGRESS"),
            )
            db_completed_qs = db_qs.filter(
                priority="URGENT",
                status="COMPLETED",
                updated_at__date__gte=completed_floor,
            )
        else:
            if is_misc:
                cat_filter = Q(category__in=wanted_cats) | Q(category__isnull=True)
            else:
                cat_filter = Q(category__in=wanted_cats)
            db_open_qs = db_qs.filter(
                cat_filter,
                status__in=("PENDING", "IN_PROGRESS"),
            ).filter(Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff))
            db_completed_qs = db_qs.filter(
                cat_filter,
                status="COMPLETED",
                updated_at__date__gte=completed_floor,
            )

        db_open_rows = list(
            db_open_qs.order_by("priority_rank", "due_date", "-created_at")[
                : limit * 3
            ]
        )
        db_completed_rows = list(
            db_completed_qs.order_by("-updated_at")[: limit * 3]
        )
        db_open_count = db_open_qs.count()
        db_completed_count = db_completed_qs.count()
        db_in_progress_count = db_open_qs.filter(status="IN_PROGRESS").count()

        # Legacy rows where ``category`` is NULL: re-classify on read so
        # they still find their widget. We skip this when the bucket
        # is "urgent" (urgent already covers everything by priority so
        # legacy rows are included regardless) or "miscellaneous" (NULL
        # rows are already pulled in via the cat_filter OR clause above
        # — re-scanning them here would double-count).
        legacy_open: list = []
        legacy_completed: list = []
        legacy_open_count = 0
        legacy_completed_count = 0
        legacy_in_progress_count = 0
        if not is_urgent and not is_misc:
            legacy_qs = (
                Task.objects.filter(restaurant=restaurant, category__isnull=True)
                .select_related("assigned_to")
                .annotate(priority_rank=_PRIORITY_RANK)
            )
            # Cap the scan so a tenant with thousands of legacy rows
            # doesn't pay a huge cost on every dashboard refresh — the
            # widget only needs the top handful per bucket anyway.
            legacy_scan_cap = 200
            legacy_open_scan = list(
                legacy_qs.filter(status__in=("PENDING", "IN_PROGRESS"))
                .filter(Q(due_date__isnull=True) | Q(due_date__lte=future_cutoff))
                .order_by("priority_rank", "due_date", "-created_at")[
                    :legacy_scan_cap
                ]
            )
            legacy_completed_scan = list(
                legacy_qs.filter(
                    status="COMPLETED", updated_at__date__gte=completed_floor
                ).order_by("-updated_at")[:legacy_scan_cap]
            )
            for t in legacy_open_scan:
                inferred = _classify_legacy_task_category(t)
                if inferred and inferred in wanted_cats:
                    legacy_open.append(t)
                    legacy_open_count += 1
                    if t.status == "IN_PROGRESS":
                        legacy_in_progress_count += 1
            for t in legacy_completed_scan:
                inferred = _classify_legacy_task_category(t)
                if inferred and inferred in wanted_cats:
                    legacy_completed.append(t)
                    legacy_completed_count += 1

        # ----- staff.StaffRequest ---------------------------------------
        # Lazy import to keep the dashboard app importable even if the
        # staff app is in the middle of a migration.
        sr_open: list = []
        sr_completed: list = []
        sr_open_count = 0
        sr_completed_count = 0
        sr_in_progress_count = 0
        try:
            from staff.models import StaffRequest

            sr_qs = StaffRequest.objects.filter(restaurant=restaurant).select_related(
                "staff", "assignee"
            )
            if is_urgent:
                sr_open_qs = sr_qs.filter(
                    priority="URGENT",
                    status__in=("PENDING", "ESCALATED", "APPROVED", "WAITING_ON"),
                )
                sr_completed_qs = sr_qs.filter(
                    priority="URGENT",
                    status="CLOSED",
                    updated_at__date__gte=completed_floor,
                )
            else:
                # MEETING isn't a valid StaffRequest.category — skip the
                # SR side entirely so we don't blow up the query.
                if "MEETING" in wanted_cats and len(wanted_cats) == 1:
                    sr_open_qs = sr_qs.none()
                    sr_completed_qs = sr_qs.none()
                else:
                    valid_cats = tuple(c for c in wanted_cats if c != "MEETING")
                    sr_open_qs = sr_qs.filter(
                        category__in=valid_cats,
                        status__in=("PENDING", "ESCALATED", "APPROVED", "WAITING_ON"),
                    )
                    sr_completed_qs = sr_qs.filter(
                        category__in=valid_cats,
                        status="CLOSED",
                        updated_at__date__gte=completed_floor,
                    )
            sr_open = list(
                sr_open_qs.annotate(priority_rank=_PRIORITY_RANK).order_by(
                    "priority_rank", "-created_at"
                )[: limit * 3]
            )
            sr_completed = list(
                sr_completed_qs.order_by("-updated_at")[: limit * 3]
            )
            sr_open_count = sr_open_qs.count()
            sr_completed_count = sr_completed_qs.count()
            sr_in_progress_count = sr_open_qs.filter(status="APPROVED").count()
        except Exception:  # pragma: no cover - defensive
            pass

        # ----- finance.Invoice -------------------------------------------
        # Invoices live in their own table — the Finance widget needs to
        # surface them alongside Task + StaffRequest rows so a manager
        # who told Miya "log this bill" actually sees the bill on the
        # dashboard. We also include them in ``urgent`` when overdue or
        # due within 24h, since unpaid/late bills are inherently urgent.
        inv_open: list = []
        inv_completed: list = []
        inv_open_count = 0
        inv_completed_count = 0
        inv_in_progress_count = 0  # invoices have no in-progress concept; kept for parity
        if bucket in ("finance", "urgent"):
            try:
                from finance.models import Invoice

                inv_qs = (
                    Invoice.objects.filter(restaurant=restaurant)
                    .select_related("created_by")
                )
                if is_urgent:
                    # Only the bills that should genuinely be on the urgent
                    # widget: open + (overdue OR due in next 24h).
                    inv_open_qs = inv_qs.filter(
                        status=Invoice.STATUS_OPEN,
                    ).filter(Q(due_date__lte=today + timedelta(days=1)))
                    inv_completed_qs = inv_qs.none()
                else:
                    inv_open_qs = inv_qs.filter(
                        status__in=(Invoice.STATUS_OPEN, Invoice.STATUS_DRAFT)
                    ).filter(
                        Q(due_date__isnull=True)
                        | Q(due_date__lte=future_cutoff)
                        | Q(due_date__lt=today)  # always include overdue, even >14d old
                    )
                    inv_completed_qs = inv_qs.filter(
                        status=Invoice.STATUS_PAID,
                        updated_at__date__gte=completed_floor,
                    )
                inv_open = list(
                    inv_open_qs.order_by("due_date", "-created_at")[: limit * 3]
                )
                inv_completed = list(
                    inv_completed_qs.order_by("-updated_at")[: limit * 3]
                )
                inv_open_count = inv_open_qs.count()
                inv_completed_count = inv_completed_qs.count()
            except Exception:  # pragma: no cover - defensive (e.g. unmigrated env)
                pass

        # ----- merge & rank ---------------------------------------------
        # ``now`` shared across every serializer call so age_label and the
        # NEW-badge cutoff are computed against a consistent reference —
        # otherwise rows serialised milliseconds apart could disagree.
        serialize_now = timezone.now()
        open_items: list[dict[str, Any]] = []
        open_items.extend(_serialize_dashboard_task(t, now=serialize_now) for t in db_open_rows)
        open_items.extend(_serialize_dashboard_task(t, now=serialize_now) for t in legacy_open)
        open_items.extend(_serialize_staff_request(r, now=serialize_now) for r in sr_open)
        open_items.extend(_serialize_invoice(i, now=serialize_now) for i in inv_open)

        # Stable sort by (priority, due_date, -created_at). We sort twice
        # because Python's stable sort can't mix asc/desc on different keys
        # cleanly: first by created_at desc, then by priority/due asc.
        open_items.sort(key=lambda x: x.get("created_at") or "", reverse=True)
        open_items.sort(
            key=lambda x: (
                _PRIORITY_RANK_MAP.get(x.get("priority") or "", 4),
                x.get("due_date") or "9999-99-99",
            )
        )
        open_items = open_items[:limit]

        completed_items: list[dict[str, Any]] = []
        completed_items.extend(
            _serialize_dashboard_task(t, now=serialize_now) for t in db_completed_rows
        )
        completed_items.extend(
            _serialize_dashboard_task(t, now=serialize_now) for t in legacy_completed
        )
        completed_items.extend(
            _serialize_staff_request(r, now=serialize_now) for r in sr_completed
        )
        completed_items.extend(
            _serialize_invoice(i, now=serialize_now) for i in inv_completed
        )
        completed_items.sort(
            key=lambda x: x.get("updated_at") or "", reverse=True
        )
        completed_items = completed_items[:limit]

        # Granular breakdown — counted from the trimmed top-N already
        # serialised because computing exact OVERDUE / WAITING_ON / NEW
        # totals across the full open set would mean a second pass over
        # every Task / StaffRequest / Invoice. The header strip only
        # needs "is there at least one of these?" semantics; managers
        # who want the exact count click through to the bucket page.
        breakdown = {"overdue": 0, "waiting_on": 0, "escalated": 0, "new": 0}
        for it in open_items:
            ps = (it.get("pill_status") or "").upper()
            if ps == "OVERDUE":
                breakdown["overdue"] += 1
            elif ps == "WAITING_ON":
                breakdown["waiting_on"] += 1
            elif ps == "ESCALATED":
                breakdown["escalated"] += 1
            elif ps == "NEW":
                breakdown["new"] += 1

        data = {
            "bucket": bucket,
            "categories": list(wanted_cats),
            "items": open_items,
            "completed": completed_items,
            "counts": {
                "open": (
                    db_open_count
                    + legacy_open_count
                    + sr_open_count
                    + inv_open_count
                ),
                "in_progress": (
                    db_in_progress_count
                    + legacy_in_progress_count
                    + sr_in_progress_count
                    + inv_in_progress_count
                ),
                "completed": (
                    db_completed_count
                    + legacy_completed_count
                    + sr_completed_count
                    + inv_completed_count
                ),
                # Granular sub-counts driven by the items we already
                # serialised — at least gives the manager a yellow/red
                # signal in the header without a second DB pass. Treat
                # these as "≥ this many" (best-effort, not authoritative).
                "overdue": breakdown["overdue"],
                "waiting_on": breakdown["waiting_on"],
                "escalated": breakdown["escalated"],
                "new": breakdown["new"],
            },
            "generated_at": timezone.now().isoformat(),
        }

        return json_response_with_cache(
            request,
            data,
            max_age=30,
            private=True,
            stale_while_revalidate=60,
        )
