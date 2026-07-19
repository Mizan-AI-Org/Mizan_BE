"""
Import Processes & Tasks (TaskTemplate rows) from uploaded documents.

Supports JSON, CSV, Excel (via openpyxl), plain text/Markdown, and unstructured
PDF/DOCX via GPT extraction on extracted text.
"""
from __future__ import annotations

import csv
import io
import json
import logging
import re
import uuid as uuid_mod
from typing import Any

import requests
from django.conf import settings
from django.core.cache import cache

from scheduling.document_router_service import extract_document_text
from scheduling.task_templates import TaskTemplate

logger = logging.getLogger(__name__)

_VALID_TEMPLATE_TYPES = {
    "CLEANING",
    "TEMPERATURE",
    "OPENING",
    "CLOSING",
    "HEALTH",
    "SOP",
    "MAINTENANCE",
    "COMPLIANCE",
    "SAFETY",
    "QUALITY",
    "CUSTOM",
}

_VALID_PRIORITIES = {"LOW", "MEDIUM", "HIGH", "URGENT"}

_PROCESS_INTENT_RE = re.compile(
    r"\b(process(?:es)?|checklist(?:s)?|task\s*template(?:s)?|sop|procedures?|"
    r"opening|closing|import\s+process|processes\s*&\s*tasks)\b",
    re.I,
)

_GPT_PROMPT = """You extract restaurant/business process checklists from a document.
Return STRICT JSON only (no markdown):

{
  "templates": [
    {
      "name": "Process name",
      "description": "optional short description or null",
      "template_type": "OPENING|CLOSING|CLEANING|MAINTENANCE|SAFETY|SOP|CUSTOM|...",
      "tasks": [
        {"title": "Task step title", "description": "optional or null", "priority": "LOW|MEDIUM|HIGH|URGENT"}
      ]
    }
  ]
}

Rules:
- Every task title MUST come from the document text — do NOT invent steps.
- If the document has one checklist, return one template. Multiple sections → multiple templates.
- template_type: infer from names (opening → OPENING, closing → CLOSING, else CUSTOM).
- If you cannot find any checklist steps, return {"templates": []}.
"""


def looks_like_process_import(note: str = "", filename: str = "") -> bool:
    blob = f"{note} {filename}".strip()
    if not blob:
        return False
    return bool(_PROCESS_INTENT_RE.search(blob))


def _normalize_template_type(raw: str | None, name: str = "") -> str:
    t = (raw or "").upper().strip()
    if t in _VALID_TEMPLATE_TYPES:
        return t
    n = (name or "").lower()
    if any(k in n for k in ("opening", "open checklist", "ouverture")):
        return "OPENING"
    if any(k in n for k in ("closing", "close checklist", "fermeture")):
        return "CLOSING"
    if any(k in n for k in ("clean", "hygiene", "nettoyage")):
        return "CLEANING"
    if any(k in n for k in ("maintenance", "equipment", "entretien")):
        return "MAINTENANCE"
    if any(k in n for k in ("safety", "haccp", "sécurité")):
        return "SAFETY"
    return "CUSTOM"


def _normalize_priority(raw: str | None) -> str:
    p = (raw or "MEDIUM").upper().strip()
    return p if p in _VALID_PRIORITIES else "MEDIUM"


def _tasks_payload(raw_tasks: list[Any]) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for item in raw_tasks or []:
        if isinstance(item, str):
            title = item.strip()
            if title:
                out.append(
                    {
                        "id": str(uuid_mod.uuid4()),
                        "title": title,
                        "description": "",
                        "priority": "MEDIUM",
                        "completed": False,
                    }
                )
            continue
        if not isinstance(item, dict):
            continue
        title = str(
            item.get("title")
            or item.get("task_title")
            or item.get("task")
            or item.get("name")
            or item.get("step")
            or ""
        ).strip()
        if not title:
            continue
        out.append(
            {
                "id": str(uuid_mod.uuid4()),
                "title": title,
                "description": str(item.get("description") or "").strip(),
                "priority": _normalize_priority(item.get("priority")),
                "completed": False,
            }
        )
    return out


def _normalize_template(raw: dict[str, Any]) -> dict[str, Any] | None:
    name = str(
        raw.get("name")
        or raw.get("process_name")
        or raw.get("template_name")
        or raw.get("title")
        or ""
    ).strip()
    if not name:
        return None
    tasks_raw = (
        raw.get("tasks")
        or raw.get("steps")
        or raw.get("checklist")
        or raw.get("items")
        or []
    )
    if isinstance(tasks_raw, dict):
        tasks_raw = list(tasks_raw.values())
    tasks = _tasks_payload(tasks_raw if isinstance(tasks_raw, list) else [])
    if not tasks:
        return None
    return {
        "name": name[:255],
        "description": str(raw.get("description") or "").strip() or None,
        "template_type": _normalize_template_type(raw.get("template_type"), name),
        "tasks": tasks,
    }


def _parse_json_templates(text: str) -> list[dict[str, Any]]:
    text = text.strip()
    if not text:
        return []
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return []

    candidates: list[Any] = []
    if isinstance(data, list):
        candidates = data
    elif isinstance(data, dict):
        for key in ("templates", "processes", "task_templates", "checklists", "data"):
            if isinstance(data.get(key), list):
                candidates = data[key]
                break
        if not candidates and (data.get("name") or data.get("tasks")):
            candidates = [data]

    out: list[dict[str, Any]] = []
    for item in candidates:
        if isinstance(item, dict):
            norm = _normalize_template(item)
            if norm:
                out.append(norm)
    return out


def _parse_csv_templates(text: str) -> list[dict[str, Any]]:
    reader = csv.reader(io.StringIO(text))
    rows = [r for r in reader if any(c.strip() for c in r)]
    if not rows:
        return []

    header = [c.strip().lower().replace(" ", "_") for c in rows[0]]
    has_header = any(h in {"process", "process_name", "template", "task", "task_title", "title", "name"} for h in header)
    data_rows = rows[1:] if has_header else rows

    grouped: dict[str, list[dict[str, Any]]] = {}
    for row in data_rows:
        cells = [c.strip() for c in row]
        if not cells:
            continue
        if has_header and len(cells) >= len(header):
            row_map = {header[i]: cells[i] for i in range(min(len(header), len(cells)))}
            proc = (
                row_map.get("process_name")
                or row_map.get("process")
                or row_map.get("template_name")
                or row_map.get("template")
                or row_map.get("name")
                or "Imported Process"
            )
            task_title = (
                row_map.get("task_title")
                or row_map.get("task")
                or row_map.get("title")
                or row_map.get("step")
                or cells[-1]
            )
            grouped.setdefault(proc, []).append(
                {
                    "title": task_title,
                    "description": row_map.get("description") or "",
                    "priority": row_map.get("priority") or "MEDIUM",
                }
            )
        elif len(cells) >= 2:
            grouped.setdefault(cells[0], []).append({"title": cells[1], "priority": "MEDIUM"})
        elif len(cells) == 1:
            grouped.setdefault("Imported Process", []).append({"title": cells[0], "priority": "MEDIUM"})

    out: list[dict[str, Any]] = []
    for name, tasks in grouped.items():
        norm = _normalize_template({"name": name, "tasks": tasks})
        if norm:
            out.append(norm)
    return out


def _parse_text_templates(text: str) -> list[dict[str, Any]]:
    lines = [ln.rstrip() for ln in text.splitlines()]
    templates: list[dict[str, Any]] = []
    current_name = "Imported Process"
    current_tasks: list[dict[str, Any]] = []

    def flush() -> None:
        nonlocal current_name, current_tasks
        if current_tasks:
            norm = _normalize_template({"name": current_name, "tasks": current_tasks})
            if norm:
                templates.append(norm)
        current_tasks = []

    heading_re = re.compile(r"^(#{1,3}\s+|[A-Z][^:\n]{2,80}:)\s*(.+)$")
    bullet_re = re.compile(r"^[\s]*(?:[-*•]|\d+[.)])\s+(.+)$")

    for line in lines:
        stripped = line.strip()
        if not stripped:
            continue
        hm = heading_re.match(stripped)
        if hm and not bullet_re.match(stripped):
            flush()
            current_name = hm.group(2).strip().rstrip(":")
            continue
        bm = bullet_re.match(stripped)
        if bm:
            current_tasks.append({"title": bm.group(1).strip(), "priority": "MEDIUM"})
            continue
        if not current_tasks and len(stripped) > 3:
            current_name = stripped.rstrip(":")

    flush()
    return templates


def _gpt_extract_templates(extracted: str, note: str = "") -> list[dict[str, Any]]:
    api_key = getattr(settings, "OPENAI_API_KEY", "") or ""
    if not api_key:
        return []

    snippet = extracted[:12000]
    user_blob = f"Manager note: {note}\n\nDOCUMENT:\n<<<\n{snippet}\n>>>"
    payload = {
        "model": "gpt-4o",
        "messages": [
            {"role": "system", "content": _GPT_PROMPT},
            {"role": "user", "content": user_blob},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 2500,
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
            timeout=60,
        )
    except requests.RequestException:
        logger.exception("process_import: GPT request failed")
        return []

    if r.status_code != 200:
        logger.warning("process_import: GPT error %s", r.status_code)
        return []

    text = ((r.json() or {}).get("choices") or [{}])[0].get("message", {}).get("content") or ""
    try:
        parsed = json.loads(text.strip())
    except json.JSONDecodeError:
        return []

    raw_list = parsed.get("templates") if isinstance(parsed, dict) else []
    out: list[dict[str, Any]] = []
    for item in raw_list or []:
        if isinstance(item, dict):
            norm = _normalize_template(item)
            if norm:
                out.append(norm)
    return out


def parse_process_templates_from_bytes(
    blob: bytes,
    *,
    content_type: str = "",
    name: str = "",
    note: str = "",
) -> dict[str, Any]:
    """Return {templates: [...], errors: [...], extracted_kind: str}."""
    kind, extracted = extract_document_text(blob, content_type=content_type, name=name)
    errors: list[str] = []

    if kind == "unknown":
        return {
            "templates": [],
            "errors": ["unsupported_document_type"],
            "extracted_kind": kind,
        }
    if not extracted.strip():
        return {
            "templates": [],
            "errors": ["empty_extraction"],
            "extracted_kind": kind,
        }

    templates: list[dict[str, Any]] = []
    lower_name = (name or "").lower()

    if lower_name.endswith(".json") or extracted.lstrip().startswith(("{", "[")):
        templates = _parse_json_templates(extracted)
    if not templates and (lower_name.endswith(".csv") or kind == "csv"):
        templates = _parse_csv_templates(extracted)
    if not templates and kind in {"text", "docx", "pdf"}:
        templates = _parse_text_templates(extracted)
    if not templates:
        templates = _gpt_extract_templates(extracted, note=note)

    if not templates:
        errors.append("no_templates_parsed")

    return {
        "templates": templates,
        "errors": errors,
        "extracted_kind": kind,
        "extracted_chars": len(extracted),
    }


def bulk_create_task_templates(
    restaurant,
    templates: list[dict[str, Any]],
    *,
    acting_user=None,
    skip_duplicates: bool = True,
    source_note: str = "",
) -> dict[str, Any]:
    """Create TaskTemplate rows. Returns created/skipped lists."""
    created: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    errors: list[str] = []

    existing_names = set(
        TaskTemplate.objects.filter(restaurant=restaurant, is_active=True).values_list("name", flat=True)
    )

    for tpl in templates:
        name = tpl["name"]
        if skip_duplicates and name in existing_names:
            skipped.append({"name": name, "reason": "duplicate_name"})
            continue
        try:
            row = TaskTemplate.objects.create(
                restaurant=restaurant,
                name=name,
                description=tpl.get("description"),
                template_type=tpl.get("template_type") or "CUSTOM",
                tasks=tpl.get("tasks") or [],
                frequency="CUSTOM",
                ai_generated=True,
                ai_prompt=source_note[:500] or f"Imported by Miya from document",
                created_by=acting_user,
                is_active=True,
            )
            existing_names.add(name)
            created.append(
                {
                    "id": str(row.id),
                    "name": row.name,
                    "template_type": row.template_type,
                    "tasks_count": len(row.tasks or []),
                }
            )
        except Exception as e:
            logger.exception("process_import: create failed for %s", name)
            errors.append(f"{name}: {str(e)[:120]}")

    try:
        cache.delete(f"agent:sched:task_templates:{restaurant.id}")
    except Exception:
        pass

    return {"created": created, "skipped": skipped, "errors": errors}
