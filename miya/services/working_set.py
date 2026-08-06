"""Turn-local working set for Miya pronoun / short-reply resolution.

After list_* tools succeed we cache ordered entity ids (invoices, tasks, ops)
keyed by restaurant + user. On assign / cancel / status updates with missing
or pronoun-like ids ("les", "them", "celui-là", "it"), we fill from that set.
"""

from __future__ import annotations

import logging
import re
from typing import Any

from django.core.cache import cache

logger = logging.getLogger(__name__)

_TTL_SECONDS = 60 * 45  # 45 minutes — covers a manager WhatsApp session

_PRONOUN_RE = re.compile(
    r"^(les|leur|eux|elles|them|those|these|they|it|that|"
    r"celui[- ]?là|celle[- ]?là|ceux[- ]?là|celles[- ]?là|"
    r"the\s+(first|second|third|last)\s*(one)?|"
    r"le\s+premier|la\s+première|le\s+dernier|la\s+dernière|"
    r"all|tous|toutes|tout)$",
    re.I,
)

_INDEX_WORDS = {
    "first": 0,
    "premier": 0,
    "première": 0,
    "1": 0,
    "second": 1,
    "deuxième": 1,
    "2": 1,
    "third": 2,
    "troisième": 2,
    "3": 2,
    "last": -1,
    "dernier": -1,
    "dernière": -1,
}


def _cache_key(restaurant_id: str, user_id: str, kind: str) -> str:
    return f"miya:working_set:{restaurant_id}:{user_id}:{kind}"


def remember_entities(
    *,
    restaurant_id: str | None,
    user_id: str | None,
    kind: str,
    entities: list[dict[str, Any]],
) -> None:
    if not restaurant_id or not user_id or not entities:
        return
    cleaned: list[dict[str, Any]] = []
    for ent in entities:
        eid = str(ent.get("id") or "").strip()
        if not eid:
            continue
        cleaned.append(
            {
                "id": eid,
                "label": str(ent.get("label") or ent.get("title") or "")[:200],
                "extra": {
                    k: v
                    for k, v in ent.items()
                    if k not in {"id", "label", "title"} and isinstance(v, (str, int, float, bool))
                },
            }
        )
    if not cleaned:
        return
    try:
        cache.set(_cache_key(str(restaurant_id), str(user_id), kind), cleaned, _TTL_SECONDS)
    except Exception:
        logger.exception("working_set remember failed kind=%s", kind)


def get_entities(
    *,
    restaurant_id: str | None,
    user_id: str | None,
    kind: str,
) -> list[dict[str, Any]]:
    if not restaurant_id or not user_id:
        return []
    try:
        rows = cache.get(_cache_key(str(restaurant_id), str(user_id), kind)) or []
    except Exception:
        return []
    return rows if isinstance(rows, list) else []


def looks_like_pronoun_ref(value: Any) -> bool:
    text = str(value or "").strip()
    if not text:
        return True
    if _PRONOUN_RE.match(text):
        return True
    # Bare ordinals / "the first one" already covered; also "invoice 1" style.
    return bool(re.match(r"^(the\s+)?(first|second|third|last)(\s+one)?$", text, re.I))


def _pick_index(text: str, size: int) -> int | None:
    lower = text.lower()
    for word, idx in _INDEX_WORDS.items():
        if word in lower:
            if idx < 0:
                return size - 1 if size else None
            return idx if idx < size else None
    return None


def resolve_ids(
    *,
    restaurant_id: str | None,
    user_id: str | None,
    kind: str,
    explicit_ids: list[str] | None = None,
    pronoun_hint: str | None = None,
    all_listed: bool = False,
) -> list[str]:
    """Return concrete entity ids from explicit args and/or the working set."""
    cleaned = [str(x).strip() for x in (explicit_ids or []) if str(x).strip()]
    # Drop pronoun-like tokens accidentally passed as ids.
    concrete = [x for x in cleaned if not looks_like_pronoun_ref(x) and len(x) > 2]
    if concrete and not all_listed:
        return concrete

    listed = get_entities(restaurant_id=restaurant_id, user_id=user_id, kind=kind)
    if not listed:
        return concrete

    if all_listed or (pronoun_hint and re.search(r"\b(all|tous|toutes)\b", pronoun_hint, re.I)):
        return [str(e["id"]) for e in listed]

    hint = (pronoun_hint or "").strip() or (cleaned[0] if cleaned else "")
    if hint:
        idx = _pick_index(hint, len(listed))
        if idx is not None:
            return [str(listed[idx]["id"])]

    if looks_like_pronoun_ref(hint) or not concrete:
        return [str(e["id"]) for e in listed]

    return concrete


def extract_list_entities(tool_name: str, body: dict[str, Any]) -> tuple[str, list[dict[str, Any]]]:
    """Map a successful tool response into (kind, entities)."""
    data = body.get("data") if isinstance(body.get("data"), dict) else body

    if tool_name == "list_invoices":
        rows = data.get("invoices") or data.get("results") or data.get("items") or []
        entities = []
        for r in rows if isinstance(rows, list) else []:
            if not isinstance(r, dict):
                continue
            entities.append(
                {
                    "id": r.get("id"),
                    "label": r.get("vendor_name") or r.get("title") or "",
                    "invoice_number": r.get("invoice_number") or "",
                }
            )
        return "invoices", entities

    if tool_name in ("list_operations_live", "list_dashboard_tasks", "list_tasks_demands"):
        entities = []
        for key in ("pending", "in_progress", "completed", "items", "tasks", "results"):
            rows = data.get(key) or []
            if not isinstance(rows, list):
                continue
            for r in rows:
                if not isinstance(r, dict):
                    continue
                entities.append(
                    {
                        "id": r.get("id"),
                        "label": r.get("title") or r.get("operation") or "",
                        "status": r.get("status") or r.get("display_status") or "",
                    }
                )
        return "tasks", entities

    if tool_name in ("list_staff_requests",):
        rows = data.get("requests") or data.get("items") or data.get("results") or []
        entities = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                entities.append({"id": r.get("id"), "label": r.get("subject") or r.get("title") or ""})
        return "tasks", entities

    if tool_name == "list_incidents":
        rows = data.get("incidents") or []
        entities = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                entities.append(
                    {
                        "id": r.get("id"),
                        "label": r.get("title") or r.get("description") or "",
                        "status": r.get("status") or "",
                    }
                )
        return "incidents", entities

    if tool_name == "search_operational_records":
        rows = data.get("matches") or []
        entities = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                entities.append(
                    {
                        "id": r.get("id"),
                        "label": r.get("title") or r.get("subject") or "",
                        "status": r.get("status") or "",
                        "type": r.get("type") or "",
                    }
                )
        return "records", entities

    if tool_name == "list_calendar_events":
        rows = data.get("events") or []
        entities = []
        for r in rows if isinstance(rows, list) else []:
            if isinstance(r, dict):
                entities.append(
                    {
                        "id": r.get("id"),
                        "label": r.get("title") or "",
                        "start": r.get("start") or "",
                        "location": r.get("location") or "",
                    }
                )
        return "calendar_events", entities

    return "", []


def apply_working_set_to_args(
    tool_name: str,
    arguments: dict[str, Any],
    *,
    restaurant_id: str | None,
    user_id: str | None,
) -> dict[str, Any]:
    """Mutate a copy of tool args to fill missing ids from the working set."""
    args = dict(arguments or {})

    if tool_name == "assign_invoice":
        ids_raw = args.get("invoice_ids") or args.get("invoiceIds") or []
        if isinstance(ids_raw, str):
            ids_raw = [x.strip() for x in ids_raw.replace(";", ",").split(",") if x.strip()]
        single = str(args.get("invoice_id") or args.get("invoiceId") or args.get("id") or "").strip()
        if single:
            ids_raw = list(ids_raw) + [single]
        all_open = bool(args.get("all_open") or args.get("allOpen"))
        hint = str(args.get("pronoun") or args.get("ref") or args.get("which") or "").strip()
        # If LLM passed nothing / pronouns, resolve from last list.
        needs = all_open or not ids_raw or all(looks_like_pronoun_ref(x) for x in ids_raw)
        if needs and not all_open:
            resolved = resolve_ids(
                restaurant_id=restaurant_id,
                user_id=user_id,
                kind="invoices",
                explicit_ids=[str(x) for x in ids_raw],
                pronoun_hint=hint or (str(ids_raw[0]) if ids_raw else None),
                all_listed=True if not ids_raw else False,
            )
            # Default: pronoun without ordinal → all listed open invoices.
            if not resolved:
                resolved = resolve_ids(
                    restaurant_id=restaurant_id,
                    user_id=user_id,
                    kind="invoices",
                    all_listed=True,
                )
            if resolved:
                args["invoice_ids"] = resolved
                args.pop("invoice_id", None)
                args.pop("invoiceId", None)

    if tool_name in ("update_dashboard_task_status", "update_dashboard_task", "reassign_dashboard_task"):
        tid = str(args.get("task_id") or args.get("taskId") or args.get("id") or "").strip()
        if looks_like_pronoun_ref(tid):
            resolved = resolve_ids(
                restaurant_id=restaurant_id,
                user_id=user_id,
                kind="tasks",
                pronoun_hint=tid,
            )
            if resolved:
                args["task_id"] = resolved[0]
                args.pop("taskId", None)
                args.pop("id", None)

    if tool_name == "close_incident":
        iid = str(args.get("incident_id") or args.get("incidentId") or args.get("id") or "").strip()
        if looks_like_pronoun_ref(iid) or not iid:
            resolved = resolve_ids(
                restaurant_id=restaurant_id,
                user_id=user_id,
                kind="incidents",
                explicit_ids=[iid] if iid and not looks_like_pronoun_ref(iid) else [],
                pronoun_hint=iid or str(args.get("q") or args.get("title") or ""),
            )
            if resolved:
                args["incident_id"] = resolved[0]
                args.pop("incidentId", None)
                args.pop("id", None)

    if tool_name == "update_calendar_event":
        eid = str(args.get("event_id") or args.get("eventId") or args.get("id") or "").strip()
        if looks_like_pronoun_ref(eid) or not eid:
            resolved = resolve_ids(
                restaurant_id=restaurant_id,
                user_id=user_id,
                kind="calendar_events",
                explicit_ids=[eid] if eid and not looks_like_pronoun_ref(eid) else [],
                pronoun_hint=eid or str(args.get("q") or args.get("title") or ""),
            )
            if resolved:
                args["event_id"] = resolved[0]
                args.pop("eventId", None)
                args.pop("id", None)

    if tool_name == "delete_calendar_event":
        eid = str(args.get("event_id") or args.get("eventId") or args.get("id") or "").strip()
        if looks_like_pronoun_ref(eid) or not eid:
            resolved = resolve_ids(
                restaurant_id=restaurant_id,
                user_id=user_id,
                kind="calendar_events",
                explicit_ids=[eid] if eid and not looks_like_pronoun_ref(eid) else [],
                pronoun_hint=eid or str(args.get("q") or args.get("title") or ""),
            )
            if resolved:
                args["event_id"] = resolved[0]
                args.pop("eventId", None)
                args.pop("id", None)

    return args
