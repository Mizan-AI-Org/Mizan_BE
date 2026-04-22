"""
Toast POS connect/disconnect — partner-credentials flow.

Unlike Square/Clover, Toast's API does NOT expose a per-merchant OAuth
redirect. Instead, partners receive a single ``client_id`` /
``client_secret`` and call the ``/authentication/v1/authentication/login``
endpoint with the grant ``CLIENT_CREDENTIALS`` to obtain a short-lived
(~60 min) access token that is then used with the restaurant's
``Toast-Restaurant-External-ID`` header to read that specific location's
orders, menus, and labor data.

Connect flow for a tenant:
1. Manager clicks "Connect Toast" → frontend calls ``POST /api/pos/toast/connect/``
   with ``{"restaurant_guid": "<uuid-from-Toast>"}``.
2. Backend verifies it can mint a token using partner creds + the supplied
   restaurantGuid. If the call succeeds, the GUID is saved on the
   ``Restaurant`` row under ``pos_oauth_data['toast']`` and the restaurant
   is marked connected. If Toast returns 401, we surface a clear error
   without persisting anything.
3. Subsequent API calls from :mod:`pos.integrations` mint tokens on demand
   (cached in memory) — we never persist tokens for Toast because the
   grant is cheap and keeps the secret material out of the database.
"""

from __future__ import annotations

import logging
import re

import requests
from django.conf import settings
from django.utils import timezone
from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

logger = logging.getLogger(__name__)

# Toast login host differs by environment. Both accept the same payload
# shape but issue tokens scoped to their own world.
_TOAST_HOSTS = {
    "production": "https://ws-api.toasttab.com",
    "sandbox": "https://ws-sandbox-api.eng.toasttab.com",
}

_GUID_RE = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def toast_host() -> str:
    env = (getattr(settings, "TOAST_ENV", "sandbox") or "sandbox").lower()
    return _TOAST_HOSTS.get(env, _TOAST_HOSTS["sandbox"])


def toast_partner_configured() -> bool:
    return bool(
        getattr(settings, "TOAST_CLIENT_ID", "")
        and getattr(settings, "TOAST_CLIENT_SECRET", "")
    )


def fetch_toast_access_token() -> dict | None:
    """Mint a Toast access token with the partner credentials.

    Returns ``{"access_token": str, "expires_at": iso, "expires_in": int}``
    on success, ``None`` on failure. The token is shared across all
    restaurants onboarded under the same partner account — per-restaurant
    scoping happens via the ``Toast-Restaurant-External-ID`` header on
    each downstream request.
    """
    if not toast_partner_configured():
        return None

    try:
        resp = requests.post(
            f"{toast_host()}/authentication/v1/authentication/login",
            json={
                "clientId": settings.TOAST_CLIENT_ID,
                "clientSecret": settings.TOAST_CLIENT_SECRET,
                "userAccessType": "TOAST_MACHINE_CLIENT",
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        logger.warning("Toast auth request failed: %s", exc)
        return None

    if resp.status_code != 200:
        logger.warning(
            "Toast auth non-200: %s %s",
            resp.status_code,
            (resp.text or "")[:200],
        )
        return None

    body = resp.json() if resp.content else {}
    token = ((body.get("token") or {}).get("accessToken")) or body.get("accessToken")
    expires_in = ((body.get("token") or {}).get("expiresIn")) or body.get("expiresIn") or 3000
    if not token:
        return None

    from datetime import timedelta

    expires_at = (timezone.now() + timedelta(seconds=int(expires_in) - 60)).isoformat()
    return {"access_token": token, "expires_at": expires_at, "expires_in": int(expires_in)}


def _probe_restaurant(access_token: str, restaurant_guid: str) -> bool:
    """Verify the supplied restaurantGuid by calling a cheap read endpoint.

    We hit ``/restaurants/v1/restaurants/{guid}`` which returns 200 for
    valid GUIDs under the partner's scope and 404/403 otherwise.
    """
    try:
        resp = requests.get(
            f"{toast_host()}/restaurants/v1/restaurants/{restaurant_guid}",
            headers={
                "Authorization": f"Bearer {access_token}",
                "Toast-Restaurant-External-ID": restaurant_guid,
            },
            timeout=10,
        )
    except requests.RequestException:
        return False
    return resp.status_code == 200


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def toast_connect(request):
    """Save the restaurant's Toast GUID after verifying it with Toast.

    Body: ``{"restaurant_guid": "<uuid>"}``
    """
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response(
            {"error": "No restaurant associated with your account."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    if not toast_partner_configured():
        return Response(
            {
                "configured": False,
                "detail": (
                    "Toast is not available right now. Please try again "
                    "later or contact your administrator."
                ),
            },
            status=status.HTTP_501_NOT_IMPLEMENTED,
        )

    guid = (request.data.get("restaurant_guid") or "").strip()
    if not guid or not _GUID_RE.match(guid):
        return Response(
            {"error": "A valid Toast restaurant GUID is required."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    token_data = fetch_toast_access_token()
    if not token_data:
        return Response(
            {"error": "Toast authentication failed. Check partner credentials."},
            status=status.HTTP_502_BAD_GATEWAY,
        )

    if not _probe_restaurant(token_data["access_token"], guid):
        return Response(
            {
                "error": (
                    "Toast rejected this restaurant GUID. Make sure the "
                    "restaurant is provisioned to your partner account."
                )
            },
            status=status.HTTP_400_BAD_REQUEST,
        )

    restaurant.pos_provider = "TOAST"
    restaurant.pos_merchant_id = guid
    restaurant.pos_is_connected = True
    restaurant.pos_api_key = ""  # Toast uses partner creds, not per-tenant keys.
    restaurant.set_toast_oauth(
        {
            "restaurant_guid": guid,
            "cached_access_token": token_data["access_token"],
            "expires_at": token_data["expires_at"],
            "connected_at": timezone.now().isoformat(),
        }
    )
    restaurant.save()

    logger.info("Toast connected for restaurant %s (guid=%s)", restaurant.id, guid)

    return Response(
        {
            "success": True,
            "provider": "TOAST",
            "restaurant_guid": guid,
        }
    )


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def toast_disconnect(request):
    """Remove Toast credentials from the restaurant."""
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"error": "No restaurant associated."}, status=status.HTTP_400_BAD_REQUEST)

    if restaurant.pos_provider != "TOAST":
        return Response(
            {"error": "Restaurant is not connected to Toast."},
            status=status.HTTP_400_BAD_REQUEST,
        )

    # Clear Toast-specific data while preserving other providers' records
    # stored under the same encrypted envelope.
    root = restaurant.get_pos_oauth() or {}
    root.pop("toast", None)
    restaurant.set_pos_oauth(root)

    restaurant.pos_provider = "NONE"
    restaurant.pos_is_connected = False
    restaurant.pos_merchant_id = ""
    restaurant.pos_location_id = ""
    restaurant.pos_api_key = ""
    restaurant.pos_token_expires_at = None
    restaurant.save()

    return Response({"success": True, "message": "Toast POS disconnected."})
