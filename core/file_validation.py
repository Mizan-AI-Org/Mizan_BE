"""Upload validation for Mizan documents (invoices, HR, incidents)."""
from __future__ import annotations

ALLOWED_DOCUMENT_CONTENT_TYPES = frozenset(
    {
        "application/pdf",
        "image/jpeg",
        "image/jpg",
        "image/png",
        "image/webp",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
)

ALLOWED_DOCUMENT_EXTENSIONS = frozenset(
    {".pdf", ".jpg", ".jpeg", ".png", ".webp", ".docx", ".xlsx"}
)

MAX_DOCUMENT_SIZE_BYTES = 20 * 1024 * 1024


def validate_document_upload(
    *,
    content_type: str | None,
    filename: str | None,
    size: int,
) -> str | None:
    """Return an error message when invalid; otherwise None."""
    if size <= 0:
        return "Empty file."
    if size > MAX_DOCUMENT_SIZE_BYTES:
        return f"File exceeds {MAX_DOCUMENT_SIZE_BYTES // (1024 * 1024)} MB limit."

    ct = (content_type or "").split(";")[0].strip().lower()
    if ct and ct not in ALLOWED_DOCUMENT_CONTENT_TYPES:
        return f"Unsupported file type: {ct}."

    name = (filename or "").lower()
    if name and "." in name:
        ext = "." + name.rsplit(".", 1)[-1]
        if ext not in ALLOWED_DOCUMENT_EXTENSIONS:
            return f"Unsupported file extension: {ext}."

    return None
