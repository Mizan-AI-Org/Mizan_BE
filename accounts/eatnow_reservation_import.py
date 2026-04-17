"""
Map Eat App Concierge API reservation rows (from eatnow_client._flatten_reservation) into EatNowReservation.
"""
from __future__ import annotations

from typing import Any, Dict

from django.utils.dateparse import parse_date, parse_datetime

from .models import EatNowReservation, Restaurant


def upsert_from_concierge_flat(restaurant: Restaurant, flat: Dict[str, Any]) -> bool:
    ext_id = str(flat.get("id") or "").strip()[:128]
    if not ext_id:
        return False

    st = flat.get("start_time")
    rd = None
    rt = ""
    if st is not None:
        s = str(st).strip()
        if s:
            dt = parse_datetime(s.replace("Z", "+00:00"))
            if dt:
                rd = dt.date()
                rt = dt.strftime("%H:%M")
            else:
                pd = parse_date(s[:10]) if len(s) >= 10 else None
                if pd:
                    rd = pd

    covers = flat.get("covers")
    try:
        covers_i = int(covers) if covers is not None and covers != "" else None
    except (TypeError, ValueError):
        covers_i = None

    raw = flat.get("raw")
    raw_dict = raw if isinstance(raw, dict) else flat

    EatNowReservation.objects.update_or_create(
        restaurant=restaurant,
        external_id=ext_id,
        defaults={
            "status": str(flat.get("status") or "")[:128],
            "group_size": covers_i,
            "reservation_date": rd,
            "reservation_time": rt[:32],
            "guest_name": str(flat.get("guest_name") or "")[:255],
            "phone": str(flat.get("phone") or "")[:64],
            "email": str(flat.get("email") or "")[:254],
            "notes": str(flat.get("notes") or "")[:8000],
            "tags": [],
            "source": "CONCIERGE_API",
            "raw_reservation": raw_dict if isinstance(raw_dict, dict) else {},
            "is_deleted": False,
        },
    )
    return True
