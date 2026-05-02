"""
Resolve (restaurant, user) for Lua/Miya agent calls with read-through caching.

Cuts repeated RDS lookups when the agent sends the same sessionId, userId, phone, or JWT
across multiple tool calls in one conversation.
"""
from __future__ import annotations

import hashlib
import re
from typing import Any

from core.read_through_cache import safe_cache_get, safe_cache_set

_RESOLVE_TTL = 600


def _merge_agent_request_data(request, payload: dict | None) -> tuple[dict, dict]:
    data: dict[str, Any] = dict(payload or {})
    if request is not None:
        try:
            qp = getattr(request, "query_params", None) or getattr(request, "GET", None) or {}
            for k, v in getattr(qp, "items", lambda: [])():
                data.setdefault(k, v)
        except Exception:
            pass
        try:
            hdr_rest_id = request.META.get("HTTP_X_RESTAURANT_ID")
            if hdr_rest_id and not data.get("restaurant_id"):
                data["restaurant_id"] = hdr_rest_id
            hdr_session = request.META.get("HTTP_X_SESSION_ID")
            if hdr_session and not data.get("sessionId"):
                data["sessionId"] = hdr_session
            hdr_token = request.META.get("HTTP_X_CONTEXT_TOKEN") or request.META.get("HTTP_X_USER_TOKEN")
            if hdr_token and not data.get("token"):
                data["token"] = hdr_token
        except Exception:
            pass
    meta = data.get("metadata") if isinstance(data.get("metadata"), dict) else {}
    return data, meta


def _get_first(data: dict, meta: dict, *keys: str):
    for k in keys:
        v = data.get(k)
        if v:
            if isinstance(v, (list, tuple)) and len(v) > 0:
                return v[0]
            return v
    for k in keys:
        v = meta.get(k)
        if v:
            if isinstance(v, (list, tuple)) and len(v) > 0:
                return v[0]
            return v
    return None


def _extract_whatsapp_sender_phone(d: dict):
    try:
        if not isinstance(d, dict):
            return None
        if d.get("from"):
            return d.get("from")
        if d.get("phoneNumber"):
            return d.get("phoneNumber")
        msgs = d.get("messages")
        if isinstance(msgs, list) and msgs:
            m0 = msgs[0] if isinstance(msgs[0], dict) else {}
            if m0.get("from"):
                return m0.get("from")
        contacts = d.get("contacts")
        if isinstance(contacts, list) and contacts:
            c0 = contacts[0] if isinstance(contacts[0], dict) else {}
            if c0.get("wa_id"):
                return c0.get("wa_id")
            if c0.get("waId"):
                return c0.get("waId")
    except Exception:
        return None
    return None


def _phone_digits_for_cache(data: dict, meta: dict) -> str | None:
    phone = _get_first(
        data,
        meta,
        "phone",
        "phoneNumber",
        "mobileNumber",
        "reporter_phone",
        "reporterPhone",
        "from",
        "wa_id",
        "waId",
    )
    if not phone:
        phone = _extract_whatsapp_sender_phone(data) or _extract_whatsapp_sender_phone(meta)
    if not phone:
        return None
    digits = "".join(filter(str.isdigit, str(phone)))
    return digits[-12:] if len(digits) >= 6 else None


def _cache_keys_to_try(data: dict, meta: dict) -> list[str]:
    keys: list[str] = []
    session_id = _get_first(data, meta, "sessionId", "session_id")
    if session_id:
        h = hashlib.sha256(str(session_id).encode("utf-8")).hexdigest()[:40]
        keys.append(f"agent:resolve:v4:s:{h}")
    user_id = _get_first(data, meta, "userId", "user_id", "staffId", "staff_id")
    if user_id:
        keys.append(f"agent:resolve:v4:u:{str(user_id).strip()}")
    pd = _phone_digits_for_cache(data, meta)
    if pd:
        keys.append(f"agent:resolve:v4:p:{pd}")
    token = _get_first(data, meta, "token", "accessToken", "access_token")
    if token:
        th = hashlib.sha256(str(token).encode("utf-8")).hexdigest()[:40]
        keys.append(f"agent:resolve:v4:j:{th}")
    email = _get_first(data, meta, "email", "emailAddress")
    if email:
        eh = hashlib.sha256(str(email).strip().lower().encode("utf-8")).hexdigest()[:24]
        keys.append(f"agent:resolve:v4:e:{eh}")
    restaurant_id = _get_first(data, meta, "restaurant_id", "restaurantId", "restaurant")
    if restaurant_id:
        keys.append(f"agent:resolve:v4:r:{str(restaurant_id).strip()}")
    return keys


def _hydrate_resolution(blob) -> tuple:
    if not blob or not isinstance(blob, dict) or not blob.get("rid"):
        return None, None
    from accounts.models import CustomUser, Restaurant

    rid = blob["rid"]
    uid = blob.get("uid")
    try:
        if uid:
            u = CustomUser.objects.select_related("restaurant").filter(id=uid).first()
            if u and getattr(u, "restaurant", None):
                return u.restaurant, u
        r = Restaurant.objects.filter(id=rid).first()
        if r:
            return r, None
    except Exception:
        pass
    return None, None


def _try_cached_resolution(data: dict, meta: dict):
    for ck in _cache_keys_to_try(data, meta):
        hit = safe_cache_get(ck)
        if hit:
            r, u = _hydrate_resolution(hit)
            if r is not None:
                return r, u
    return None, None


def _store_resolution_cache(data: dict, meta: dict, restaurant, user) -> None:
    if not restaurant:
        return
    blob = {"rid": str(restaurant.id), "uid": str(user.id) if user else None}
    for ck in _cache_keys_to_try(data, meta):
        safe_cache_set(ck, blob, _RESOLVE_TTL)


def _resolve_uncached(data: dict, meta: dict):
    from accounts.models import CustomUser, Restaurant

    # 1) Direct restaurant id
    restaurant_id = _get_first(data, meta, "restaurant_id", "restaurantId", "restaurant")
    if restaurant_id:
        try:
            return Restaurant.objects.get(id=restaurant_id), None
        except Exception:
            pass

    # 2) SessionId pattern
    session_id = _get_first(data, meta, "sessionId", "session_id")
    if session_id:
        m = re.search(r"tenant-([0-9a-fA-F-]{8,})-user-([0-9a-fA-F-]{8,})", str(session_id))
        if m:
            rest_id = m.group(1)
            user_id = m.group(2)
            user_obj = None
            try:
                user_obj = CustomUser.objects.filter(id=user_id).select_related("restaurant").first()
            except Exception:
                user_obj = None
            if user_obj and getattr(user_obj, "restaurant_id", None):
                return user_obj.restaurant, user_obj
            try:
                rest_obj = Restaurant.objects.get(id=rest_id)
                return rest_obj, user_obj
            except Exception:
                pass

    # 3) UserId
    user_id = _get_first(data, meta, "userId", "user_id", "staffId", "staff_id")
    if user_id:
        try:
            user_obj = CustomUser.objects.filter(id=user_id).select_related("restaurant").first()
            if user_obj and user_obj.restaurant:
                return user_obj.restaurant, user_obj
        except Exception:
            pass

    # 4) Email
    email = _get_first(data, meta, "email", "emailAddress")
    if email:
        try:
            user_obj = (
                CustomUser.objects.filter(email__iexact=str(email).strip()).select_related("restaurant").first()
            )
            if user_obj and user_obj.restaurant:
                return user_obj.restaurant, user_obj
        except Exception:
            pass

    # 5) Phone
    phone = _get_first(
        data,
        meta,
        "phone",
        "phoneNumber",
        "mobileNumber",
        "reporter_phone",
        "reporterPhone",
        "from",
        "wa_id",
        "waId",
    )
    if not phone:
        phone = _extract_whatsapp_sender_phone(data) or _extract_whatsapp_sender_phone(meta)
    if phone:
        digits = "".join(filter(str.isdigit, str(phone)))
        patterns = [digits, digits[-10:] if len(digits) > 10 else digits, f"+{digits}"]
        try:
            for p in patterns:
                user_obj = CustomUser.objects.filter(phone__icontains=p).select_related("restaurant").first()
                if user_obj and user_obj.restaurant:
                    return user_obj.restaurant, user_obj
        except Exception:
            pass

    # 6) JWT
    token = _get_first(data, meta, "token", "accessToken", "access_token")
    if token:
        try:
            from rest_framework_simplejwt.authentication import JWTAuthentication

            jwt_auth = JWTAuthentication()
            validated = jwt_auth.get_validated_token(str(token))
            user_obj = jwt_auth.get_user(validated)
            if user_obj and getattr(user_obj, "restaurant", None):
                return user_obj.restaurant, user_obj
        except Exception:
            pass

    return None, None


def resolve_agent_restaurant_and_user(request=None, payload=None):
    """
    Resolve (restaurant, user) for agent-authenticated endpoints.
    Cached by sessionId / userId / phone / JWT / email / restaurant_id keys (TTL 180s).
    """
    data, meta = _merge_agent_request_data(request, payload)
    r, u = _try_cached_resolution(data, meta)
    if r is not None:
        return r, u
    r, u = _resolve_uncached(data, meta)
    if r is not None:
        _store_resolution_cache(data, meta, r, u)
    return r, u
