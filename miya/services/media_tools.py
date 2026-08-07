"""Resolve uploaded media for Miya parse_photo / parse_document tools."""

from __future__ import annotations

import base64
import logging
from typing import Any

from django.core.files.uploadedfile import SimpleUploadedFile

logger = logging.getLogger(__name__)


def _decode_base64(raw: str) -> tuple[bytes | None, str]:
    text = (raw or "").strip()
    if not text:
        return None, ""
    mime = "application/octet-stream"
    if text.startswith("data:"):
        header, _, payload = text.partition(",")
        if ";" in header:
            mime = header.split(";", 1)[0].replace("data:", "").strip() or mime
        text = payload
    try:
        return base64.b64decode(text, validate=False), mime
    except Exception:
        return None, mime


def resolve_media_bytes(
    arguments: dict[str, Any],
    session_context: dict[str, Any],
) -> tuple[bytes | None, str, str]:
    """
    Return (blob, mime_type, filename).

    Supports document_id (TenantDocument), media_url/image_url/document_url,
    image_base64/document_base64.
    """
    args = dict(arguments or {})
    rid = str(session_context.get("restaurant_id") or args.get("restaurant_id") or "").strip()

    doc_id = str(args.get("document_id") or args.get("tenant_document_id") or "").strip()
    if doc_id and rid:
        from miya.models import TenantDocument

        doc = TenantDocument.objects.filter(restaurant_id=rid, id=doc_id).first()
        if doc and doc.file:
            try:
                with doc.file.open("rb") as fh:
                    blob = fh.read()
                mime = doc.mime_type or "application/octet-stream"
                name = doc.original_filename or doc.title or "upload"
                return blob, mime, name
            except Exception as exc:
                logger.warning("resolve_media_bytes tenant doc read failed: %s", exc)
        if doc and doc.file_url:
            from core.media_fetch import fetch_remote_media_bytes

            blob, mime = fetch_remote_media_bytes(doc.file_url)
            if blob:
                return blob, mime or doc.mime_type or "application/octet-stream", doc.original_filename or "upload"

    url = str(
        args.get("media_url")
        or args.get("image_url")
        or args.get("document_url")
        or args.get("file_url")
        or ""
    ).strip()
    if url:
        from core.media_fetch import fetch_remote_media_bytes

        blob, mime = fetch_remote_media_bytes(url)
        if blob:
            name = url.rsplit("/", 1)[-1].split("?", 1)[0] or "upload"
            return blob, mime or "application/octet-stream", name

    b64 = str(args.get("image_base64") or args.get("document_base64") or args.get("base64") or "").strip()
    if b64:
        blob, mime = _decode_base64(b64)
        if blob:
            is_image = mime.startswith("image/") or bool(args.get("image_base64"))
            name = "photo.jpg" if is_image else "document.pdf"
            return blob, mime or ("image/jpeg" if is_image else "application/pdf"), name

    return None, "", ""


def dispatch_parse_photo(
    arguments: dict[str, Any],
    session_context: dict[str, Any],
    *,
    headers: dict[str, str],
) -> tuple[int, Any]:
    from miya.services.tool_dispatch import dispatch_agent_request, dispatch_multipart_agent_request

    args = dict(arguments or {})
    rid = session_context.get("restaurant_id")
    form: dict[str, Any] = {
        "restaurant_id": str(rid or args.get("restaurant_id") or ""),
        "note": str(args.get("note") or ""),
    }
    if args.get("auto_create") not in (None, "", False, "false", "0"):
        logger.info("parse_photo tool: auto_create ignored (extraction-only)")
    url = str(args.get("media_url") or args.get("image_url") or "").strip()
    if url:
        form["image_url"] = url
        return dispatch_agent_request("POST", "/api/dashboard/agent/parse-photo/", json_payload=form, headers=headers)

    blob, mime, filename = resolve_media_bytes(args, session_context)
    if not blob:
        return 400, {
            "success": False,
            "error": "missing_media",
            "message_for_user": (
                "Attach a photo, send a document_id from a recent upload, or paste an image URL."
            ),
        }

    upload = SimpleUploadedFile(filename, blob, content_type=mime or "image/jpeg")
    return dispatch_multipart_agent_request(
        "POST",
        "/api/dashboard/agent/parse-photo/",
        form_data=form,
        files={"image": upload},
        headers=headers,
    )


def dispatch_parse_document(
    arguments: dict[str, Any],
    session_context: dict[str, Any],
    *,
    headers: dict[str, str],
) -> tuple[int, Any]:
    from miya.services.tool_dispatch import dispatch_multipart_agent_request

    args = dict(arguments or {})
    rid = session_context.get("restaurant_id")
    form: dict[str, Any] = {
        "restaurant_id": str(rid or args.get("restaurant_id") or ""),
        "note": str(args.get("note") or ""),
    }
    if args.get("auto_create") not in (None, "", False, "false", "0"):
        logger.info("parse_photo tool: auto_create ignored (extraction-only)")
    if args.get("import_processes") not in (None, "", False, "false", "0"):
        logger.info("parse_document tool: import_processes ignored (preview-only on agent path)")

    blob, mime, filename = resolve_media_bytes(args, session_context)
    if not blob:
        return 400, {
            "success": False,
            "error": "missing_media",
            "message_for_user": (
                "Attach the PDF or document, or give me the document_id from your recent upload."
            ),
        }

    upload = SimpleUploadedFile(filename, blob, content_type=mime or "application/pdf")
    return dispatch_multipart_agent_request(
        "POST",
        "/api/dashboard/agent/parse-document/",
        form_data=form,
        files={"document": upload},
        headers=headers,
    )
