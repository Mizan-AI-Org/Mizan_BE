"""Restaurant-level compliance documents (insurance, hygiene, extinguishers, etc.)."""
from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from django.utils import timezone

# Suggested starter set — managers set expiry dates; Miya reminds before they lapse.
DOCUMENT_TYPE_CHOICES = (
    ("BUSINESS_REGISTRATION", "Business registration"),
    ("INSURANCE", "Insurance"),
    ("FIRE_EXTINGUISHER", "Fire extinguisher inspection"),
    ("HYGIENE", "Hygiene / food safety certificate"),
    ("HEALTH_PERMIT", "Health permit"),
    ("LIQUOR_LICENSE", "Liquor license"),
    ("EQUIPMENT_INSPECTION", "Equipment inspection"),
    ("OTHER", "Other"),
)

DOCUMENT_TYPE_IDS = {c[0] for c in DOCUMENT_TYPE_CHOICES}

STARTER_DOCUMENTS: list[dict[str, Any]] = [
    {
        "document_type": "BUSINESS_REGISTRATION",
        "title": "Business registration",
        "description": "Company / trade register renewal (e.g. patente, registre de commerce).",
        "remind_days_before": 60,
    },
    {
        "document_type": "INSURANCE",
        "title": "Business insurance",
        "description": "Liability / property insurance policy renewal.",
        "remind_days_before": 45,
    },
    {
        "document_type": "FIRE_EXTINGUISHER",
        "title": "Fire extinguisher inspection",
        "description": "Annual extinguisher / fire-safety inspection tag.",
        "remind_days_before": 30,
    },
    {
        "document_type": "HYGIENE",
        "title": "Hygiene / food safety certificate",
        "description": "Hygiene rating or food-safety certification renewal.",
        "remind_days_before": 45,
    },
    {
        "document_type": "HEALTH_PERMIT",
        "title": "Health permit",
        "description": "Municipal or health-authority operating permit.",
        "remind_days_before": 60,
    },
]


def days_until(expires_at: date | None, today: date | None = None) -> int | None:
    if not expires_at:
        return None
    today = today or timezone.now().date()
    return (expires_at - today).days


def document_urgency(expires_at: date | None, today: date | None = None) -> str:
    """Return expired | critical | soon | ok | unset."""
    d = days_until(expires_at, today)
    if d is None:
        return "unset"
    if d < 0:
        return "expired"
    if d <= 7:
        return "critical"
    if d <= 30:
        return "soon"
    return "ok"


def serialize_document(doc) -> dict[str, Any]:
    today = timezone.now().date()
    dleft = days_until(getattr(doc, "expires_at", None), today)
    return {
        "id": str(doc.id),
        "title": doc.title,
        "document_type": doc.document_type,
        "description": doc.description or "",
        "expires_at": doc.expires_at.isoformat() if doc.expires_at else None,
        "days_until_expiry": dleft,
        "urgency": document_urgency(doc.expires_at, today),
        "remind_days_before": doc.remind_days_before,
        "status": doc.status,
        "reference_number": doc.reference_number or "",
        "last_notified_at": doc.last_notified_at.isoformat() if doc.last_notified_at else None,
        "created_at": doc.created_at.isoformat() if doc.created_at else None,
    }


def seed_starter_documents(restaurant) -> list:
    """Create suggested compliance docs (no expiry yet) if missing for this restaurant."""
    from payroll.models import ComplianceDocument

    created = []
    for tpl in STARTER_DOCUMENTS:
        exists = ComplianceDocument.objects.filter(
            restaurant=restaurant,
            document_type=tpl["document_type"],
            status=ComplianceDocument.STATUS_ACTIVE,
        ).exists()
        if exists:
            continue
        row = ComplianceDocument.objects.create(
            restaurant=restaurant,
            document_type=tpl["document_type"],
            title=tpl["title"],
            description=tpl["description"],
            remind_days_before=tpl["remind_days_before"],
            expires_at=None,
        )
        created.append(row)
    return created


def documents_needing_attention(restaurant, within_days: int = 60):
    """Active docs missing a date, expired, or expiring within within_days."""
    from payroll.models import ComplianceDocument

    today = timezone.now().date()
    horizon = today + timedelta(days=within_days)
    qs = ComplianceDocument.objects.filter(
        restaurant=restaurant,
        status=ComplianceDocument.STATUS_ACTIVE,
    )
    out = []
    for doc in qs:
        if doc.expires_at is None:
            out.append(doc)
        elif doc.expires_at <= horizon:
            out.append(doc)
    out.sort(key=lambda d: (d.expires_at is None, d.expires_at or today))
    return out
