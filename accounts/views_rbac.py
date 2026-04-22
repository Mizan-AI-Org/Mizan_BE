"""
REST endpoints for tenant Role-Based Access Control.

Role-level (default scope):
- GET  /api/rbac/catalog/               — list of apps / widgets / actions the
                                          admin UI can toggle, plus per-role
                                          defaults.
- GET  /api/rbac/role-permissions/      — stored permission sets for the
                                          current tenant (one row per role).
- PUT  /api/rbac/role-permissions/<role>/ — upsert permissions for a role.
- DELETE /api/rbac/role-permissions/<role>/ — reset a role back to defaults.

User-level (per-user overrides — take priority over role-level):
- GET  /api/rbac/user-permissions/assignable/ — users the admin may override
                                                 (scoped to tenant, excludes
                                                 privileged roles).
- GET  /api/rbac/user-permissions/      — stored per-user overrides for the
                                          current tenant.
- GET  /api/rbac/user-permissions/<user_id>/ — a user's effective permissions
                                                + whether an override is
                                                saved.
- PUT  /api/rbac/user-permissions/<user_id>/ — upsert override for one user.
- DELETE /api/rbac/user-permissions/<user_id>/ — reset override for one user.
- POST /api/rbac/user-permissions/bulk/ — apply the SAME permissions to many
                                          users at once. Body:
                                            {"user_ids": [...],
                                             "permissions": {"apps": [...],
                                                             "widgets": [...],
                                                             "actions": [...]}}

- GET  /api/rbac/me/                    — effective permissions for the logged-
                                          in user. Resolution order:
                                          privileged → user → role → defaults.

SUPER_ADMIN / ADMIN / OWNER are always full-access regardless of storage.
"""

from __future__ import annotations

import logging
import uuid as _uuid

from django.db import transaction
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from .models import CustomUser, RolePermissionSet, UserPermissionSet
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
    """Only SUPER_ADMIN / ADMIN / OWNER may write role / user permissions."""
    return bool(user and getattr(user, "is_authenticated", False) and user.role in PRIVILEGED_ROLES)


def _serialize_role_set(row: RolePermissionSet) -> dict:
    perms = sanitize_permissions(row.permissions)
    return {
        "role": row.role,
        "permissions": perms,
        "updated_by": str(row.updated_by_id) if row.updated_by_id else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }


def _user_summary(user: CustomUser) -> dict:
    full_name = (user.get_full_name() or "").strip() or user.email or str(user.id)
    return {
        "id": str(user.id),
        "email": user.email or "",
        "full_name": full_name,
        "first_name": user.first_name or "",
        "last_name": user.last_name or "",
        "role": (user.role or "").upper(),
        "is_active": bool(user.is_active),
    }


def _serialize_user_set(row: UserPermissionSet) -> dict:
    perms = sanitize_permissions(row.permissions)
    payload = {
        "user_id": str(row.user_id),
        "permissions": perms,
        "updated_by": str(row.updated_by_id) if row.updated_by_id else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
    }
    # Include a lightweight user summary when the related row is loaded so the
    # UI doesn't need a second round-trip.
    user = getattr(row, "user", None)
    if user is not None:
        payload["user"] = _user_summary(user)
    return payload


def _role_permissions_for(restaurant, role: str) -> tuple[dict, str]:
    """Return (permissions, source) for a given (tenant, role) combo."""
    role_up = (role or "").upper()
    if restaurant is not None:
        row = (
            RolePermissionSet.objects.filter(restaurant=restaurant, role=role_up)
            .only("permissions")
            .first()
        )
        if row is not None:
            return sanitize_permissions(row.permissions), "tenant"
    return default_permissions_for(role_up), "defaults"


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
    """List stored role-level permission sets for the current tenant."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"results": []})

        rows = RolePermissionSet.objects.filter(restaurant=restaurant).order_by("role")
        return Response({"results": [_serialize_role_set(r) for r in rows]})


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
        logger.info("RBAC role permissions updated for %s / %s by %s", restaurant.id, role_up, request.user.id)
        return Response(_serialize_role_set(row))

    def delete(self, request, role: str):
        """Reset a role back to catalog defaults by removing the override row."""
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"detail": "No tenant"}, status=status.HTTP_400_BAD_REQUEST)

        RolePermissionSet.objects.filter(restaurant=restaurant, role=(role or "").upper()).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


# ---------------------------------------------------------------------------
# Per-user overrides
# ---------------------------------------------------------------------------


class AssignableUsersView(APIView):
    """
    List staff in the current tenant that can receive per-user permission
    overrides. Privileged users (SUPER_ADMIN / ADMIN / OWNER) are never
    included: they always have full access.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"results": []})

        qs = (
            CustomUser.objects.filter(restaurant=restaurant, is_active=True)
            .exclude(role__in=PRIVILEGED_ROLES)
            .order_by("first_name", "last_name", "email")
        )

        # Mark users who already have a stored override.
        overridden_ids = set(
            UserPermissionSet.objects.filter(restaurant=restaurant).values_list("user_id", flat=True)
        )
        results = []
        for u in qs:
            entry = _user_summary(u)
            entry["has_override"] = str(u.id) in {str(x) for x in overridden_ids}
            results.append(entry)
        return Response({"results": results})


class UserPermissionListView(APIView):
    """List stored per-user permission overrides for the current tenant."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"results": []})

        rows = (
            UserPermissionSet.objects.filter(restaurant=restaurant)
            .select_related("user")
            .order_by("user__first_name", "user__last_name")
        )
        return Response({"results": [_serialize_user_set(r) for r in rows]})


class UserPermissionDetailView(APIView):
    """Inspect / upsert / reset a single user's override."""

    permission_classes = [permissions.IsAuthenticated]

    def _get_target_user(self, request, user_id):
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return None, Response({"detail": "No tenant"}, status=status.HTTP_400_BAD_REQUEST)
        try:
            user = CustomUser.objects.get(id=user_id, restaurant=restaurant)
        except (CustomUser.DoesNotExist, ValueError):
            return None, Response({"detail": "User not found"}, status=status.HTTP_404_NOT_FOUND)
        if (user.role or "").upper() in PRIVILEGED_ROLES:
            return None, Response(
                {"detail": "Privileged users always have full access and cannot be overridden."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        return user, None

    def get(self, request, user_id):
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        user, err = self._get_target_user(request, user_id)
        if err is not None:
            return err

        row = UserPermissionSet.objects.filter(restaurant=user.restaurant, user=user).first()
        role_perms, role_source = _role_permissions_for(user.restaurant, user.role)
        if row is not None:
            return Response(
                {
                    "user": _user_summary(user),
                    "permissions": sanitize_permissions(row.permissions),
                    "source": "user",
                    "has_override": True,
                    "role_permissions": role_perms,
                    "role_source": role_source,
                    "updated_by": str(row.updated_by_id) if row.updated_by_id else None,
                    "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                }
            )
        return Response(
            {
                "user": _user_summary(user),
                "permissions": role_perms,
                "source": role_source,
                "has_override": False,
                "role_permissions": role_perms,
                "role_source": role_source,
                "updated_by": None,
                "updated_at": None,
            }
        )

    def put(self, request, user_id):
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        user, err = self._get_target_user(request, user_id)
        if err is not None:
            return err

        payload = request.data or {}
        perms = sanitize_permissions(payload.get("permissions") or payload)

        row, _ = UserPermissionSet.objects.update_or_create(
            restaurant=user.restaurant,
            user=user,
            defaults={
                "permissions": perms,
                "updated_by": request.user if request.user.is_authenticated else None,
            },
        )
        logger.info(
            "RBAC user permissions updated for tenant=%s user=%s by=%s",
            user.restaurant_id, user.id, request.user.id,
        )
        row.user = user  # hydrate for serializer.
        return Response(_serialize_user_set(row))

    def delete(self, request, user_id):
        """Reset a user's override so they fall back to role-level permissions."""
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        user, err = self._get_target_user(request, user_id)
        if err is not None:
            return err

        UserPermissionSet.objects.filter(restaurant=user.restaurant, user=user).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class UserPermissionBulkView(APIView):
    """Apply the same permissions to many users at once.

    Body:
      {
        "user_ids": ["<uuid>", "<uuid>", ...],
        "permissions": {"apps": [...], "widgets": [...], "actions": [...]}
      }
    """

    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        if not _can_edit_permissions(request.user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)
        restaurant = getattr(request.user, "restaurant", None)
        if restaurant is None:
            return Response({"detail": "No tenant"}, status=status.HTTP_400_BAD_REQUEST)

        payload = request.data or {}
        raw_ids = payload.get("user_ids") or []
        if not isinstance(raw_ids, list) or not raw_ids:
            return Response(
                {"detail": "Provide a non-empty 'user_ids' list."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user_ids: list[str] = []
        invalid_ids: list[str] = []
        for raw in raw_ids:
            s = str(raw).strip()
            if not s:
                continue
            try:
                _uuid.UUID(s)
            except (ValueError, TypeError):
                invalid_ids.append(s)
                continue
            user_ids.append(s)

        perms = sanitize_permissions(payload.get("permissions") or {})

        users = list(
            CustomUser.objects.filter(restaurant=restaurant, id__in=user_ids)
            .exclude(role__in=PRIVILEGED_ROLES)
        )
        found_ids = {str(u.id) for u in users}
        missing = [uid for uid in user_ids if uid not in found_ids] + invalid_ids

        saved_rows = []
        with transaction.atomic():
            for u in users:
                row, _ = UserPermissionSet.objects.update_or_create(
                    restaurant=restaurant,
                    user=u,
                    defaults={
                        "permissions": perms,
                        "updated_by": request.user if request.user.is_authenticated else None,
                    },
                )
                row.user = u  # hydrate.
                saved_rows.append(row)

        logger.info(
            "RBAC bulk user permissions: tenant=%s count=%d missing=%d by=%s",
            restaurant.id, len(saved_rows), len(missing), request.user.id,
        )

        return Response(
            {
                "results": [_serialize_user_set(r) for r in saved_rows],
                "applied_count": len(saved_rows),
                "missing_user_ids": missing,
            }
        )


class EffectivePermissionsView(APIView):
    """
    Resolve effective permissions for the current user.

    Resolution order:
      1. Privileged roles (SUPER_ADMIN / ADMIN / OWNER) → full permissions.
      2. Stored UserPermissionSet for (tenant, user)    → stored permissions.
      3. Stored RolePermissionSet for (tenant, role)    → stored permissions.
      4. Otherwise                                      → catalog defaults.
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

        # 2. User-level override.
        if restaurant is not None:
            user_row = (
                UserPermissionSet.objects.filter(restaurant=restaurant, user=user)
                .only("permissions")
                .first()
            )
            if user_row is not None:
                return Response(
                    {
                        "role": role,
                        "source": "user",
                        "permissions": sanitize_permissions(user_row.permissions),
                    }
                )

        # 3 + 4. Role-level override or defaults.
        perms, source = _role_permissions_for(restaurant, role)
        return Response({"role": role, "source": source, "permissions": perms})
