"""
Clover POS OAuth connect/disconnect — authorization code flow.

Flow:
1. Manager clicks "Connect Clover" → frontend GETs ``/api/pos/clover/authorize/``.
2. Backend returns a Clover OAuth URL containing the partner's ``client_id``,
   our ``redirect_uri``, and a signed ``state`` that binds the redirect to
   this exact (user, restaurant) pair.
3. Manager signs in to Clover and authorizes the app; Clover redirects to
   ``/api/pos/clover/callback/?code=...&merchant_id=...&state=...``.
4. Backend verifies the state, exchanges the code for ``access_token`` +
   ``refresh_token``, stores them on the restaurant encrypted, marks
   connected, and redirects the browser back to Settings.

Docs: https://docs.clover.com/docs/using-oauth-20
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time

import requests
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Restaurant

logger = logging.getLogger(__name__)

# Clover hosts differ per environment. Both expose /oauth/authorize for the
# UI redirect and /oauth/token for code exchange.
_CLOVER_HOSTS = {
    "production": {
        "site": "https://www.clover.com",
        "api": "https://api.clover.com",
    },
    "sandbox": {
        "site": "https://sandbox.dev.clover.com",
        "api": "https://apisandbox.dev.clover.com",
    },
}


def _clover_env() -> str:
    return (getattr(settings, "CLOVER_ENV", "sandbox") or "sandbox").lower()


def clover_site_url() -> str:
    hosts = _CLOVER_HOSTS.get(_clover_env(), _CLOVER_HOSTS["sandbox"])
    return hosts["site"]


def clover_api_url() -> str:
    hosts = _CLOVER_HOSTS.get(_clover_env(), _CLOVER_HOSTS["sandbox"])
    return hosts["api"]


def clover_configured() -> bool:
    return bool(
        getattr(settings, "CLOVER_APP_ID", "")
        and getattr(settings, "CLOVER_APP_SECRET", "")
        and getattr(settings, "CLOVER_REDIRECT_URI", "")
    )


def _build_state(restaurant_id: str) -> str:
    """Signed, short-lived state param → CSRF protection + tenant binding."""
    nonce = secrets.token_hex(16)
    payload = f"{restaurant_id}:{nonce}:{int(time.time())}"
    secret = getattr(settings, "SECRET_KEY", "fallback")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"


def _verify_state(state: str) -> str | None:
    """Return restaurant_id if signature is valid and state is fresh (<10 min)."""
    try:
        parts = state.rsplit(":", 3)
        if len(parts) != 4:
            return None
        restaurant_id, nonce, ts_str, sig = parts
        payload = f"{restaurant_id}:{nonce}:{ts_str}"
        secret = getattr(settings, "SECRET_KEY", "fallback")
        expected = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected):
            return None
        if abs(time.time() - int(ts_str)) > 600:
            return None
        return restaurant_id
    except Exception:
        return None


def _frontend_settings_url() -> str:
    frontend_base = getattr(settings, "FRONTEND_URL", "http://localhost:8080")
    return f"{frontend_base}/dashboard/settings?tab=integrations"


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def clover_authorize(request):
    """Return the Clover OAuth URL the browser should navigate to."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response(
            {"error": "No restaurant associated with your account."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not clover_configured():
        return Response(
            {
                "configured": False,
                "detail": (
                    "Clover is not available right now. Please try again "
                    "later or contact your administrator."
                ),
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    state = _build_state(str(restaurant.id))
    # Clover's OAuth authorize endpoint accepts client_id + redirect_uri +
    # state. Scope is declared in the Clover app dashboard, not here.
    auth_url = (
        f"{clover_site_url()}/oauth/authorize"
        f"?client_id={settings.CLOVER_APP_ID}"
        f"&redirect_uri={settings.CLOVER_REDIRECT_URI}"
        f"&state={state}"
    )
    return Response({"authorization_url": auth_url, "state": state, "configured": True})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def clover_callback(request):
    """Exchange the authorization code for tokens and persist on the tenant."""
    settings_url = _frontend_settings_url()

    code = request.query_params.get("code")
    state = request.query_params.get("state", "")
    merchant_id = request.query_params.get("merchant_id", "")
    employee_id = request.query_params.get("employee_id", "")
    error = request.query_params.get("error") or request.query_params.get("error_description")

    if error:
        logger.warning("Clover OAuth error: %s", error)
        return HttpResponseRedirect(f"{settings_url}&pos_error={error}")

    if not code:
        return HttpResponseRedirect(f"{settings_url}&pos_error=no_code")

    restaurant_id = _verify_state(state)
    if not restaurant_id:
        logger.warning("Clover OAuth: invalid or expired state")
        return HttpResponseRedirect(f"{settings_url}&pos_error=invalid_state")

    try:
        restaurant = Restaurant.objects.get(id=restaurant_id)
    except Restaurant.DoesNotExist:
        return HttpResponseRedirect(f"{settings_url}&pos_error=restaurant_not_found")

    if not clover_configured():
        # Shouldn't normally happen (authorize would have failed first)
        # but guard anyway so we don't try to POST empty creds.
        return HttpResponseRedirect(f"{settings_url}&pos_error=not_configured")

    # Clover token exchange — POST /oauth/token on the API host (NOT the
    # customer-facing site). Response includes access_token and, when the
    # app has v2 scopes, refresh_token + access_token_expiration.
    try:
        token_resp = requests.post(
            f"{clover_api_url()}/oauth/v2/token",
            json={
                "client_id": settings.CLOVER_APP_ID,
                "client_secret": settings.CLOVER_APP_SECRET,
                "code": code,
            },
            timeout=15,
        )
        token_data = token_resp.json() if token_resp.content else {}
        # Fall back to v1 endpoint if v2 isn't available on this app.
        if token_resp.status_code == 404 or (not token_data.get("access_token") and "error" not in token_data):
            legacy = requests.get(
                f"{clover_api_url()}/oauth/token",
                params={
                    "client_id": settings.CLOVER_APP_ID,
                    "client_secret": settings.CLOVER_APP_SECRET,
                    "code": code,
                },
                timeout=15,
            )
            token_data = legacy.json() if legacy.content else {}
            token_resp = legacy
        token_resp.raise_for_status()
    except Exception:
        logger.exception("Clover OAuth token exchange failed for restaurant %s", restaurant_id)
        return HttpResponseRedirect(f"{settings_url}&pos_error=token_exchange_failed")

    access_token = token_data.get("access_token") or ""
    refresh_token = token_data.get("refresh_token") or ""
    expires_epoch = token_data.get("access_token_expiration")  # unix seconds (v2 only)

    if not access_token:
        logger.warning("Clover OAuth: no access_token in response (keys=%s)", list(token_data.keys()))
        return HttpResponseRedirect(f"{settings_url}&pos_error=no_access_token")

    expires_dt = None
    if expires_epoch:
        try:
            from datetime import datetime, timezone as dt_tz

            expires_dt = datetime.fromtimestamp(int(expires_epoch), tz=dt_tz.utc)
        except Exception:
            pass

    restaurant.pos_provider = "CLOVER"
    restaurant.pos_merchant_id = merchant_id or ""
    restaurant.pos_is_connected = True
    restaurant.pos_api_key = ""  # We intentionally don't mirror into the legacy field.
    restaurant.pos_token_expires_at = expires_dt
    restaurant.set_clover_oauth(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "expires_at": expires_dt.isoformat() if expires_dt else None,
            "merchant_id": merchant_id,
            "employee_id": employee_id,
            "connected_at": timezone.now().isoformat(),
            "env": _clover_env(),
        }
    )
    restaurant.save()

    logger.info(
        "Clover OAuth connected for restaurant %s (merchant=%s)",
        restaurant_id,
        merchant_id or "-",
    )

    return HttpResponseRedirect(f"{settings_url}&pos_connected=true&provider=clover")


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def clover_disconnect(request):
    """Revoke the Clover token (best-effort) and clear credentials."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"error": "No restaurant associated."}, status=status.HTTP_400_BAD_REQUEST)

    if restaurant.pos_provider != "CLOVER":
        return Response(
            {"error": "Restaurant is not connected to Clover."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token = restaurant.get_clover_access_token()
    if token:
        # Best-effort revoke. Clover may 404 on this endpoint for older
        # apps; we swallow any failure because the local record is
        # already going to be cleared.
        try:
            requests.post(
                f"{clover_api_url()}/oauth/v2/revoke",
                headers={"Authorization": f"Bearer {token}"},
                timeout=10,
            )
        except Exception:
            logger.debug("Clover token revoke failed (best-effort)", exc_info=True)

    root = restaurant.get_pos_oauth() or {}
    root.pop("clover", None)
    restaurant.set_pos_oauth(root)

    restaurant.pos_provider = "NONE"
    restaurant.pos_is_connected = False
    restaurant.pos_merchant_id = ""
    restaurant.pos_location_id = ""
    restaurant.pos_api_key = ""
    restaurant.pos_token_expires_at = None
    restaurant.save()

    return Response({"success": True, "message": "Clover POS disconnected."})
