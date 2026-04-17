"""
REST endpoints for tenant Role-Based Access Control.

- GET  /api/rbac/catalog/               — list of apps / widgets / actions the
                                          admin UI can toggle, plus per-role
                                          defaults.
- GET  /api/rbac/role-permissions/      — stored permission sets for the
                                          current tenant (one row per role).
- PUT  /api/rbac/role-permissions/<role>/ — upsert permissions for a role.
                                          SUPER_ADMIN / ADMIN / OWNER only.
- GET  /api/rbac/me/                    — effective permissions for the logged-
                                          in user. Used by the frontend to
                                          gate nav, widgets and actions.

This pass ships UI/nav gating only: backend views that already enforce
coarse role checks keep doing so, but any client wanting finer-grained
decisions can consult /rbac/me/.
"""

from __future__ import annotations

import logging

from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import RolePermissionSet
from .rbac_catalog import (
    ACTIONS,
    APPS,
    EDITABLE_ROLES,
    PRIVILEGED_ROLES,
    WIDGETS,
    default_permissions_for,
    full_permissions,
    sanitize_permissions,
)

logger = logging.getLogger(__name__)


def _can_edit_permissions(user) -> bool:
    """Only SUPER_ADMIN / ADMIN / OWNER may write role permissions."""
    return bool(user and getattr(user, "is_authenticated", False) and user.role in PRIVILEGED_ROLES)


def _serialize_set(row: RolePermissionSet) -> dict:
    perms = sanitize_permissions(row.permissions)
    return {
        "role": row.role,
        "permissions": perms,
        "updated_by": str(row.updated_by_id) if row.updated_by_id else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


class RBACCatalogView(APIView):
    """Public-to-authenticated catalog of capabilities + role defaults."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        role_defaults = {role: default_permissions_for(role) for role in EDITABLE_ROLES}
        return Response(
            {
                "apps": APPS,
                "widgets": WIDGETS,
                "actions": ACTIONS,
                "editable_roles": EDITABLE_ROLES,
                "privileged_roles": sorted(PRIVILEGED_ROLES),
                "role_defaults": role_defaults,
            }
        )


class RolePermissionListView(APIView):
    """List stored permission sets for the current tenant."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"results": []})

        rows = RolePermissionSet.objects.filter(restaurant=restaurant).order_by("role")
        return Response({"results": [_serialize_set(r) for r in rows]})


class RolePermissionDetailView(APIView):
    """Upsert the permissions for a given role in the current tenant."""

    permission_classes = [permissions.IsAuthenticated]

    def put(self, request, role: str):
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"detail": "No tenant"}, status=status.HTTP_400_BAD_REQUEST)

        role_up = (role or "").upper()
        if role_up not in EDITABLE_ROLES:
            return Response(
                {"detail": f"Role '{role}' is not editable via RBAC UI."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        payload = request.data or {}
        perms = sanitize_permissions(payload.get("permissions") or payload)

        row, _ = RolePermissionSet.objects.update_or_create(
            restaurant=restaurant,
            role=role_up,
            defaults={
                "permissions": perms,
                "updated_by": request.user if request.user.is_authenticated else None,
            },
        )
        logger.info("RBAC permissions updated for %s / %s by %s", restaurant.id, role_up, request.user.id)
        return Response(_serialize_set(row))

    def delete(self, request, role: str):
        """Reset a role back to catalog defaults by removing the override row."""
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"detail": "No tenant"}, status=status.HTTP_400_BAD_REQUEST)

        RolePermissionSet.objects.filter(restaurant=restaurant, role=(role or "").upper()).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class EffectivePermissionsView(APIView):
    """
    Resolve effective permissions for the current user.

    Resolution order:
      1. Privileged roles (SUPER_ADMIN / ADMIN / OWNER) → full permissions.
      2. Stored RolePermissionSet for (tenant, role)    → stored permissions.
      3. Otherwise                                     → catalog defaults.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        role = (getattr(user, "role", "") or "").upper()

        if role in PRIVILEGED_ROLES:
            return Response(
                {
                    "role": role,
                    "source": "privileged",
                    "permissions": full_permissions(),
                }
            )

        restaurant = getattr(user, "restaurant", None)
        source = "defaults"
        perms = default_permissions_for(role)

        if restaurant is not None:
            row = (
                RolePermissionSet.objects.filter(restaurant=restaurant, role=role)
                .only("permissions")
                .first()
            )
            if row is not None:
                perms = sanitize_permissions(row.permissions)
                source = "tenant"

        return Response({"role": role, "source": source, "permissions": perms})
