"""Organization-scoped S3 / media upload paths for Mizan.

Mizan tenants are ``Restaurant`` rows; S3 prefixes use ``organizations/{id}/``
so each workspace's files stay isolated inside one private bucket.
"""
from __future__ import annotations

import os
import uuid


def _org_prefix(restaurant_id, category: str) -> str:
    rid = restaurant_id or "unknown"
    return f"organizations/{rid}/{category.strip('/')}"


def _extension(filename: str) -> str:
    return os.path.splitext(filename or "")[1].lower()


def organization_upload_path(instance, filename, *, category: str) -> str:
    """Generic org-scoped path with a UUID filename."""
    restaurant_id = getattr(instance, "restaurant_id", None)
    ext = _extension(filename)
    unique = f"{uuid.uuid4()}{ext}" if ext else str(uuid.uuid4())
    return f"{_org_prefix(restaurant_id, category)}/{unique}"


def invoice_upload_path(instance, filename):
    """Original invoice scan (image or PDF)."""
    restaurant_id = getattr(instance, "restaurant_id", None)
    invoice_id = instance.pk or uuid.uuid4()
    ext = _extension(filename) or ".bin"
    return f"{_org_prefix(restaurant_id, 'invoices')}/{invoice_id}/original{ext}"


def invoice_photo_upload_path(instance, filename):
    """Invoice snapshot image (same folder layout as attachment)."""
    return invoice_upload_path(instance, filename)


def payment_proof_upload_path(instance, filename):
    """Proof-of-payment receipt / transfer confirmation."""
    restaurant_id = getattr(instance, "restaurant_id", None)
    invoice_id = getattr(instance, "pk", None) or uuid.uuid4()
    ext = _extension(filename) or ".bin"
    return (
        f"{_org_prefix(restaurant_id, 'payment-proofs')}/{invoice_id}/"
        f"{uuid.uuid4()}{ext}"
    )


def incident_photo_upload_path(instance, filename):
    """Safety / incident evidence photo."""
    restaurant_id = getattr(instance, "restaurant_id", None)
    incident_id = getattr(instance, "pk", None) or uuid.uuid4()
    ext = _extension(filename) or ".jpg"
    return (
        f"{_org_prefix(restaurant_id, 'incidents')}/{incident_id}/"
        f"{uuid.uuid4()}{ext}"
    )


def incident_attachment_upload_path(instance, filename):
    """Non-image incident evidence (PDF, etc.)."""
    return incident_photo_upload_path(instance, filename)


def task_attachment_upload_path(instance, filename):
    """Dashboard task inbound attachment."""
    restaurant_id = getattr(instance, "restaurant_id", None)
    task_id = getattr(instance, "pk", None) or uuid.uuid4()
    ext = _extension(filename) or ".bin"
    return (
        f"{_org_prefix(restaurant_id, 'tasks')}/{task_id}/"
        f"{uuid.uuid4()}{ext}"
    )


def reminder_attachment_upload_path(instance, filename):
    """Personal reminder attachment."""
    restaurant_id = getattr(instance, "restaurant_id", None)
    reminder_id = getattr(instance, "pk", None) or uuid.uuid4()
    ext = _extension(filename) or ".bin"
    return (
        f"{_org_prefix(restaurant_id, 'reminders')}/{reminder_id}/"
        f"{uuid.uuid4()}{ext}"
    )


def org_media_folder(restaurant_id, category: str) -> str:
    """Build a folder prefix for programmatic uploads (WhatsApp re-hosting)."""
    return _org_prefix(restaurant_id, category)
