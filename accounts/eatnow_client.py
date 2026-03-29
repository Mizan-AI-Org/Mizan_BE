"""
Eat Now / Eat App Concierge API (v2) — list reservations and bootstrap groups/restaurants.

Docs: https://restaurant.eatapp.co/knowledge/using-the-eat-app-partner-api-to-get-and-post-availability-0
Default production host: https://api.eatapp.co (override via EATNOW_CONCIERGE_API_BASE).
"""
from __future__ import annotations

import logging
from datetime import date, timedelta
from typing import Any, Dict, List, Optional, Tuple

import requests
from django.conf import settings

logger = logging.getLogger(__name__)


def concierge_api_base() -> str:
    return getattr(settings, "EATNOW_CONCIERGE_API_BASE", "https://api.eatapp.co").rstrip("/")


def _headers(api_key: str, *, restaurant_id: Optional[str] = None, group_id: Optional[str] = None) -> Dict[str, str]:
    h: Dict[str, str] = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }
    if restaurant_id:
        h["X-Restaurant-ID"] = str(restaurant_id).strip()
    if group_id:
        h["X-Group-ID"] = str(group_id).strip()
    return h


def _get_json(url: str, headers: Dict[str, str], *, params=None, timeout: int = 30) -> Tuple[int, Any]:
    resp = requests.get(url, headers=headers, params=params or {}, timeout=timeout)
    try:
        body = resp.json() if resp.content else {}
    except Exception:
        body = {}
    return resp.status_code, body


def _normalize_jsonapi_list(payload: Any) -> List[Dict[str, Any]]:
    """Return list of {id, type, attributes} from JSON:API or plain list."""
    if not payload or not isinstance(payload, dict):
        return []
    data = payload.get("data")
    if data is None:
        return []
    if isinstance(data, dict):
        return [data]
    if isinstance(data, list):
        return [x for x in data if isinstance(x, dict)]
    return []


def _flatten_reservation(item: Dict[str, Any]) -> Dict[str, Any]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    rid = item.get("id") or attrs.get("id")
    guest = attrs.get("guest") if isinstance(attrs.get("guest"), dict) else {}
    first = (guest.get("first_name") or guest.get("firstName") or "").strip()
    last = (guest.get("last_name") or guest.get("lastName") or "").strip()
    name = " ".join(x for x in [first, last] if x).strip() or (guest.get("name") or "").strip()
    return {
        "id": str(rid) if rid is not None else "",
        "start_time": attrs.get("start_time") or attrs.get("startTime") or attrs.get("date"),
        "covers": attrs.get("covers") or attrs.get("party_size") or attrs.get("guests"),
        "status": attrs.get("status") or attrs.get("state"),
        "guest_name": name,
        "phone": guest.get("phone") or attrs.get("phone"),
        "email": guest.get("email") or attrs.get("email"),
        "notes": attrs.get("notes") or attrs.get("note"),
        "raw": item,
    }


def _normalize_resource(item: Dict[str, Any]) -> Dict[str, Any]:
    attrs = item.get("attributes") if isinstance(item.get("attributes"), dict) else {}
    rid = item.get("id") or attrs.get("id")
    name = attrs.get("name") or attrs.get("title") or str(rid)
    return {"id": str(rid) if rid is not None else "", "name": str(name), "raw": item}


def discover(api_key: str, api_base: Optional[str] = None) -> Dict[str, Any]:
    """GET /groups and /restaurants (per group)."""
    base = (api_base or concierge_api_base()).rstrip("/")
    key = (api_key or "").strip()
    if not key:
        return {"success": False, "error": "API key is required"}

    groups_url = f"{base}/concierge/v2/groups"
    status, body = _get_json(groups_url, _headers(key))
    if status == 401:
        return {"success": False, "error": "Unauthorized — check your API key", "status_code": status}
    if status >= 400:
        return {"success": False, "error": body.get("errors") or body.get("error") or f"HTTP {status}", "status_code": status}

    groups = []
    for it in _normalize_jsonapi_list(body):
        groups.append(_normalize_resource(it))

    restaurants_by_group: List[Dict[str, Any]] = []
    for g in groups:
        gid = g.get("id")
        if not gid:
            continue
        rurl = f"{base}/concierge/v2/restaurants"
        st, rbody = _get_json(rurl, _headers(key, group_id=gid))
        if st >= 400:
            restaurants_by_group.append({"group_id": gid, "error": rbody or f"HTTP {st}", "restaurants": []})
            continue
        rests = [_normalize_resource(x) for x in _normalize_jsonapi_list(rbody)]
        restaurants_by_group.append({"group_id": gid, "restaurants": rests})

    return {"success": True, "groups": groups, "restaurants_by_group": restaurants_by_group}


def list_reservations(
    api_key: str,
    restaurant_id: str,
    start_date: date,
    end_date: date,
    api_base: Optional[str] = None,
) -> Dict[str, Any]:
    """GET /concierge/v2/reservations for each day in range (API filters by start_time_on)."""
    base = (api_base or concierge_api_base()).rstrip("/")
    key = (api_key or "").strip()
    rid = (restaurant_id or "").strip()
    if not key:
        return {"success": False, "error": "API key is required", "reservations": []}
    if not rid:
        return {"success": False, "error": "Restaurant ID is required", "reservations": []}
    if end_date < start_date:
        start_date, end_date = end_date, start_date

    url = f"{base}/concierge/v2/reservations"
    headers = _headers(key, restaurant_id=rid)
    merged: List[Dict[str, Any]] = []
    seen: set = set()
    d = start_date
    while d <= end_date:
        params = {"start_time_on": d.isoformat()}
        try:
            status, body = _get_json(url, headers, params=params)
        except requests.RequestException as exc:
            logger.warning("Eat Now reservations request failed: %s", exc)
            return {"success": False, "error": str(exc), "reservations": merged}
        if status == 401:
            return {"success": False, "error": "Unauthorized — check API key and restaurant ID", "reservations": []}
        if status >= 400:
            err = body.get("errors") or body.get("error") or f"HTTP {status}"
            return {"success": False, "error": err, "reservations": merged}
        for it in _normalize_jsonapi_list(body):
            flat = _flatten_reservation(it)
            uid = flat.get("id") or str(it.get("id"))
            if uid and uid not in seen:
                seen.add(uid)
                merged.append(flat)
        d += timedelta(days=1)

    merged.sort(key=lambda x: str(x.get("start_time") or ""))
    return {"success": True, "reservations": merged, "count": len(merged)}


def test_connection(api_key: str, restaurant_id: str, api_base: Optional[str] = None) -> Dict[str, Any]:
    """Lightweight GET to verify credentials."""
    today = date.today()
    return list_reservations(api_key, restaurant_id, today, today, api_base=api_base)
