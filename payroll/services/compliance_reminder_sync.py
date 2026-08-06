"""Sync compliance document expiry rows to dashboard / WhatsApp personal reminders."""
from __future__ import annotations

import logging
import re
from datetime import date, datetime, time
from zoneinfo import ZoneInfo

from django.utils import timezone

logger = logging.getLogger(__name__)

DEFAULT_TZ = "Africa/Casablanca"
REMINDER_HOUR = 9


def _local_expiry_datetime(expires_at: date, tz_name: str = DEFAULT_TZ, hour: int = REMINDER_HOUR):
    tz = ZoneInfo(tz_name or DEFAULT_TZ)
    return datetime.combine(expires_at, time(hour, 0)).replace(tzinfo=tz)


def _reminder_title(doc) -> str:
    title = (getattr(doc, "title", None) or "Document").strip()
    if re.search(r"expiration reminder$", title, re.I):
        return title[:255]
    return f"{title} Expiration Reminder"[:255]


def _resolve_reminder_owner(doc, preferred=None):
    from accounts.models import CustomUser

    if preferred and getattr(preferred, "restaurant_id", None) == doc.restaurant_id:
        return preferred
    if doc.created_by_id and getattr(doc.created_by, "restaurant_id", None) == doc.restaurant_id:
        return doc.created_by
    return (
        CustomUser.objects.filter(
            restaurant=doc.restaurant,
            role__in=["OWNER", "MANAGER", "ADMIN"],
            is_active=True,
        )
        .exclude(phone__isnull=True)
        .exclude(phone="")
        .order_by("date_joined")
        .first()
    )


def sync_compliance_document_reminder(doc, *, owner=None, reset_nudges: bool = False) -> dict:
    """
    Ensure one pending PersonalReminder exists for a compliance document's expiry date.
    Dashboard Meetings & Reminders shows this row; approach nudges fire via Celery sweep.
    """
    from payroll.models import ComplianceDocument
    from scheduling.memory_models import PersonalReminder

    summary = {"created": 0, "updated": 0, "cancelled": 0, "skipped": 0}

    if doc.status == ComplianceDocument.STATUS_ARCHIVED or not doc.expires_at:
        cancelled = PersonalReminder.objects.filter(
            linked_compliance_document=doc,
            status="pending",
        ).update(status="cancelled")
        summary["cancelled"] = cancelled
        return summary

    owner = _resolve_reminder_owner(doc, preferred=owner)
    if not owner:
        summary["skipped"] = 1
        logger.info("compliance_reminder_sync: no owner for doc=%s", doc.id)
        return summary

    due_at = _local_expiry_datetime(doc.expires_at)
    phone = re.sub(r"\D", "", str(getattr(owner, "phone", "") or ""))
    body = (
        f"{doc.get_document_type_display()} expires {doc.expires_at.isoformat()}. "
        f"Miya will nudge you starting {doc.remind_days_before} days before."
    )

    rem = PersonalReminder.objects.filter(linked_compliance_document=doc).first()
    if rem:
        rem.title = _reminder_title(doc)
        rem.body = body
        rem.due_at = due_at
        rem.owner = owner
        rem.phone = phone[:40]
        rem.restaurant = doc.restaurant
        rem.status = "pending"
        if reset_nudges:
            rem.approach_nudges_sent = []
        rem.save()
        summary["updated"] = 1
        return summary

    # Link legacy reminder created by Miya before FK existed (title match).
    legacy = (
        PersonalReminder.objects.filter(
            restaurant=doc.restaurant,
            owner=owner,
            status="pending",
            linked_compliance_document__isnull=True,
            title__iexact=_reminder_title(doc),
        )
        .order_by("-created_at")
        .first()
    )
    if legacy:
        legacy.linked_compliance_document = doc
        legacy.body = body
        legacy.due_at = due_at
        legacy.phone = phone[:40]
        if reset_nudges:
            legacy.approach_nudges_sent = []
        legacy.save()
        summary["updated"] = 1
        return summary

    PersonalReminder.objects.create(
        restaurant=doc.restaurant,
        owner=owner,
        phone=phone[:40],
        title=_reminder_title(doc),
        body=body,
        due_at=due_at,
        timezone_name=DEFAULT_TZ,
        linked_compliance_document=doc,
        approach_nudges_sent=[],
    )
    summary["created"] = 1
    return summary


def sync_all_compliance_reminders_for_restaurant(restaurant) -> dict:
    from payroll.models import ComplianceDocument

    totals = {"created": 0, "updated": 0, "cancelled": 0, "skipped": 0}
    qs = ComplianceDocument.objects.filter(
        restaurant=restaurant,
        status=ComplianceDocument.STATUS_ACTIVE,
    ).exclude(expires_at__isnull=True)
    for doc in qs.iterator():
        row = sync_compliance_document_reminder(doc, reset_nudges=False)
        for k in totals:
            totals[k] += row.get(k, 0)
    return totals
