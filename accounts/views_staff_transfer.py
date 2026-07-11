"""
Move staff between branches (BusinessLocation) within a tenant.

Supports single and bulk moves of ``primary_location``, with optional
adjustment of ``allowed_locations``.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.shortcuts import get_object_or_404
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import BusinessLocation, CustomUser
from .permissions import IsAdminOrManager
from .serializers import CustomUserSerializer

logger = logging.getLogger(__name__)


class StaffTransferLocationsView(APIView):
    """
    POST /api/staff/transfer-locations/

    Body:
      staff_ids: string[] (1+)
      primary_location: UUID of destination BusinessLocation
      allowed_mode: "unchanged" | "set_destination_only" | "add_destination"
        - unchanged: leave allowed_locations as-is
        - set_destination_only: allowed_locations = [destination] (or empty if primary-only)
        - add_destination: add destination to allowed_locations if not already present
    """

    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def post(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response(
                {"success": False, "error": "No restaurant context"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        staff_ids = request.data.get("staff_ids") or request.data.get("staffIds") or []
        if isinstance(staff_ids, str):
            staff_ids = [staff_ids]
        staff_ids = [str(s).strip() for s in staff_ids if str(s).strip()]
        if not staff_ids:
            return Response(
                {"success": False, "error": "staff_ids is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(staff_ids) > 100:
            return Response(
                {"success": False, "error": "You can move at most 100 staff at a time"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        dest_id = (
            request.data.get("primary_location")
            or request.data.get("primaryLocation")
            or request.data.get("destination_location")
        )
        if not dest_id:
            return Response(
                {"success": False, "error": "primary_location is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        allowed_mode = str(
            request.data.get("allowed_mode")
            or request.data.get("allowedMode")
            or "add_destination"
        ).strip().lower()
        if allowed_mode not in ("unchanged", "set_destination_only", "add_destination"):
            allowed_mode = "add_destination"

        destination = get_object_or_404(
            BusinessLocation,
            id=dest_id,
            restaurant=restaurant,
            is_active=True,
        )

        # Branch-scoped managers may only move people within their managed sites.
        actor = request.user
        managed_ids = set()
        if getattr(actor, "role", None) == "MANAGER":
            managed_ids = set(
                str(x) for x in actor.managed_locations.values_list("id", flat=True)
            )
            if managed_ids and str(destination.id) not in managed_ids:
                return Response(
                    {
                        "success": False,
                        "error": "You can only move staff to branches you manage",
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        qs = CustomUser.objects.filter(
            id__in=staff_ids,
            restaurant=restaurant,
            is_active=True,
        ).select_related("primary_location")

        found = {str(u.id): u for u in qs}
        missing = [sid for sid in staff_ids if sid not in found]
        if missing:
            return Response(
                {
                    "success": False,
                    "error": f"Staff not found in this workspace: {', '.join(missing[:5])}",
                    "missing_ids": missing,
                },
                status=status.HTTP_404_NOT_FOUND,
            )

        if managed_ids:
            blocked = [
                str(u.id)
                for u in found.values()
                if u.primary_location_id
                and str(u.primary_location_id) not in managed_ids
            ]
            if blocked:
                return Response(
                    {
                        "success": False,
                        "error": "Some selected staff belong to branches you don't manage",
                        "blocked_ids": blocked,
                    },
                    status=status.HTTP_403_FORBIDDEN,
                )

        moved: list[dict] = []
        with transaction.atomic():
            for uid in staff_ids:
                user = found[uid]
                previous = (
                    {
                        "id": str(user.primary_location.id),
                        "name": user.primary_location.name,
                    }
                    if user.primary_location_id
                    else None
                )
                user.primary_location = destination
                user.save(update_fields=["primary_location"])

                if allowed_mode == "set_destination_only":
                    user.allowed_locations.set([destination])
                elif allowed_mode == "add_destination":
                    user.allowed_locations.add(destination)

                # If this person manages locations and only managed the old
                # branch, add the new destination so they keep access.
                if user.role == "MANAGER" and previous:
                    managed = list(user.managed_locations.all())
                    if managed and all(str(m.id) == previous["id"] for m in managed):
                        user.managed_locations.add(destination)

                moved.append(
                    {
                        "id": str(user.id),
                        "name": user.get_full_name() or user.email,
                        "previous_location": previous,
                        "primary_location": {
                            "id": str(destination.id),
                            "name": destination.name,
                        },
                    }
                )

        logger.info(
            "staff_transfer_locations: restaurant=%s actor=%s dest=%s count=%s",
            restaurant.id,
            actor.id,
            destination.id,
            len(moved),
        )

        # Return refreshed serializer payloads for the first few (UI can refetch list).
        refreshed = CustomUserSerializer(
            CustomUser.objects.filter(id__in=staff_ids),
            many=True,
        ).data

        return Response(
            {
                "success": True,
                "moved_count": len(moved),
                "destination": {
                    "id": str(destination.id),
                    "name": destination.name,
                    "is_primary": bool(destination.is_primary),
                },
                "moved": moved,
                "staff": refreshed,
                "message": (
                    f"Moved {len(moved)} staff member{'s' if len(moved) != 1 else ''} "
                    f"to {destination.name}."
                ),
            },
            status=status.HTTP_200_OK,
        )
