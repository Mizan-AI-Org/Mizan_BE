"""Import restaurant compliance documents from parsed uploads (WhatsApp / Miya)."""
from __future__ import annotations

import logging
import re
from datetime import date
from typing import Any

from django.core.files.base import ContentFile
from django.utils.dateparse import parse_date

from payroll.services.compliance_documents import DOCUMENT_TYPE_IDS, STARTER_DOCUMENTS

logger = logging.getLogger(__name__)

_MANAGER_ROLES = frozenset({"OWNER", "MANAGER", "ADMIN"})

_COMPLIANCE_CATEGORIES = frozenset({"id_or_certification", "contract", "other"})

_RESTAURANT_KEYWORDS = (
    "insurance",
    "assurance",
    "liability",
    "property policy",
    "business registration",
    "registre",
    "patente",
    "hygiene",
    "haccp",
    "food safety",
    "health permit",
    "fire extinguisher",
    "extinguisher",
    "liquor license",
    "operating permit",
    "certificate of occupancy",
    "conformité",
    "conformite",
    "permis",
    "licence",
    "license",
    "renouvel",
    "renew",
)

_REMINDER_KEYWORDS = (
    "remind",
    "rappel",
    "rappelle",
    "notify",
    "alert",
    "before expiry",
    "before expiration",
    "avant expiration",
    "avant l'expiration",
    "avant la date",
    "renew",
    "renouvel",
)

_STARTER_TITLES = {tpl["title"].lower() for tpl in STARTER_DOCUMENTS}
_DEFAULT_REMIND_BY_TYPE = {tpl["document_type"]: tpl["remind_days_before"] for tpl in STARTER_DOCUMENTS}


def _is_manager(user) -> bool:
    return (getattr(user, "role", None) or "") in _MANAGER_ROLES


def parse_remind_days_before(text: str, *, default: int = 30) -> int:
    """Parse natural-language lead time, e.g. '2 weeks before expiry' → 14."""
    if not text or not str(text).strip():
        return max(1, min(365, default))

    lower = str(text).lower()
    lower = lower.replace("’", "'")

    word_weeks = {
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "une": 1,
        "deux": 2,
        "trois": 3,
        "quatre": 4,
    }
    for word, n in word_weeks.items():
        if re.search(rf"\b{word}\s+weeks?\b", lower):
            return max(1, min(365, n * 7))
        if re.search(rf"\b{word}\s+semaines?\b", lower):
            return max(1, min(365, n * 7))

    for pattern in (
        r"(\d+)\s*weeks?\s*(?:before|prior|avant|to|until|till)?",
        r"(\d+)\s*semaines?\s*(?:avant|before|prior)?",
        r"(?:remind|notify|rappel(?:le|er)?).*?(\d+)\s*weeks?",
        r"(?:remind|notify|rappel(?:le|er)?).*?(\d+)\s*semaines?",
    ):
        m = re.search(pattern, lower)
        if m:
            return max(1, min(365, int(m.group(1)) * 7))

    for pattern in (
        r"(\d+)\s*months?\s*(?:before|prior|avant|to|until|till)?",
        r"(\d+)\s*mois\s*(?:avant|before|prior)?",
        r"(?:remind|notify|rappel(?:le|er)?).*?(\d+)\s*months?",
        r"(?:remind|notify|rappel(?:le|er)?).*?(\d+)\s*mois",
    ):
        m = re.search(pattern, lower)
        if m:
            return max(1, min(365, int(m.group(1)) * 30))

    for pattern in (
        r"(\d+)\s*days?\s*(?:before|prior|avant|to|until|till)?",
        r"(\d+)\s*jours?\s*(?:avant|before|prior)?",
        r"(?:remind|notify|rappel(?:le|er)?).*?(\d+)\s*days?",
        r"(?:remind|notify|rappel(?:le|er)?).*?(\d+)\s*jours?",
    ):
        m = re.search(pattern, lower)
        if m:
            return max(1, min(365, int(m.group(1))))

    if any(k in lower for k in _REMINDER_KEYWORDS):
        return max(1, min(365, default))

    return max(1, min(365, default))


def infer_document_type(
    fields: dict[str, Any],
    title: str,
    note: str = "",
    filename: str = "",
) -> str:
    """Map parsed fields / caption to ComplianceDocument.document_type."""
    raw = str(fields.get("document_type") or fields.get("type") or "").upper().replace(" ", "_")
    if raw in DOCUMENT_TYPE_IDS:
        return raw

    combined = " ".join(
        [
            str(fields.get("document_type") or ""),
            str(fields.get("title") or ""),
            title or "",
            note or "",
            filename or "",
        ]
    ).lower()

    if any(k in combined for k in ("insur", "assurance")):
        return "INSURANCE"
    if any(k in combined for k in ("hygien", "food safety", "haccp", "hygiène")):
        return "HYGIENE"
    if any(k in combined for k in ("extinguish", "fire")):
        return "FIRE_EXTINGUISHER"
    if any(k in combined for k in ("health permit", "health authority", "permis sanitaire")):
        return "HEALTH_PERMIT"
    if any(k in combined for k in ("liquor", "alcool")):
        return "LIQUOR_LICENSE"
    if any(k in combined for k in ("regist", "patente", "commerce", "registration")):
        return "BUSINESS_REGISTRATION"
    if any(k in combined for k in ("equipment", "inspection tag")):
        return "EQUIPMENT_INSPECTION"
    return "OTHER"


def _pick_title(
    fields: dict[str, Any],
    note: str,
    filename: str,
    summary: str,
) -> str:
    for key in ("title", "document_title", "document_type"):
        val = fields.get(key)
        if isinstance(val, str) and val.strip():
            return val.strip()[:255]
    if note and len(note) <= 120 and not re.search(r"\b(remind|rappel|renew|renouvel)\b", note, re.I):
        return note.strip()[:255]
    if summary and len(summary) <= 120:
        return summary.strip()[:255]
    base = (filename or "Compliance document").rsplit("/", 1)[-1]
    if "." in base:
        base = base.rsplit(".", 1)[0]
    return (base or "Compliance document")[:255]


def _parse_expiry(fields: dict[str, Any], note: str) -> date | None:
    for key in ("expiry_date", "expires_at", "due_date", "expiration_date"):
        raw = fields.get(key)
        if raw:
            parsed = parse_date(str(raw))
            if parsed:
                return parsed
    # Optional ISO date in caption (manager typed it)
    m = re.search(r"\b(20\d{2}-\d{2}-\d{2})\b", note or "")
    if m:
        return parse_date(m.group(1))
    return None


def should_track_as_restaurant_compliance(
    *,
    classification: dict[str, Any],
    note: str = "",
    acting_user=None,
    min_confidence: float = 0.45,
) -> bool:
    """True when an upload should land in Settings → Documents de conformité."""
    if not acting_user or not _is_manager(acting_user):
        return False

    category = str(classification.get("category") or "other")
    if category not in _COMPLIANCE_CATEGORIES:
        return False

    confidence = float(classification.get("confidence") or 0.0)
    if confidence < min_confidence:
        return False

    fields = classification.get("fields") if isinstance(classification.get("fields"), dict) else {}
    person = (fields.get("person_name") or "").strip()
    note_l = (note or "").lower()
    combined = " ".join(
        [
            str(fields.get("document_type") or ""),
            str(fields.get("title") or ""),
            note_l,
        ]
    ).lower()

    if any(k in note_l for k in _REMINDER_KEYWORDS):
        return True

    if any(k in combined for k in _RESTAURANT_KEYWORDS):
        return True

    if fields.get("expiry_date") and not person:
        return True

    # Business policy / lease with expiry — restaurant-level, not staff HR.
    if category == "contract" and fields.get("expiry_date") and not person:
        return True

    return False


def _default_remind_for_type(document_type: str) -> int:
    return int(_DEFAULT_REMIND_BY_TYPE.get(document_type) or 30)


def _find_existing_doc(restaurant, document_type: str, title: str):
    from payroll.models import ComplianceDocument

    qs = ComplianceDocument.objects.filter(
        restaurant=restaurant,
        status=ComplianceDocument.STATUS_ACTIVE,
        document_type=document_type,
    )
    if qs.count() == 1:
        return qs.first()

    title_l = (title or "").strip().lower()
    if title_l:
        for doc in qs:
            if doc.title.strip().lower() == title_l:
                return doc
        for doc in qs:
            if doc.title.strip().lower() in _STARTER_TITLES and title_l in _STARTER_TITLES:
                return doc
    return None


def format_compliance_confirmation(doc, *, created: bool, language: str = "en") -> str:
    from core.i18n import tr

    lang = language or "en"
    dtype = doc.get_document_type_display()
    if doc.expires_at:
        return tr(
            "compliance.upload.tracked_with_expiry",
            lang,
            title=doc.title,
            doc_type=dtype,
            expiry=doc.expires_at.isoformat(),
            remind_days=doc.remind_days_before,
        )
    return tr(
        "compliance.upload.tracked_no_expiry",
        lang,
        title=doc.title,
        doc_type=dtype,
    )


def try_create_compliance_from_classification(
    *,
    restaurant,
    acting_user,
    classification: dict[str, Any],
    file_bytes: bytes | None = None,
    filename: str = "",
    content_type: str = "",
    note: str = "",
) -> tuple[Any | None, str]:
    """
    Create or update a ComplianceDocument from parse_document / parse_photo output.
    Returns (document, message_for_user) or (None, "") when not applicable.
    """
    if not should_track_as_restaurant_compliance(
        classification=classification,
        note=note,
        acting_user=acting_user,
    ):
        return None, ""

    from payroll.models import ComplianceDocument
    from payroll.services.compliance_reminder_sync import sync_compliance_document_reminder

    fields = classification.get("fields") if isinstance(classification.get("fields"), dict) else {}
    summary = str(classification.get("summary") or "").strip()
    title = _pick_title(fields, note, filename, summary)
    document_type = infer_document_type(fields, title, note, filename)
    expires_at = _parse_expiry(fields, note)
    remind_days = parse_remind_days_before(
        note,
        default=_default_remind_for_type(document_type),
    )

    doc = _find_existing_doc(restaurant, document_type, title)
    created = doc is None
    if created:
        doc = ComplianceDocument(
            restaurant=restaurant,
            title=title[:255],
            document_type=document_type,
            created_by=acting_user,
        )
    elif title and doc.title.strip().lower() in _STARTER_TITLES and title.strip().lower() not in _STARTER_TITLES:
        doc.title = title[:255]

    if expires_at:
        doc.expires_at = expires_at
        doc.status = ComplianceDocument.STATUS_ACTIVE
        doc.last_notified_at = None
    doc.remind_days_before = remind_days
    if summary and not doc.description:
        doc.description = summary[:2000]
    ref = fields.get("reference_number") or fields.get("policy_number") or fields.get("invoice_number")
    if ref and not doc.reference_number:
        doc.reference_number = str(ref)[:128]

    doc.save()

    if file_bytes and filename:
        safe_name = filename.rsplit("/", 1)[-1] or "compliance-upload.bin"
        try:
            doc.file.save(safe_name, ContentFile(file_bytes), save=True)
        except Exception:
            logger.exception("compliance_import: file save failed doc=%s", doc.id)

    sync_compliance_document_reminder(doc, owner=acting_user, reset_nudges=True)

    from core.i18n import get_effective_language

    lang = get_effective_language(user=acting_user, restaurant=restaurant)
    message = format_compliance_confirmation(doc, created=created, language=lang)
    return doc, message
