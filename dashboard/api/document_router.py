"""
Document-to-action router endpoint.

Sibling of ``photo_router`` for non-image attachments.

POST /api/dashboard/agent/parse-document/
   document       required (file)   — PDF / DOCX / XLSX / CSV / TXT
   auto_create    IGNORED (Phase 14.2 — extraction only)
   import_processes IGNORED (Phase 14.2.1 — preview only; use explicit import endpoint)
   note           optional manager-supplied caption

Phase 14.2.1: This agent endpoint is EXTRACTION + PREVIEW only.
TaskTemplate creation requires explicit ``agent_import_process_templates`` or Miya control plane.
"""
from __future__ import annotations

import logging
from typing import Any

from rest_framework import permissions, status
from rest_framework.decorators import (
    api_view,
    authentication_classes,
    parser_classes,
    permission_classes,
)
from rest_framework.parsers import FormParser, MultiPartParser
from rest_framework.response import Response

from scheduling.document_router_service import parse_document
from scheduling.process_template_import_service import (
    looks_like_process_import,
    parse_process_templates_from_bytes,
)

from .photo_router import _action_envelope

logger = logging.getLogger(__name__)


@api_view(["POST"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
@parser_classes([MultiPartParser, FormParser])
def agent_parse_document(request):
    """Classify an uploaded non-image document and (optionally) take action."""
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _acting_user, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    doc_file = (
        request.FILES.get("document")
        or request.FILES.get("file")
        or request.FILES.get("attachment")
    )
    if not doc_file:
        return Response(
            {
                "success": False,
                "error": "Missing document",
                "message_for_user": (
                    "Attach the PDF / Word / Excel / CSV file and I'll read it."
                ),
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    content_type = (getattr(doc_file, "content_type", "") or "").lower()
    name = getattr(doc_file, "name", "") or ""

    if content_type.startswith("image/"):
        # Wrong endpoint — guide Miya back to parse_photo. We don't try to be
        # clever and re-route silently because the agent contract should be
        # explicit about which tool was actually called.
        return Response(
            {
                "success": False,
                "status": "wrong_tool",
                "code": "USE_PARSE_PHOTO",
                "error": f"parse_document doesn't handle images (got {content_type})",
                "miya_directive": (
                    "This is an image. Call parse_photo with the same file instead. "
                    "Do NOT pretend you parsed it."
                ),
                "message_for_user": (
                    "That looks like a photo — let me run the photo reader on it instead."
                ),
            },
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    note = str(request.data.get("note") or "").strip()

    # Phase 14.2 hardening (D5): agent/LLM path must never mutate via auto_create.
    if request.data.get("auto_create") is not None:
        logger.info(
            "parse_document: auto_create ignored (extraction-only) restaurant=%s",
            getattr(restaurant, "id", ""),
        )

    # Phase 14.2.1: import_processes on agent path is preview-only — never bulk-create here.
    wants_process_preview = False
    if request.data.get("import_processes") is not None:
        logger.info(
            "parse_document: import_processes ignored for mutation (preview-only) restaurant=%s",
            getattr(restaurant, "id", ""),
        )
        wants_process_preview = str(request.data.get("import_processes")).strip().lower() not in (
            "0", "false", "no", "off", "",
        )

    try:
        blob = doc_file.read()
    except Exception:
        return Response(
            {"success": False, "error": "Could not read uploaded file"},
            status=status.HTTP_400_BAD_REQUEST,
        )

    classification = parse_document(blob, content_type=content_type, name=name)

    if not wants_process_preview:
        wants_process_preview = looks_like_process_import(note, name)
    if not wants_process_preview:
        wants_process_preview = classification.get("category") == "process_checklist"

    # Process/checklist documents → structured preview only (no TaskTemplate mutation).
    if wants_process_preview:
        parsed = parse_process_templates_from_bytes(
            blob, content_type=content_type, name=name, note=note,
        )
        templates = parsed.get("templates") or []
        if templates:
            names = ", ".join(t["name"] for t in templates[:5])
            extra = f" (+{len(templates) - 5} more)" if len(templates) > 5 else ""
            msg = (
                f"I found {len(templates)} process checklist(s): {names}{extra}. "
                "Tell me when you want to import them and I'll add them to Templates."
            )
            action_envelope = _action_envelope(
                type_="process_import_preview",
                record_id=None,
                message=msg,
                extra={
                    "template_count": len(templates),
                    "template_names": [t["name"] for t in templates],
                    "preview_only": True,
                },
            )
            return Response(
                {
                    "success": True,
                    "classification": classification,
                    "action_taken": action_envelope,
                    "message_for_user": msg,
                    "process_preview": {
                        "templates": templates,
                        "template_count": len(templates),
                        "extracted_kind": parsed.get("extracted_kind"),
                        "preview_only": True,
                    },
                },
                status=status.HTTP_200_OK,
            )

    # Hard-error paths — bubble up to Miya so she asks the user for fields
    # rather than fabricating them.
    if classification.get("error") == "unsupported_document_type":
        return Response(
            {
                "success": False,
                "status": "unsupported",
                "code": "UNSUPPORTED_DOCUMENT_TYPE",
                "classification": classification,
                "miya_directive": (
                    f"I can't read content_type={content_type or 'unknown'}. "
                    "Ask the user to type out the key fields (vendor, amount, due_date, "
                    "invoice_number) and then call record_invoice with the values they confirm. "
                    "Never invent fields."
                ),
                "message_for_user": (
                    "I can't read that file format directly. Tell me the vendor, the amount, "
                    "the due date and the invoice number and I'll log it in Finance."
                ),
            },
            status=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
        )

    if classification.get("error") == "empty_extraction":
        return Response(
            {
                "success": False,
                "status": "empty",
                "code": "EMPTY_DOCUMENT",
                "classification": classification,
                "miya_directive": (
                    "The document gave us no text (probably a scanned PDF or password-protected). "
                    "Ask the user for the key fields manually, then call record_invoice. "
                    "Do NOT fabricate values."
                ),
                "message_for_user": (
                    "I couldn't read any text out of that file (it might be a scan). "
                    "Tell me the vendor, amount, due date and invoice number and I'll log it."
                ),
            },
            status=status.HTTP_422_UNPROCESSABLE_ENTITY,
        )

    if classification.get("error"):
        return Response(
            {
                "success": False,
                "error": classification["error"],
                "classification": classification,
                "message_for_user": (
                    "I couldn't parse that document. Tell me the key fields and I'll "
                    "still log it manually."
                ),
            },
            status=status.HTTP_502_BAD_GATEWAY,
        )

    category = classification["category"]
    confidence = float(classification.get("confidence") or 0.0)
    fields = classification.get("fields") or {}
    summary = classification.get("summary") or ""

    action_envelope: dict[str, Any] = _action_envelope(
        type_="none",
        record_id=None,
        message=summary or "Got the document, not sure what to do with it.",
    )

    if category == "schedule":
        action_envelope = _action_envelope(
            type_="schedule_pending",
            record_id=None,
            message=(
                "That looks like a staff schedule. Open Schedule > 'Import from document' "
                "and I'll parse the shifts in detail."
            ),
        )
    elif category == "id_or_certification":
        doc_type = (fields.get("document_type") or "document").strip()
        person = (fields.get("person_name") or "").strip()
        expiry = fields.get("expiry_date") or None
        who = f" for {person}" if person else ""
        when = f" (expires {expiry})" if expiry else ""
        action_envelope = _action_envelope(
            type_="document_pending",
            record_id=None,
            message=(
                f"I see a {doc_type}{who}{when}. Tell me what you'd like me to do with it "
                "(reminder, compliance record, attach to staff)."
            ),
        )
    elif confidence < 0.55:
        action_envelope = _action_envelope(
            type_="low_confidence",
            record_id=None,
            message=(
                "I read the document but I'm not confident enough to file it automatically. "
                "Want to tell me what it is and where it should go?"
            ),
        )

    return Response(
        {
            "success": True,
            "classification": classification,
            "action_taken": action_envelope,
            "message_for_user": action_envelope.get("message_for_user"),
        },
        status=status.HTTP_200_OK,
    )
