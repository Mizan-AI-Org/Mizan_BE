"""
Scheduled jobs for the Staff Requests inbox + compliance.

Two background sweeps run independently:

1. ``staff_request_sla_sweep`` — re-surfaces parked / aged requests:
   - ``WAITING_ON`` rows whose ``follow_up_date`` is today or earlier are
     bounced back to ``ESCALATED`` (or kept WAITING_ON if explicitly set
     in the past) and a system comment is added so the manager knows
     why they're seeing it again.
   - ``HIGH`` and ``URGENT`` rows that have been ``PENDING`` longer than
     the per-priority SLA get a single nudge comment (idempotent — we
     don't spam the same row every time the beat ticks).

2. ``compliance_renewal_sweep`` — opens HR staff_requests for staff
   certifications that expire within the next 30 days, so the
   compliance lane on the dashboard auto-fills before things actually
   lapse.

Both tasks are idempotent and tenant-scoped (one query per restaurant
to keep things tidy in the activity log).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Iterable

from celery import shared_task
from django.db.models import Q
from django.utils import timezone

logger = logging.getLogger(__name__)


# Per-priority SLAs in hours. Tuned so URGENT gets a nudge same-day and
# HIGH on the next morning. Any request older than this AND still
# ``PENDING`` (no manager touch) earns one nudge comment.
_SLA_HOURS = {
    "URGENT": 4,
    "HIGH": 24,
}


def _sla_marker_key(priority: str) -> str:
    """Metadata key used to dedupe SLA nudges per priority tier."""
    return f"sla_nudged_{priority.lower()}_at"


@shared_task(name="staff.tasks.staff_request_sla_sweep")
def staff_request_sla_sweep() -> dict:
    """
    Wake stale staff requests so they don't rot in the inbox.

    Returns a small summary dict for observability — the Celery flower
    UI / activity log surfaces this.
    """
    from staff.models import StaffRequest, StaffRequestComment

    now = timezone.now()
    today = now.date()
    summary = {"waiting_on_revived": 0, "sla_nudged_urgent": 0, "sla_nudged_high": 0}

    # ── Part 1: WAITING_ON rows whose follow-up date has arrived. ──
    overdue_qs = StaffRequest.objects.filter(
        status="WAITING_ON",
        follow_up_date__isnull=False,
        follow_up_date__lte=today,
    )
    for req in overdue_qs.iterator(chunk_size=200):
        try:
            req.status = "ESCALATED"
            req.save(update_fields=["status", "updated_at"])
            StaffRequestComment.objects.create(
                request=req,
                author=None,  # system
                kind="status_change",
                body=(
                    f"⏰ Follow-up date reached ({req.follow_up_date.isoformat()})"
                    + (f" — was waiting on {req.waiting_reason}" if req.waiting_reason else "")
                    + ". Re-escalated for manager review."
                ),
                metadata={
                    "from": "WAITING_ON",
                    "to": "ESCALATED",
                    "follow_up_date": req.follow_up_date.isoformat(),
                    "trigger": "sla_sweep",
                },
            )
            summary["waiting_on_revived"] += 1
        except Exception:
            logger.exception("SLA sweep: failed to revive request %s", req.pk)

    # ── Part 2: long-PENDING URGENT/HIGH that haven't been nudged yet. ──
    for priority, hours in _SLA_HOURS.items():
        cutoff = now - timedelta(hours=hours)
        marker = _sla_marker_key(priority)
        # Each row only earns one nudge per priority tier — we mark the
        # metadata when we ping so subsequent runs short-circuit.
        candidates = StaffRequest.objects.filter(
            status="PENDING",
            priority=priority,
            created_at__lte=cutoff,
        ).exclude(metadata__has_key=marker) if hasattr(StaffRequest._meta.get_field("metadata"), "has_key") else \
            StaffRequest.objects.filter(
                status="PENDING",
                priority=priority,
                created_at__lte=cutoff,
            )

        for req in candidates.iterator(chunk_size=200):
            try:
                # ``has_key`` lookup isn't portable across Postgres versions
                # so re-check the marker in Python before mutating.
                md = dict(req.metadata or {})
                if marker in md:
                    continue
                age_hours = int((now - req.created_at).total_seconds() // 3600)
                StaffRequestComment.objects.create(
                    request=req,
                    author=None,
                    kind="system",
                    body=(
                        f"⏰ SLA nudge — this {priority.lower()} request has been "
                        f"pending for {age_hours}h and still has no owner action."
                    ),
                    metadata={"trigger": "sla_sweep", "age_hours": age_hours},
                )
                md[marker] = now.isoformat()
                req.metadata = md
                req.save(update_fields=["metadata", "updated_at"])
                summary[f"sla_nudged_{priority.lower()}"] += 1
            except Exception:
                logger.exception("SLA sweep: failed to nudge request %s", req.pk)

    if any(summary.values()):
        logger.info("staff_request_sla_sweep summary: %s", summary)
    return summary


def _certs_expiring_soon(certifications) -> Iterable[dict]:
    """Yield certifications whose expiry lands inside the next 30 days."""
    if not isinstance(certifications, list):
        return
    horizon = date.today() + timedelta(days=30)
    today = date.today()
    for c in certifications:
        if not isinstance(c, dict):
            continue
        raw = c.get("expiry") or c.get("expiry_date")
        if not raw:
            continue
        try:
            exp = date.fromisoformat(str(raw)[:10])
        except ValueError:
            continue
        if today <= exp <= horizon:
            yield {**c, "_parsed_expiry": exp}


@shared_task(name="staff.tasks.compliance_renewal_sweep")
def compliance_renewal_sweep() -> dict:
    """
    Open HR/DOCUMENT staff_requests for certifications expiring soon.

    De-duplicated via ``external_id = "cert-renewal:<staff_id>:<cert_name>:<expiry>"``
    so re-running the sweep doesn't pile up duplicate inbox rows.
    """
    from accounts.models import Restaurant
    from staff.models import StaffProfile, StaffRequest

    summary = {"opened": 0, "scanned": 0}

    for profile in StaffProfile.objects.select_related("user", "user__restaurant").iterator(chunk_size=200):
        user = profile.user
        if not user or not user.is_active or not getattr(user, "restaurant_id", None):
            continue
        for cert in _certs_expiring_soon(profile.certifications):
            summary["scanned"] += 1
            cert_name = (cert.get("name") or cert.get("title") or "Certification").strip()[:120]
            exp = cert["_parsed_expiry"]
            ext_id = f"cert-renewal:{user.pk}:{cert_name}:{exp.isoformat()}"
            if StaffRequest.objects.filter(external_id=ext_id).exists():
                continue
            days_left = (exp - date.today()).days
            try:
                StaffRequest.objects.create(
                    restaurant_id=user.restaurant_id,
                    staff=user,
                    staff_name=user.get_full_name() or user.email or "",
                    staff_phone=getattr(user, "phone", "") or "",
                    category="HR",
                    priority="HIGH" if days_left <= 7 else "MEDIUM",
                    status="PENDING",
                    subject=f"Certification renewal: {cert_name}",
                    description=(
                        f"{user.get_full_name() or user.email}'s {cert_name} expires "
                        f"on {exp.isoformat()} ({days_left} day{'s' if days_left != 1 else ''} from today). "
                        "Renew it before the expiry date to stay compliant."
                    ),
                    source="compliance_sweep",
                    external_id=ext_id,
                    metadata={
                        "trigger": "compliance_sweep",
                        "certification": cert_name,
                        "expiry": exp.isoformat(),
                        "days_left": days_left,
                    },
                )
                summary["opened"] += 1
            except Exception:
                logger.exception(
                    "compliance_renewal_sweep: failed to open request for staff=%s cert=%s",
                    user.pk,
                    cert_name,
                )

    if summary["opened"]:
        logger.info("compliance_renewal_sweep opened %d renewals", summary["opened"])
    return summary
