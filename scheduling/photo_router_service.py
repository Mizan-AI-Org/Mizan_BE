"""
Photo-to-action router.

Takes a single image and asks GPT-4o vision what kind of restaurant
operations photo it is, then returns a structured suggestion the agent
(or a manager workflow) can act on:

- ``invoice_or_receipt``  →  vendor, amount, due_date, invoice_number
                              → can feed straight into the Finance tool
- ``schedule``            →  hand off to ``parse_schedule_image`` (existing)
- ``equipment_issue``     →  short description + severity → MAINTENANCE staff_request
- ``incident``            →  safety/health/security → IncidentReport
- ``id_or_certification`` →  document type, expiry → HR document upload
- ``inventory``           →  list of items + counts → inventory tool
- ``other``               →  fallback with a free-text description

The classifier never invents a structured field — if a value isn't
clearly visible it returns null and the caller decides whether to ask
the manager for the missing piece.

Why this lives in ``scheduling`` rather than its own app: the existing
schedule_photo_service already wraps OpenAI Vision and we want the same
auth / settings / error envelope. This module borrows the request
helper but ships a different prompt + response shape.
"""
from __future__ import annotations

import base64
import json
import logging
import re
from typing import Any

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


_PROMPT = """You are an AI assistant for a restaurant management system.
Look at the attached photo and decide what *kind* of business document or
scene it shows, then extract the fields a manager would care about.

Pick exactly one category from this list:
  - "invoice_or_receipt"  — supplier bill, utility bill, restaurant receipt
                              that the manager owes (NOT a guest receipt
                              the customer paid)
  - "schedule"            — staff rota / weekly schedule
  - "equipment_issue"     — a broken / damaged piece of equipment, leak,
                              spillage, faulty appliance, broken tile
  - "incident"            — health/safety/security event (injury, fire,
                              violence, food-safety hazard, theft)
  - "id_or_certification" — staff ID, food handler card, license,
                              certification, training certificate
  - "inventory"           — stockroom shelves, fridge contents, ingredient
                              list, anything that needs a stock count
  - "other"               — anything that doesn't fit above

Respond with a STRICT JSON object (no markdown, no commentary) of
exactly this shape:

{
  "category": "invoice_or_receipt" | "schedule" | "equipment_issue" | "incident" | "id_or_certification" | "inventory" | "other",
  "confidence": 0.0 - 1.0,
  "summary": "one-sentence human description of the photo",
  "fields": {
    // Only the keys that match the chosen category. Use null for
    // anything you cannot read off the image. Do NOT guess values.
    "vendor": string | null,                     // invoice_or_receipt
    "amount": number | null,                     // invoice_or_receipt
    "currency": string | null,                   // invoice_or_receipt (ISO 4217)
    "invoice_number": string | null,             // invoice_or_receipt
    "due_date": "YYYY-MM-DD" | null,             // invoice_or_receipt
    "issue_date": "YYYY-MM-DD" | null,           // invoice_or_receipt

    "equipment": string | null,                  // equipment_issue
    "severity": "low" | "medium" | "high" | null,// equipment_issue / incident
    "location_hint": string | null,              // equipment_issue / incident

    "incident_type": "safety" | "health" | "security" | "food_safety" | "other" | null,
    "injury": boolean | null,                    // incident

    "document_type": string | null,              // id_or_certification (e.g. "Food Handler Card")
    "person_name": string | null,                // id_or_certification
    "expiry_date": "YYYY-MM-DD" | null,          // id_or_certification

    "items": [                                   // inventory
      { "name": string, "quantity": number | null, "unit": string | null }
    ] | null
  },
  "suggested_action": "log_invoice" | "import_schedule" | "open_maintenance_request" | "report_incident" | "upload_document" | "stock_count" | "ask_manager"
}

If you genuinely cannot tell, set category="other", confidence < 0.4,
and suggested_action="ask_manager".
"""


_VALID_CATEGORIES = {
    "invoice_or_receipt",
    "schedule",
    "equipment_issue",
    "incident",
    "id_or_certification",
    "inventory",
    "other",
}

_VALID_ACTIONS = {
    "log_invoice",
    "import_schedule",
    "open_maintenance_request",
    "report_incident",
    "upload_document",
    "stock_count",
    "ask_manager",
}


def parse_photo(image_bytes: bytes, content_type: str = "image/jpeg") -> dict[str, Any]:
    """Classify a photo and extract action-relevant fields.

    Always returns a dict with at least ``category`` and ``confidence``,
    even on error — callers can branch on ``error`` or check the
    confidence threshold.
    """
    api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not api_key:
        return {
            "category": "other",
            "confidence": 0.0,
            "summary": "Vision API not configured.",
            "error": "OPENAI_API_KEY not configured",
            "suggested_action": "ask_manager",
            "fields": {},
        }

    b64 = base64.standard_b64encode(image_bytes).decode("ascii")
    payload = {
        "model": "gpt-4o",
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": _PROMPT},
                    {
                        "type": "image_url",
                        "image_url": {"url": f"data:{content_type};base64,{b64}"},
                    },
                ],
            }
        ],
        "max_tokens": 800,
        # Keep the temperature low so the classifier doesn't get
        # creative on amounts/dates — wrong numbers are worse than
        # null here because the user trusts the bill they snapped.
        "temperature": 0.1,
    }

    try:
        r = requests.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=45,
        )
    except requests.RequestException as e:
        logger.exception("parse_photo: OpenAI request failed")
        return {
            "category": "other",
            "confidence": 0.0,
            "summary": "Vision call failed.",
            "error": str(e),
            "suggested_action": "ask_manager",
            "fields": {},
        }

    if r.status_code != 200:
        return {
            "category": "other",
            "confidence": 0.0,
            "summary": f"Vision API error: {r.status_code}",
            "error": r.text[:300],
            "suggested_action": "ask_manager",
            "fields": {},
        }

    data = r.json() or {}
    choices = data.get("choices") or []
    if not choices:
        return {
            "category": "other",
            "confidence": 0.0,
            "summary": "Empty vision response.",
            "suggested_action": "ask_manager",
            "fields": {},
        }

    text = ((choices[0].get("message") or {}).get("content") or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text)

    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        logger.warning("parse_photo: invalid JSON from vision: %s", text[:300])
        return {
            "category": "other",
            "confidence": 0.0,
            "summary": "Couldn't parse the vision response.",
            "raw_response": text[:500],
            "suggested_action": "ask_manager",
            "fields": {},
        }

    category = str(parsed.get("category") or "other")
    if category not in _VALID_CATEGORIES:
        category = "other"
    suggested = str(parsed.get("suggested_action") or "")
    if suggested not in _VALID_ACTIONS:
        suggested = "ask_manager"

    confidence = parsed.get("confidence")
    try:
        confidence = float(confidence)
        confidence = max(0.0, min(1.0, confidence))
    except (TypeError, ValueError):
        confidence = 0.0

    return {
        "category": category,
        "confidence": confidence,
        "summary": str(parsed.get("summary") or "")[:500],
        "suggested_action": suggested,
        "fields": parsed.get("fields") or {},
    }
