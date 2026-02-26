"""
Square OAuth connect/disconnect flow — per-restaurant, with strict tenant isolation.

Flow:
1. Manager clicks "Connect Square" → frontend calls /api/pos/square/authorize/
2. Backend generates Square OAuth URL with restaurant_id in encrypted state param
3. Manager grants permissions on Square
4. Square redirects to /api/pos/square/callback/ with code + state
5. Backend exchanges code for tokens, stores them per-restaurant, marks connected
"""

import hashlib
import hmac
import json
import logging
import secrets
import time

import requests
from django.conf import settings
from django.http import HttpResponseRedirect
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from accounts.models import Restaurant

logger = logging.getLogger(__name__)

SQUARE_OAUTH_SCOPES = [
    "PAYMENTS_READ",
    "ORDERS_READ",
    "ORDERS_WRITE",
    "ITEMS_READ",
    "ITEMS_WRITE",
    "MERCHANT_PROFILE_READ",
    "INVENTORY_READ",
    "EMPLOYEES_READ",
]


def _square_base_url():
    env = getattr(settings, "SQUARE_ENV", "production")
    return "https://connect.squareup.com" if env == "production" else "https://connect.squareupsandbox.com"


def _build_state(restaurant_id: str) -> str:
    """Create signed state param to prevent CSRF and carry restaurant_id."""
    nonce = secrets.token_hex(16)
    payload = f"{restaurant_id}:{nonce}:{int(time.time())}"
    secret = getattr(settings, "SECRET_KEY", "fallback")
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
    return f"{payload}:{sig}"


def _verify_state(state: str) -> str | None:
    """Verify state param, return restaurant_id or None."""
    try:
        parts = state.rsplit(":", 3)
        if len(parts) != 4:
            return None
        restaurant_id, nonce, ts_str, sig = parts
        payload = f"{restaurant_id}:{nonce}:{ts_str}"
        secret = getattr(settings, "SECRET_KEY", "fallback")
        expected_sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()[:16]
        if not hmac.compare_digest(sig, expected_sig):
            return None
        if abs(time.time() - int(ts_str)) > 600:
            return None
        return restaurant_id
    except Exception:
        return None


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def square_authorize(request):
    """Generate Square OAuth authorization URL for the authenticated user's restaurant."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"error": "No restaurant associated with your account."}, status=status.HTTP_400_BAD_REQUEST)

    app_id = getattr(settings, "SQUARE_APPLICATION_ID", "")
    redirect_uri = getattr(settings, "SQUARE_REDIRECT_URI", "")
    if not app_id or not redirect_uri:
        return Response(
            {"error": "Square integration is not configured. Contact support.",
             "detail": "Square OAuth credentials are not configured. Contact your administrator."},
            status=status.HTTP_503_SERVICE_UNAVAILABLE,
        )

    scopes_str = "+".join(SQUARE_OAUTH_SCOPES)
    state = _build_state(str(restaurant.id))

    auth_url = (
        f"{_square_base_url()}/oauth2/authorize"
        f"?client_id={app_id}"
        f"&scope={scopes_str}"
        f"&session=false"
        f"&state={state}"
        f"&redirect_uri={redirect_uri}"
    )

    return Response({"authorization_url": auth_url, "state": state})


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def square_callback(request):
    """Handle Square OAuth callback — exchange code for tokens, store per-restaurant."""
    code = request.query_params.get("code")
    state = request.query_params.get("state", "")
    error = request.query_params.get("error")
    error_description = request.query_params.get("error_description", "")

    frontend_base = getattr(settings, "FRONTEND_URL", "http://localhost:8080")
    settings_url = f"{frontend_base}/dashboard/settings?tab=pos"

    if error:
        logger.warning("Square OAuth error: %s — %s", error, error_description)
        return HttpResponseRedirect(f"{settings_url}&pos_error={error}")

    if not code:
        return HttpResponseRedirect(f"{settings_url}&pos_error=no_code")

    restaurant_id = _verify_state(state)
    if not restaurant_id:
        logger.warning("Square OAuth: invalid or expired state param")
        return HttpResponseRedirect(f"{settings_url}&pos_error=invalid_state")

    try:
        restaurant = Restaurant.objects.get(id=restaurant_id)
    except Restaurant.DoesNotExist:
        return HttpResponseRedirect(f"{settings_url}&pos_error=restaurant_not_found")

    app_id = getattr(settings, "SQUARE_APPLICATION_ID", "")
    app_secret = getattr(settings, "SQUARE_APPLICATION_SECRET", "")
    redirect_uri = getattr(settings, "SQUARE_REDIRECT_URI", "")

    try:
        token_resp = requests.post(
            f"{_square_base_url()}/oauth2/token",
            json={
                "client_id": app_id,
                "client_secret": app_secret,
                "code": code,
                "grant_type": "authorization_code",
                "redirect_uri": redirect_uri,
            },
            timeout=15,
        )
        token_data = token_resp.json() if token_resp.content else {}
        token_resp.raise_for_status()
    except Exception as exc:
        logger.exception("Square OAuth token exchange failed for restaurant %s", restaurant_id)
        return HttpResponseRedirect(f"{settings_url}&pos_error=token_exchange_failed")

    access_token = token_data.get("access_token", "")
    refresh_token = token_data.get("refresh_token", "")
    expires_at = token_data.get("expires_at", "")
    merchant_id = token_data.get("merchant_id", "")

    expires_dt = None
    if expires_at:
        try:
            expires_dt = parse_datetime(expires_at)
        except Exception:
            pass

    restaurant.pos_provider = "SQUARE"
    restaurant.pos_merchant_id = merchant_id
    restaurant.pos_is_connected = True
    restaurant.pos_token_expires_at = expires_dt
    restaurant.set_square_oauth({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "expires_at": expires_at,
        "merchant_id": merchant_id,
    })

    location_id = _fetch_main_location(access_token)
    if location_id:
        restaurant.pos_location_id = location_id

    restaurant.save()

    logger.info(
        "Square OAuth connected for restaurant %s (merchant: %s, location: %s)",
        restaurant_id, merchant_id, location_id or "auto-detect",
    )

    return HttpResponseRedirect(f"{settings_url}&pos_connected=true")


def _fetch_main_location(access_token: str) -> str | None:
    """Fetch the main location_id from Square for this merchant."""
    try:
        env = getattr(settings, "SQUARE_ENV", "production")
        host = "https://connect.squareup.com" if env == "production" else "https://connect.squareupsandbox.com"
        resp = requests.get(
            f"{host}/v2/locations",
            headers={"Authorization": f"Bearer {access_token}", "Square-Version": "2024-01-18"},
            timeout=10,
        )
        data = resp.json() if resp.content else {}
        locations = data.get("locations") or []
        main = next((loc for loc in locations if loc.get("status") == "ACTIVE"), None)
        if main:
            return main.get("id")
        if locations:
            return locations[0].get("id")
    except Exception:
        pass
    return None


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def square_disconnect(request):
    """Disconnect Square POS for the authenticated user's restaurant."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"error": "No restaurant associated."}, status=status.HTTP_400_BAD_REQUEST)

    if restaurant.pos_provider != "SQUARE":
        return Response({"error": "Restaurant is not connected to Square."}, status=status.HTTP_400_BAD_REQUEST)

    access_token = restaurant.get_square_access_token()
    if access_token:
        try:
            app_id = getattr(settings, "SQUARE_APPLICATION_ID", "")
            requests.post(
                f"{_square_base_url()}/oauth2/revoke",
                json={"client_id": app_id, "access_token": access_token},
                headers={"Authorization": f"Client {getattr(settings, 'SQUARE_APPLICATION_SECRET', '')}"},
                timeout=10,
            )
        except Exception:
            logger.warning("Failed to revoke Square token for restaurant %s", restaurant.id)

    restaurant.pos_provider = "NONE"
    restaurant.pos_is_connected = False
    restaurant.pos_merchant_id = ""
    restaurant.pos_location_id = ""
    restaurant.pos_api_key = ""
    restaurant.pos_oauth_data = ""
    restaurant.pos_token_expires_at = None
    restaurant.save()

    return Response({"success": True, "message": "Square POS disconnected."})


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def pos_connection_status(request):
    """Return current POS connection status for the authenticated user's restaurant."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"error": "No restaurant associated."}, status=status.HTTP_400_BAD_REQUEST)

    return Response({
        "provider": restaurant.pos_provider or "NONE",
        "is_connected": restaurant.pos_is_connected,
        "merchant_id": restaurant.pos_merchant_id or None,
        "location_id": restaurant.pos_location_id or None,
        "last_sync": restaurant.pos_token_expires_at.isoformat() if restaurant.pos_token_expires_at else None,
    })
