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


def normalize_eatnow_event_type(event_header: str, payload: Dict[str, Any]) -> str:
    """Map reservation.created, types in payload, etc. to RESERVATION_* constants."""
    ev = (event_header or "").strip()
    if not ev and isinstance(payload.get("event"), str):
        ev = payload["event"].strip()
    if not ev and isinstance(payload.get("type"), str):
        ev = payload["type"].strip()
    ev = ev.replace(".", "_").replace("-", "_").upper()
    if ev in ("RESERVATION_CREATED", "RESERVATION_UPDATED", "RESERVATION_DELETED"):
        return ev
    if "RESERVATION" in ev and "CREATED" in ev:
        return "RESERVATION_CREATED"
    if "RESERVATION" in ev and "UPDATED" in ev:
        return "RESERVATION_UPDATED"
    if "RESERVATION" in ev and "DELETED" in ev:
        return "RESERVATION_DELETED"
    return ev


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
    rtop = payload.get("reservation")
    if isinstance(rtop, dict) and rtop.get("id") is not None:
        return rtop
    data = payload.get("data")
    if not isinstance(data, dict):
        return None
    r = data.get("reservation")
    if isinstance(r, dict) and r.get("id") is not None:
        return r
    # data may be the reservation object (string or non-string id)
    if data.get("id") is not None and (
        data.get("reservation_date")
        or data.get("reservationDate")
        or data.get("starts_at")
        or data.get("startsAt")
        or data.get("customer")
    ):
        return data
    if data.get("id") is not None:
        return data
    return None


def _external_id_for_delete(payload: Dict[str, Any]) -> Optional[str]:
    r = _extract_reservation_dict(payload)
    if r and r.get("id") is not None:
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


def _guest_display_name(customer: Dict[str, Any]) -> str:
    first = (customer.get("first_name") or customer.get("firstName") or "").strip()
    last = (customer.get("last_name") or customer.get("lastName") or "").strip()
    joined = " ".join(x for x in (first, last) if x).strip()
    return joined or (customer.get("name") or "").strip()


def _coerce_date_time_from_reservation(res: Dict[str, Any]) -> tuple:
    """Return (date, time_str) with snake_case and camelCase fallbacks."""
    rd = _parse_reservation_date(
        res.get("reservation_date") or res.get("reservationDate") or res.get("date")
    )
    rt = str(res.get("reservation_time") or res.get("reservationTime") or "").strip()[:32]
    if not rd or not rt:
        for key in ("starts_at", "startsAt", "start_time", "startTime", "datetime"):
            raw = res.get(key)
            if not raw:
                continue
            dt = parse_datetime(str(raw).replace("Z", "+00:00"))
            if dt:
                if not rd:
                    rd = dt.date()
                if not rt:
                    rt = dt.strftime("%H:%M")
                break
    return rd, rt


def apply_eatnow_webhook_payload(restaurant, event_type: str, payload: Dict[str, Any]) -> None:
    """Upsert or soft-delete EatNowReservation from a verified webhook body."""
    ev = normalize_eatnow_event_type(event_type, payload)
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
    if not res or res.get("id") is None:
        logger.warning("eatnow_webhook_processor: missing reservation id in payload for %s", ev)
        return

    ext_id = str(res["id"]).strip()[:128]
    customer = res.get("customer") if isinstance(res.get("customer"), dict) else {}
    name = _guest_display_name(customer)
    phone = customer.get("phone_number") or customer.get("phone") or ""
    email = customer.get("email") or ""
    msg = res.get("custom_message") or res.get("customMessage") or ""
    allergies = res.get("allergies") or ""
    note_parts = [str(x).strip() for x in (msg, allergies) if x and str(x).strip()]
    notes = "\n".join(note_parts) if note_parts else ""

    rd, rt = _coerce_date_time_from_reservation(res)
    gs = res.get("group_size") or res.get("groupSize") or res.get("party_size") or res.get("partySize") or res.get("covers")
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
