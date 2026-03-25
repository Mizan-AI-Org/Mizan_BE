"""
Apply Eat Now (eat-now.io) webhook payloads to local EatNowReservation rows.

Payload shape per Eat Now docs: event + restaurant_id + data.reservation {...}.
"""
from __future__ import annotations

import logging
from typing import Any, Dict, Optional

from django.utils.dateparse import parse_date, parse_datetime

from .models import EatNowReservation

logger = logging.getLogger(__name__)


def _parse_reservation_date(val: Any):
    if val is None:
        return None
    if hasattr(val, "date") and not hasattr(val, "hour"):
        return val
    s = str(val).strip()
    if not s:
        return None
    if len(s) >= 10 and s[4] == "-" and s[7] == "-":
        d = parse_date(s[:10])
        if d:
            return d
    dt = parse_datetime(s.replace("Z", "+00:00"))
    if dt:
        return dt.date()
    return None


def _extract_reservation_dict(payload: Dict[str, Any]) -> Optional[Dict[str, Any]]:
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    r = data.get("reservation")
    if isinstance(r, dict) and r.get("id"):
        return r
    if data.get("id") and isinstance(data.get("id"), str):
        return data
    return None


def _external_id_for_delete(payload: Dict[str, Any]) -> Optional[str]:
    r = _extract_reservation_dict(payload)
    if r and r.get("id"):
        return str(r["id"]).strip()
    data = payload.get("data")
    if isinstance(data, dict):
        for key in ("reservation_id", "id"):
            v = data.get(key)
            if isinstance(v, dict) and v.get("id"):
                return str(v["id"]).strip()
            if v and not isinstance(v, dict):
                return str(v).strip()
    return None


def apply_eatnow_webhook_payload(restaurant, event_type: str, payload: Dict[str, Any]) -> None:
    """Upsert or soft-delete EatNowReservation from a verified webhook body."""
    ev = (event_type or "").strip().upper()
    if ev == "RESERVATION_DELETED":
        ext_id = _external_id_for_delete(payload)
        if ext_id:
            EatNowReservation.objects.filter(restaurant=restaurant, external_id=ext_id[:128]).update(is_deleted=True)
        else:
            logger.warning("eatnow_webhook_processor: DELETE without reservation id")
        return

    if ev not in ("RESERVATION_CREATED", "RESERVATION_UPDATED", ""):
        # Still try to ingest if payload looks like a reservation (some providers only send type in header)
        if not _extract_reservation_dict(payload):
            logger.info("eatnow_webhook_processor: skip event %s", ev)
            return

    res = _extract_reservation_dict(payload)
    if not res or not res.get("id"):
        logger.warning("eatnow_webhook_processor: missing reservation id in payload for %s", ev)
        return

    ext_id = str(res["id"]).strip()[:128]
    customer = res.get("customer") if isinstance(res.get("customer"), dict) else {}
    name = (customer.get("name") or "").strip()
    phone = customer.get("phone_number") or customer.get("phone") or ""
    email = customer.get("email") or ""
    msg = res.get("custom_message") or ""
    allergies = res.get("allergies") or ""
    note_parts = [str(x).strip() for x in (msg, allergies) if x and str(x).strip()]
    notes = "\n".join(note_parts) if note_parts else ""

    rd = _parse_reservation_date(res.get("reservation_date"))
    rt = str(res.get("reservation_time") or "").strip()[:32]
    gs = res.get("group_size")
    covers = None
    try:
        if gs is not None and gs != "":
            covers = int(gs)
    except (TypeError, ValueError):
        covers = None

    tags = res.get("tags") if isinstance(res.get("tags"), list) else []

    defaults = {
        "status": str(res.get("status") or "")[:128],
        "group_size": covers,
        "reservation_date": rd,
        "reservation_time": rt,
        "guest_name": name[:255],
        "phone": str(phone)[:64],
        "email": str(email)[:254],
        "notes": notes[:8000],
        "tags": tags,
        "source": str(res.get("source") or "")[:64],
        "raw_reservation": res,
        "is_deleted": False,
    }
    EatNowReservation.objects.update_or_create(
        restaurant=restaurant,
        external_id=ext_id,
        defaults=defaults,
    )
