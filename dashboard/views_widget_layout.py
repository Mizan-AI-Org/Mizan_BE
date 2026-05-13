"""
Persisted dashboard widget layout per user + agent API for Miya/Lua to add widgets
and create custom dashboard tiles (custom:<uuid>).
"""

import logging
import uuid

from django.conf import settings
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from django.db import IntegrityError

from accounts.models import CustomUser
from .models import DashboardCategory, DashboardCustomWidget
from .widget_ids import (
    ALLOWED_CUSTOM_WIDGET_ICONS,
    CUSTOM_WIDGET_PREFIX,
    DASHBOARD_WIDGET_IDS,
    DEFAULT_DASHBOARD_WIDGET_ORDER,
    normalize_agent_widget_id,
)
from .widget_alias_resolver import (
    DATA_BOUND_BUILTIN_IDS,
    resolve_widget_alias,
)
from .widget_link_resolver import ensure_link

logger = logging.getLogger(__name__)


def _validate_agent_key(request):
    expected = getattr(settings, "LUA_WEBHOOK_API_KEY", None)
    if not expected:
        return False, "Agent key not configured"
    auth = request.headers.get("Authorization") or ""
    if auth != f"Bearer {expected}":
        return False, "Unauthorized"
    return True, None


def _parse_custom_slot_id(s: str) -> uuid.UUID | None:
    if not isinstance(s, str) or not s.startswith(CUSTOM_WIDGET_PREFIX):
        return None
    rest = s[len(CUSTOM_WIDGET_PREFIX) :].strip()
    try:
        return uuid.UUID(rest)
    except ValueError:
        return None


def _clean_order(raw, user: CustomUser | None) -> list[str] | None:
    if raw is None:
        return None
    if not isinstance(raw, list):
        return None
    out: list[str] = []
    seen: set[str] = set()
    for x in raw:
        if not isinstance(x, str) or x in seen:
            continue
        if x in DASHBOARD_WIDGET_IDS:
            seen.add(x)
            out.append(x)
            continue
        cid = _parse_custom_slot_id(x)
        if cid and user is not None:
            if DashboardCustomWidget.objects.filter(id=cid, user=user).exists():
                seen.add(x)
                out.append(x)
    return out


def _can_customize_dashboard(user: CustomUser) -> bool:
    return user.role in ("SUPER_ADMIN", "ADMIN", "MANAGER", "OWNER")


def _resolve_user_from_agent_payload(data: dict) -> CustomUser | None:
    user = None
    uid = data.get("user_id") or data.get("userId")
    if uid:
        try:
            user = CustomUser.objects.filter(id=uid, is_active=True).first()
        except Exception:
            user = None
    if user is None and data.get("email"):
        user = CustomUser.objects.filter(email__iexact=str(data["email"]).strip(), is_active=True).first()
    if user is None and data.get("phone"):
        from staff.views_agent import _resolve_restaurant_and_staff_by_phone

        _rest, staff = _resolve_restaurant_and_staff_by_phone(data.get("phone"))
        user = staff
    if user is None and data.get("phone"):
        # WhatsApp-oriented helper skips SUPER_ADMIN so tenant staff win;
        # dashboard Miya still needs platform admins matched by phone alone.
        from staff.views_agent import _resolve_restaurant_and_staff_by_phone

        _rest, staff = _resolve_restaurant_and_staff_by_phone(
            data.get("phone"), exclude_super_admin=False
        )
        user = staff
    return user


class DashboardWidgetOrderView(APIView):
    """GET/PATCH widget order for the authenticated user."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        cleaned = _clean_order(getattr(user, "dashboard_widget_order", None), user)
        return Response({"order": cleaned})

    def patch(self, request):
        user = request.user
        if not _can_customize_dashboard(user):
            return Response({"detail": "Forbidden"}, status=status.HTTP_403_FORBIDDEN)

        body = request.data or {}
        if "add" in body:
            to_add = body.get("add") or []
            if not isinstance(to_add, list):
                return Response({"detail": "add must be a list"}, status=status.HTTP_400_BAD_REQUEST)
            current = _clean_order(user.dashboard_widget_order, user)
            if current is None:
                current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
            for w in to_add:
                if isinstance(w, str) and w in DASHBOARD_WIDGET_IDS and w not in current:
                    current.append(w)
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])
            return Response({"order": current})

        if "order" in body:
            order = body.get("order")
            cleaned = _clean_order(order, user)
            if cleaned is None:
                return Response({"detail": "order must be a list of valid widget ids"}, status=status.HTTP_400_BAD_REQUEST)
            user.dashboard_widget_order = cleaned
            user.save(update_fields=["dashboard_widget_order"])
            return Response({"order": cleaned})

        return Response({"detail": "Expected add or order"}, status=status.HTTP_400_BAD_REQUEST)


class DashboardCustomWidgetListView(APIView):
    """List Miya-created custom widgets for the current user (for rendering).

    Side-effect on read: any *placeholder* custom widget (no link_url)
    whose title is an alias for a data-bound built-in widget
    (e.g. "Purchases" → ``purchase_orders``) is silently migrated:

      - the placeholder ``DashboardCustomWidget`` is deleted,
      - the data-bound built-in is appended to the user's layout if
        absent,
      - the placeholder slot is removed from ``dashboard_widget_order``.

    This is the *render-time* counterpart to the agent-side redirect in
    ``AgentDashboardWidgetCreateView``: tenants who already created a
    "Purchases" / "Finance" / "HR" placeholder before the fix shipped
    don't need manual cleanup — the next dashboard load fixes itself.
    Real shortcut tiles (link_url set) are never touched.
    """

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        user = request.user
        qs = (
            DashboardCustomWidget.objects.filter(user=user)
            .select_related("category")
            .order_by("-created_at")
        )

        # ----- one-shot migration for aliased placeholder tiles ------------
        # Done synchronously on read because it's O(custom widgets per user)
        # and the conversion is permanent — once dropped, future reads see
        # the clean state.
        slots_to_drop: list[str] = []
        builtins_to_add: list[str] = []
        rows_to_delete: list[DashboardCustomWidget] = []
        for w in qs:
            if w.link_url:
                continue
            target = resolve_widget_alias(w.title, w.subtitle)
            if target and target in DATA_BOUND_BUILTIN_IDS:
                rows_to_delete.append(w)
                slots_to_drop.append(w.slot_id())
                if target not in builtins_to_add:
                    builtins_to_add.append(target)

        if rows_to_delete:
            try:
                for w in rows_to_delete:
                    w.delete()
                current = _clean_order(getattr(user, "dashboard_widget_order", None), user)
                if current is None:
                    current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
                # Replace each dropped slot with its target built-in (if
                # the built-in isn't already in the layout) so the
                # widget appears in roughly the same position the
                # placeholder was.
                next_order: list[str] = []
                inserted: set[str] = set()
                for slot in current:
                    if slot in slots_to_drop:
                        target = None
                        for w in rows_to_delete:
                            if w.slot_id() == slot:
                                target = resolve_widget_alias(w.title, w.subtitle)
                                break
                        if (
                            target
                            and target in DASHBOARD_WIDGET_IDS
                            and target not in inserted
                            and target not in next_order
                        ):
                            next_order.append(target)
                            inserted.add(target)
                        continue
                    next_order.append(slot)
                # Belt-and-braces: append any remaining built-ins that
                # weren't slotted in above (e.g. the placeholder wasn't
                # in the saved order to begin with).
                for builtin in builtins_to_add:
                    if builtin not in next_order and builtin in DASHBOARD_WIDGET_IDS:
                        next_order.append(builtin)
                if next_order != current:
                    user.dashboard_widget_order = next_order
                    user.save(update_fields=["dashboard_widget_order"])
            except Exception:  # pragma: no cover — migration is best-effort.
                logger.warning(
                    "Aliased custom widget migration failed for user=%s",
                    getattr(user, "id", None),
                    exc_info=True,
                )
            # Re-load the queryset post-cleanup so we don't return rows
            # we just deleted.
            qs = (
                DashboardCustomWidget.objects.filter(user=user)
                .select_related("category")
                .order_by("-created_at")
            )

        widgets = []
        for w in qs:
            widgets.append(
                {
                    "id": str(w.id),
                    "slot_id": w.slot_id(),
                    "title": w.title,
                    "subtitle": w.subtitle or "",
                    "link_url": w.link_url or "",
                    "icon": w.icon or "sparkles",
                    "category_id": str(w.category_id) if w.category_id else None,
                    "created_at": w.created_at.isoformat() if w.created_at else None,
                }
            )
        return Response(
            {
                "widgets": widgets,
                "migrated_to_builtins": builtins_to_add,
            }
        )


class AgentDashboardWidgetsAddView(APIView):
    """
    Miya/Lua: add one or more built-in dashboard widgets for a manager user.
    Auth: Bearer LUA_WEBHOOK_API_KEY

    Body:
      - widgets: required list of widget id strings
      - user_id: optional UUID string
      - email: optional manager email
      - phone: optional phone (digits); resolves first active user with matching phone
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        widgets = data.get("widgets") or data.get("widget_ids") or []
        if not isinstance(widgets, list) or not widgets:
            return Response(
                {"success": False, "error": "widgets must be a non-empty list of widget ids"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        widgets = [normalize_agent_widget_id(w) if isinstance(w, str) else w for w in widgets]

        unknown = [w for w in widgets if not isinstance(w, str) or w not in DASHBOARD_WIDGET_IDS]
        if unknown:
            return Response(
                {"success": False, "error": f"Invalid widget ids: {unknown}", "allowed": sorted(DASHBOARD_WIDGET_IDS)},
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = _resolve_user_from_agent_payload(data)

        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        current = _clean_order(user.dashboard_widget_order, user)
        if current is None:
            current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
        added = []
        for w in widgets:
            if w not in current:
                current.append(w)
                added.append(w)
        user.dashboard_widget_order = current
        user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {
                "success": True,
                "user_id": str(user.id),
                "order": current,
                "added": added,
                "message_for_user": (
                    f"Added {len(added)} widget(s) to your dashboard: {', '.join(added)}. "
                    "Open or refresh the dashboard to see them."
                    if added
                    else "Those widgets are already on your dashboard."
                ),
            }
        )


class AgentDashboardWidgetCreateView(APIView):
    """
    Miya/Lua: create a new custom dashboard tile and optionally add it to the user's layout.

    Auth: Bearer LUA_WEBHOOK_API_KEY

    Body:
      - title: required string
      - subtitle: optional
      - link_url or link: optional (app path e.g. /dashboard/processes-tasks-app or https://...)
      - icon: optional; one of ALLOWED_CUSTOM_WIDGET_ICONS (default sparkles)
      - add_to_dashboard: optional bool (default true)
      - user_id | email | phone: target user
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        title = (data.get("title") or "").strip()
        if len(title) > 255:
            title = title[:255].rstrip()
        if not title:
            return Response({"success": False, "error": "title is required"}, status=status.HTTP_400_BAD_REQUEST)

        subtitle = str(data.get("subtitle") or "")[:2000]
        link_raw = str(data.get("link_url") or data.get("link") or "")

        # ------------------------------------------------------------------
        # Alias redirect: if the manager said "Create a Purchases widget"
        # (or HR / Finance / Maintenance / Urgent / etc.), there is already
        # a data-bound built-in widget for that lane — adding a placeholder
        # "Ask Miya" tile would just hide the real operational data behind
        # a shortcut. Detect the alias and silently *add* the built-in
        # widget instead so the manager sees the live list of items.
        #
        # The manager only sees a placeholder ("Demander à Miya") tile if
        # the title genuinely doesn't match any known operational lane —
        # which is the original ``create_custom`` use case (true shortcuts).
        # ------------------------------------------------------------------
        # Skip the redirect when:
        #   - the manager passed an explicit ``link_url``: they want a raw
        #     shortcut tile that points somewhere specific, even if the
        #     title happens to collide with a known lane.
        #   - the manager passed ``force_custom=True``: escape hatch for
        #     the dialog UI when a power user really wants a custom tile.
        force_custom = bool(data.get("force_custom"))
        explicit_link = bool(link_raw and link_raw.strip())
        aliased_id: str | None = None
        if not force_custom and not explicit_link:
            aliased_id = resolve_widget_alias(
                title,
                data.get("subtitle"),
                data.get("category_name") or data.get("categoryName"),
            )
            if aliased_id and aliased_id not in DATA_BOUND_BUILTIN_IDS:
                # Resolver returned a built-in id we don't consider data-bound
                # (shouldn't happen with the current alias table, but guard
                # against future drift).
                aliased_id = None

        if aliased_id:
            return self._redirect_to_builtin_add(
                data=data,
                builtin_id=aliased_id,
                requested_title=title,
            )

        link_url = ensure_link(title, link_raw)

        icon_raw = (data.get("icon") or "sparkles").strip().lower()[:64]
        if icon_raw not in ALLOWED_CUSTOM_WIDGET_ICONS:
            icon_raw = "sparkles"

        add_to_dashboard = data.get("add_to_dashboard", True)
        if isinstance(add_to_dashboard, str):
            add_to_dashboard = add_to_dashboard.lower() in ("1", "true", "yes")

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        if not getattr(user, "restaurant_id", None):
            return Response({"success": False, "error": "User has no restaurant"}, status=status.HTTP_400_BAD_REQUEST)

        category = None
        raw_cat = data.get("category_id") or data.get("categoryId")
        if raw_cat:
            try:
                raw_cat_uuid = uuid.UUID(str(raw_cat))
            except (TypeError, ValueError):
                raw_cat_uuid = None
            if raw_cat_uuid is not None:
                category = DashboardCategory.objects.filter(
                    id=raw_cat_uuid, restaurant_id=user.restaurant_id
                ).first()

        # Miya convenience: if no category_id was given but a category_name
        # was, find-or-create the category by name in this tenant.
        if category is None:
            cat_name = (data.get("category_name") or data.get("categoryName") or "").strip()[:100]
            if cat_name:
                category = DashboardCategory.objects.filter(
                    restaurant_id=user.restaurant_id, name__iexact=cat_name
                ).first()
                if category is None:
                    try:
                        category = DashboardCategory.objects.create(
                            restaurant_id=user.restaurant_id,
                            name=cat_name,
                            order_index=0,
                            created_by=user,
                        )
                    except IntegrityError:
                        category = DashboardCategory.objects.filter(
                            restaurant_id=user.restaurant_id, name__iexact=cat_name
                        ).first()

        w = DashboardCustomWidget.objects.create(
            user=user,
            restaurant_id=user.restaurant_id,
            category=category,
            title=title,
            subtitle=subtitle,
            link_url=link_url,
            icon=icon_raw,
        )
        slot = w.slot_id()

        if add_to_dashboard:
            current = _clean_order(user.dashboard_widget_order, user)
            if current is None:
                current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)
            if slot not in current:
                current.append(slot)
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {
                "success": True,
                "widget_id": slot,
                "user_id": str(user.id),
                "order": _clean_order(user.dashboard_widget_order, user) if add_to_dashboard else None,
                "widget": {
                    "id": str(w.id),
                    "title": w.title,
                    "subtitle": w.subtitle,
                    "link_url": w.link_url,
                    "icon": w.icon,
                    "category_id": str(w.category_id) if w.category_id else None,
                },
                "message_for_user": (
                    f'Added a new dashboard card "{w.title}". Open or refresh the dashboard to see it.'
                    if add_to_dashboard
                    else f'Created dashboard card "{w.title}" (slot {slot}).'
                ),
            },
            status=status.HTTP_201_CREATED,
        )

    # --------------------------------------------------------------------
    # Helper: alias redirect. Reuses the same auth + user resolution as
    # the rest of this view so the response shape stays identical.
    # --------------------------------------------------------------------
    def _redirect_to_builtin_add(
        self,
        *,
        data: dict,
        builtin_id: str,
        requested_title: str,
    ) -> Response:
        """Add the data-bound built-in widget instead of creating a
        placeholder custom tile. Mirrors the response shape of the
        normal create_custom path so the agent contract is unchanged.
        """
        if builtin_id not in DASHBOARD_WIDGET_IDS:
            # Defensive: alias table referenced a widget id that the
            # allow-list doesn't know about. Fall through to the normal
            # custom-tile path by raising — caller will treat it as a
            # 400 from the resolver. In practice this only happens when
            # widget_ids.py and widget_alias_resolver.py drift.
            return Response(
                {
                    "success": False,
                    "error": (
                        f"Resolved alias '{requested_title}' to '{builtin_id}', "
                        "but that built-in id is not currently allow-listed."
                    ),
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        current = _clean_order(user.dashboard_widget_order, user)
        if current is None:
            current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)

        already_present = builtin_id in current
        if not already_present:
            current.append(builtin_id)
            user.dashboard_widget_order = current
            user.save(update_fields=["dashboard_widget_order"])

        # Best-effort cleanup: if the user already has a placeholder
        # custom widget whose title resolves to this same built-in id,
        # remove it so the live data-bound widget isn't duplicated by
        # an "Ask Miya" tile next to it.
        try:
            self._cleanup_aliased_custom_widgets(user, builtin_id)
        except Exception:  # pragma: no cover — best-effort cleanup
            logger.warning(
                "Failed to clean up aliased custom widgets for user=%s builtin=%s",
                getattr(user, "id", None),
                builtin_id,
                exc_info=True,
            )

        inbox_style_lanes = {
            "urgent_top",
            "human_resources",
            "finance",
            "maintenance",
            "purchase_orders",
            "miscellaneous",
            "staff_inbox",
        }
        if builtin_id in inbox_style_lanes:
            message = (
                f'Added the "{requested_title}" lane to your dashboard — it shows the live list of '
                f"matching items from the inbox. Open or refresh the dashboard to see them."
                if not already_present
                else f'The "{requested_title}" lane is already on your dashboard — it shows the live list of matching items.'
            )
        elif builtin_id in ("clock_ins", "live_attendance"):
            message = (
                f'Added the "{requested_title}" card to your dashboard — it shows live attendance data. '
                f"Open or refresh the dashboard to see it."
                if not already_present
                else f'The "{requested_title}" card is already on your dashboard.'
            )
        else:
            message = (
                f'Added the "{requested_title}" card to your dashboard — it shows live operational data. '
                f"Open or refresh the dashboard to see it."
                if not already_present
                else f'The "{requested_title}" card is already on your dashboard.'
            )

        return Response(
            {
                "success": True,
                "user_id": str(user.id),
                "order": _clean_order(user.dashboard_widget_order, user),
                "added": [builtin_id] if not already_present else [],
                "widget_id": builtin_id,
                "resolved_from_alias": True,
                "alias_input": requested_title,
                "message_for_user": message,
            },
            status=status.HTTP_201_CREATED if not already_present else status.HTTP_200_OK,
        )

    @staticmethod
    def _cleanup_aliased_custom_widgets(user: CustomUser, builtin_id: str) -> None:
        """Remove any placeholder custom widget whose title is a known
        alias for ``builtin_id`` from this user's tenant + layout.

        Only runs against placeholder tiles (no link_url) so we never
        delete a real shortcut a user crafted to point at a specific page.
        """
        if not getattr(user, "restaurant_id", None):
            return
        candidates = list(
            DashboardCustomWidget.objects.filter(
                user=user,
                restaurant_id=user.restaurant_id,
                link_url="",
            )
        )
        if not candidates:
            return
        slots_to_drop: list[str] = []
        for w in candidates:
            if resolve_widget_alias(w.title, w.subtitle) == builtin_id:
                slots_to_drop.append(w.slot_id())
                w.delete()
        if not slots_to_drop:
            return
        current = _clean_order(user.dashboard_widget_order, user) or []
        new_order = [s for s in current if s not in slots_to_drop]
        if new_order != current:
            user.dashboard_widget_order = new_order
            user.save(update_fields=["dashboard_widget_order"])


class AgentDashboardWidgetListView(APIView):
    """
    Miya/Lua: list a user's current dashboard widget layout + the catalogue of
    built-in widgets the agent may `add`. Includes any Miya-created custom
    tiles owned by the user.

    Auth: Bearer LUA_WEBHOOK_API_KEY.
    Body: user_id | email | phone (at least one).
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        current = _clean_order(getattr(user, "dashboard_widget_order", None), user)
        if current is None:
            current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)

        custom_qs = (
            DashboardCustomWidget.objects.filter(user=user)
            .select_related("category")
            .order_by("-created_at")
        )
        custom_widgets = []
        for w in custom_qs:
            custom_widgets.append(
                {
                    "id": str(w.id),
                    "slot_id": w.slot_id(),
                    "title": w.title,
                    "subtitle": w.subtitle or "",
                    "link_url": w.link_url or "",
                    "icon": w.icon or "sparkles",
                    "category_id": str(w.category_id) if w.category_id else None,
                    "in_layout": w.slot_id() in current,
                }
            )

        # Build a description-friendly summary of the ordered layout so the
        # LLM can echo it verbatim without extra round-trips.
        custom_by_slot = {w["slot_id"]: w for w in custom_widgets}
        ordered_summary = []
        for slot in current:
            if slot.startswith(CUSTOM_WIDGET_PREFIX):
                info = custom_by_slot.get(slot)
                ordered_summary.append(
                    {"id": slot, "kind": "custom", "title": (info or {}).get("title") or slot}
                )
            else:
                ordered_summary.append({"id": slot, "kind": "builtin", "title": slot})

        return Response(
            {
                "success": True,
                "user_id": str(user.id),
                "order": current,
                "order_detail": ordered_summary,
                "custom_widgets": custom_widgets,
                "allowed_builtin_ids": sorted(DASHBOARD_WIDGET_IDS),
                "default_builtin_order": list(DEFAULT_DASHBOARD_WIDGET_ORDER),
                "allowed_custom_icons": sorted(ALLOWED_CUSTOM_WIDGET_ICONS),
            }
        )


class AgentTenantBootstrapView(APIView):
    """
    Miya/Lua: resolve ``restaurant_id`` (and confirm ``user_id``) when the
    conversation metadata is missing ``restaurantId`` but we still have
    ``user_id``, ``email``, or ``phone`` from the WhatsApp / dashboard bridge.

    Auth: Bearer LUA_WEBHOOK_API_KEY
    Body: user_id | email | phone (at least one, same semantics as other
    dashboard agent endpoints).
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {
                    "success": False,
                    "error": "Could not resolve user; pass user_id, email, or phone",
                    "message_for_user": "We couldn't link this chat to your Mizan account yet.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not getattr(user, "restaurant_id", None):
            return Response(
                {
                    "success": False,
                    "error": "User has no restaurant",
                    "message_for_user": "Your account isn't linked to a workspace yet.",
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        return Response(
            {
                "success": True,
                "restaurant_id": str(user.restaurant_id),
                "user_id": str(user.id),
                "email": (user.email or "")[:320],
            }
        )


class AgentDashboardWidgetsRemoveView(APIView):
    """
    Miya/Lua: remove one or more widgets from the user's dashboard layout.

    Auth: Bearer LUA_WEBHOOK_API_KEY.
    Body:
      - widgets: required list of widget ids (built-in IDs or `custom:<uuid>`
                 slots). Unknown ids are silently ignored so partial success
                 is safe.
      - user_id | email | phone
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        widgets = data.get("widgets") or data.get("widget_ids") or []
        if not isinstance(widgets, list) or not widgets:
            return Response(
                {"success": False, "error": "widgets must be a non-empty list of widget ids"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        widgets = [normalize_agent_widget_id(w) if isinstance(w, str) else w for w in widgets]

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        current = _clean_order(user.dashboard_widget_order, user)
        if current is None:
            current = list(DEFAULT_DASHBOARD_WIDGET_ORDER)

        to_remove = {w for w in widgets if isinstance(w, str)}
        removed = [w for w in current if w in to_remove]
        current = [w for w in current if w not in to_remove]

        user.dashboard_widget_order = current
        user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {
                "success": True,
                "user_id": str(user.id),
                "order": current,
                "removed": removed,
                "message_for_user": (
                    f"Removed {len(removed)} widget(s) from your dashboard: {', '.join(removed)}. "
                    "Open or refresh the dashboard to see the new layout."
                    if removed
                    else "None of those widgets were on your dashboard."
                ),
            }
        )


class AgentDashboardWidgetsReorderView(APIView):
    """
    Miya/Lua: replace the user's full dashboard widget order.

    Auth: Bearer LUA_WEBHOOK_API_KEY.
    Body:
      - order: required list of widget ids. Invalid / unknown ids are
               dropped but the call still succeeds so Miya can reorder the
               valid subset.
      - user_id | email | phone
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        order = data.get("order")
        if not isinstance(order, list) or not order:
            return Response(
                {"success": False, "error": "order must be a non-empty list of widget ids"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        order = [normalize_agent_widget_id(w) if isinstance(w, str) else w for w in order]

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )

        cleaned = _clean_order(order, user) or []
        dropped = [w for w in order if isinstance(w, str) and w not in cleaned]

        user.dashboard_widget_order = cleaned
        user.save(update_fields=["dashboard_widget_order"])

        return Response(
            {
                "success": True,
                "user_id": str(user.id),
                "order": cleaned,
                "dropped": dropped,
                "message_for_user": (
                    "Dashboard reordered. "
                    + (
                        f"{len(dropped)} unknown id(s) were skipped: {', '.join(dropped)}."
                        if dropped
                        else ""
                    )
                ).strip(),
            }
        )


class AgentDashboardCustomWidgetDeleteView(APIView):
    """
    Miya/Lua: permanently delete a Miya-created custom widget tile and remove
    it from the user's saved layout.

    Auth: Bearer LUA_WEBHOOK_API_KEY.
    Body:
      - widget_id: required UUID of the DashboardCustomWidget, OR `custom:<uuid>` slot.
      - user_id | email | phone: target user (must own the widget or be a
                                  manager in the same tenant).
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        raw_id = str(data.get("widget_id") or data.get("id") or "").strip()
        if not raw_id:
            return Response(
                {"success": False, "error": "widget_id is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Accept either the raw UUID or a `custom:<uuid>` slot id.
        parsed_from_slot = _parse_custom_slot_id(raw_id)
        if parsed_from_slot is not None:
            widget_uuid = parsed_from_slot
        else:
            try:
                widget_uuid = uuid.UUID(raw_id)
            except ValueError:
                return Response(
                    {"success": False, "error": "widget_id must be a UUID or a 'custom:<uuid>' slot"},
                    status=status.HTTP_400_BAD_REQUEST,
                )

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not getattr(user, "restaurant_id", None):
            return Response(
                {"success": False, "error": "User has no restaurant"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        # Owner OR manager in the same tenant may delete the tile (mirrors the
        # authenticated endpoint in views_categories.DashboardCustomWidgetDetailView).
        w = DashboardCustomWidget.objects.filter(
            id=widget_uuid, restaurant_id=user.restaurant_id
        ).first()
        if w is None:
            return Response(
                {"success": False, "error": "Custom widget not found in this tenant"},
                status=status.HTTP_404_NOT_FOUND,
            )
        if w.user_id != user.id and not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "Only the owner or a manager may delete this widget"},
                status=status.HTTP_403_FORBIDDEN,
            )

        slot = w.slot_id()
        title = w.title
        owner = w.user
        w.delete()

        # Best-effort: drop the slot from the owner's layout too.
        try:
            current = _clean_order(getattr(owner, "dashboard_widget_order", None), owner)
            if current is not None and slot in current:
                owner.dashboard_widget_order = [s for s in current if s != slot]
                owner.save(update_fields=["dashboard_widget_order"])
        except Exception:  # pragma: no cover — layout cleanup is best-effort.
            logger.warning("Failed to clean widget %s from owner layout", slot, exc_info=True)

        return Response(
            {
                "success": True,
                "widget_id": slot,
                "removed_slot": slot,
                "message_for_user": (
                    f'Deleted dashboard card "{title}". Open or refresh the dashboard to see the new layout.'
                ),
            }
        )


class AgentDashboardCategoryCreateView(APIView):
    """
    Miya/Lua: create a dashboard category (tenant-wide) for grouping custom
    shortcuts. Idempotent — if a category with the same name already exists in
    the tenant we return it instead of creating a duplicate.

    Auth: Bearer LUA_WEBHOOK_API_KEY

    Body:
      - name: required string (max 100 chars)
      - order_index: optional int (default 0)
      - user_id | email | phone: target user (used to resolve tenant)
    """

    permission_classes = [permissions.AllowAny]
    authentication_classes = []

    def post(self, request):
        ok, err = _validate_agent_key(request)
        if not ok:
            return Response({"success": False, "error": err}, status=status.HTTP_401_UNAUTHORIZED)

        data = request.data or {}
        name = (data.get("name") or "").strip()[:100]
        if not name:
            return Response(
                {"success": False, "error": "name is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            order_index = int(data.get("order_index") or 0)
        except (TypeError, ValueError):
            order_index = 0

        user = _resolve_user_from_agent_payload(data)
        if user is None:
            return Response(
                {"success": False, "error": "Could not resolve user; pass user_id, email, or phone"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not _can_customize_dashboard(user):
            return Response(
                {"success": False, "error": "User cannot customize dashboard (need manager role)"},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not getattr(user, "restaurant_id", None):
            return Response(
                {"success": False, "error": "User has no restaurant"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        existing = DashboardCategory.objects.filter(
            restaurant_id=user.restaurant_id, name__iexact=name
        ).first()
        created = False
        if existing is not None:
            category = existing
        else:
            try:
                category = DashboardCategory.objects.create(
                    restaurant_id=user.restaurant_id,
                    name=name,
                    order_index=order_index,
                    created_by=user,
                )
                created = True
            except IntegrityError:
                category = DashboardCategory.objects.filter(
                    restaurant_id=user.restaurant_id, name__iexact=name
                ).first()

        return Response(
            {
                "success": True,
                "created": created,
                "category": {
                    "id": str(category.id),
                    "name": category.name,
                    "order_index": category.order_index,
                },
                "message_for_user": (
                    f'Created a new dashboard category "{category.name}".'
                    if created
                    else f'Dashboard category "{category.name}" already exists — using it.'
                ),
            },
            status=status.HTTP_201_CREATED if created else status.HTTP_200_OK,
        )
