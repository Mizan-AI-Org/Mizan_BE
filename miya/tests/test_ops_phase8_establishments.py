"""Phase 8: multi-establishment context, selection, and no cross-branch leakage."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.ops import CANONICAL_TOOL_NAMES, dispatch_canonical_tool
from miya.services.ops.context import (
    OpsContext,
    assert_location_access,
    guard_entity_location,
    require_establishment_context,
)
from miya.services.ops.scoping import (
    apply_location_scope,
    filter_visible_location_ids,
    notification_data_with_location,
    user_can_access_location,
)


def _loc(id_: str, name: str):
    loc = MagicMock()
    loc.id = id_
    loc.name = name
    loc.is_primary = False
    loc.address = ""
    return loc


def _ctx(
    *,
    location_id=None,
    location_name=None,
    available=None,
    role="OWNER",
    channel="dashboard",
):
    rest = MagicMock()
    rest.id = "rest-1"
    rest.name = "Mizan Group"
    user = MagicMock()
    user.id = "u-mgr"
    user.pk = "u-mgr"
    user.role = role
    user.is_active = True
    user.phone = "+212600000001"
    user.email = "mgr@ex.com"
    user.first_name = "Maya"
    user.last_name = "Manager"
    locs = available or [
        {"id": "loc-marrakech", "name": "Marrakech Restaurant", "kind": "establishment"},
        {"id": "loc-casa", "name": "Casablanca", "kind": "establishment"},
    ]
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="rest-1",
        user_id="u-mgr",
        role=role,
        channel=channel,
        location_id=location_id,
        location_name=location_name,
        available_locations=locs,
    )


class EstablishmentContextTests(SimpleTestCase):
    def test_require_clarifies_when_multi_and_unset(self):
        ctx = _ctx(location_id=None)
        err = require_establishment_context(ctx, for_action="incidents")
        self.assertIsNotNone(err)
        self.assertFalse(err.success)
        self.assertEqual(err.code, "needs_establishment")
        self.assertIn("Which establishment", err.message_for_user)

    def test_require_ok_when_active(self):
        ctx = _ctx(location_id="loc-marrakech", location_name="Marrakech Restaurant")
        self.assertIsNone(require_establishment_context(ctx, for_action="incidents"))

    def test_require_ok_when_single_branch(self):
        ctx = _ctx(
            location_id=None,
            available=[{"id": "only-1", "name": "Solo", "kind": "establishment"}],
        )
        self.assertIsNone(require_establishment_context(ctx, for_action="tasks"))

    def test_assert_location_access_denies_invisible(self):
        ctx = _ctx(location_id="loc-marrakech")
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=False,
        ):
            denied = assert_location_access(ctx, "loc-forbidden")
        self.assertIsNotNone(denied)
        self.assertEqual(denied.code, "location_forbidden")

    def test_guard_blocks_other_establishment(self):
        ctx = _ctx(location_id="loc-marrakech", location_name="Marrakech Restaurant")
        entity = MagicMock()
        entity.location_id = "loc-casa"
        entity.business_location_id = None
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=True,
        ):
            err = guard_entity_location(ctx, entity)
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "location_mismatch")


class ScopingHelpersTests(SimpleTestCase):
    def test_apply_location_scope(self):
        qs = MagicMock()
        qs.filter.return_value = "scoped"
        out = apply_location_scope(qs, location_id="loc-a", field="location_id")
        self.assertEqual(out, "scoped")
        qs.filter.assert_called_once_with(location_id="loc-a")

    def test_filter_visible_location_ids(self):
        qs = MagicMock()
        qs.filter.return_value = "vis"
        out = filter_visible_location_ids(qs, location_ids=["a", "b"], field="business_location_id")
        self.assertEqual(out, "vis")
        qs.filter.assert_called_once_with(business_location_id__in=["a", "b"])

    def test_notification_data_stamps_location(self):
        data = notification_data_with_location(
            {"task_id": "t1"},
            location_id="loc-casa",
            location_name="Casablanca",
        )
        self.assertEqual(data["location_id"], "loc-casa")
        self.assertEqual(data["location_name"], "Casablanca")
        self.assertEqual(data["task_id"], "t1")


class SetEstablishmentContextTests(SimpleTestCase):
    def test_tool_registered(self):
        self.assertIn("set_establishment_context", CANONICAL_TOOL_NAMES)
        self.assertIn("switch_establishment", CANONICAL_TOOL_NAMES)

    @patch("miya.services.ops.establishments.visible_locations_for_user")
    @patch("miya.services.ops.establishments.resolve_location_by_name")
    def test_switch_by_name(self, resolve, visible):
        casa = _loc("loc-casa", "Casablanca")
        mar = _loc("loc-marrakech", "Marrakech Restaurant")
        visible.return_value = [mar, casa]
        resolve.return_value = (casa, [casa])
        ctx = _ctx(location_id="loc-marrakech", location_name="Marrakech Restaurant")

        result = dispatch_canonical_tool(
            "set_establishment_context",
            {"q": "Casablanca"},
            ctx=ctx,
        )
        self.assertTrue(result.success)
        self.assertEqual(ctx.location_id, "loc-casa")
        self.assertEqual(ctx.location_name, "Casablanca")
        self.assertEqual(result.data["session_patch"]["location_id"], "loc-casa")

    @patch("miya.services.ops.establishments.visible_locations_for_user")
    def test_forbidden_location_id(self, visible):
        mar = _loc("loc-marrakech", "Marrakech Restaurant")
        visible.return_value = [mar]
        ctx = _ctx(
            location_id="loc-marrakech",
            available=[{"id": "loc-marrakech", "name": "Marrakech Restaurant"}],
        )
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=False,
        ):
            result = dispatch_canonical_tool(
                "set_establishment_context",
                {"location_id": "loc-secret"},
                ctx=ctx,
            )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "location_forbidden")


class LeakagePreventionTests(SimpleTestCase):
    @patch("miya.services.ops.incidents.require_permission", return_value=None)
    @patch("miya.services.ops.incidents.require_restaurant", return_value=None)
    def test_find_incidents_clarifies_without_context(self, *_):
        from miya.services.ops.incidents import find_incidents

        ctx = _ctx(location_id=None)
        result = find_incidents(ctx, since="today")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    @patch("miya.services.ops.tasks.require_permission", return_value=None)
    @patch("miya.services.ops.tasks.require_restaurant", return_value=None)
    def test_find_tasks_clarifies_without_context(self, *_):
        from miya.services.ops.tasks import find_tasks

        ctx = _ctx(location_id=None)
        result = find_tasks(ctx)
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    @patch("miya.services.ops.invoices.require_permission", return_value=None)
    @patch("miya.services.ops.invoices.require_restaurant", return_value=None)
    def test_find_invoices_clarifies_without_context(self, *_):
        from miya.services.ops.invoices import find_invoices

        ctx = _ctx(location_id=None)
        result = find_invoices(ctx)
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    @patch("miya.services.ops.documents.require_restaurant", return_value=None)
    def test_find_documents_clarifies_without_context(self, *_):
        from miya.services.ops.documents import find_documents

        ctx = _ctx(location_id=None)
        result = find_documents(ctx, q="insurance")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    @patch("miya.services.ops.scoping.apply_location_scope")
    @patch("staff.models_task.SafetyConcernReport")
    @patch("miya.services.ops.incidents.require_permission", return_value=None)
    @patch("miya.services.ops.incidents.require_restaurant", return_value=None)
    def test_find_incidents_scopes_to_active_location(self, _rr, _rp, model, scope):
        from miya.services.ops.incidents import find_incidents

        qs = MagicMock()
        qs.filter.return_value = qs
        qs.select_related.return_value = qs
        qs.order_by.return_value = []
        model.objects.filter.return_value = qs
        scope.side_effect = lambda q, **kw: q

        ctx = _ctx(location_id="loc-marrakech", location_name="Marrakech Restaurant")
        result = find_incidents(ctx, since="today")
        scope.assert_called()
        kwargs = scope.call_args.kwargs
        self.assertEqual(kwargs.get("location_id"), "loc-marrakech")
        self.assertEqual(kwargs.get("field"), "business_location_id")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "incidents_not_found")
        self.assertIn("Marrakech", result.message_for_user)

    @patch("miya.services.ops.scoping.apply_location_scope")
    @patch("finance.models.Invoice")
    @patch("miya.services.ops.invoices.require_permission", return_value=None)
    @patch("miya.services.ops.invoices.require_restaurant", return_value=None)
    def test_find_invoices_scopes_to_active_location(self, _rr, _rp, model, scope):
        from miya.services.ops.invoices import find_invoices

        qs = MagicMock()
        qs.filter.return_value = qs
        qs.select_related.return_value = qs
        qs.order_by.return_value = []
        model.objects.filter.return_value = qs
        scope.side_effect = lambda q, **kw: q

        ctx = _ctx(location_id="loc-casa", location_name="Casablanca")
        result = find_invoices(ctx)
        scope.assert_called()
        self.assertEqual(scope.call_args.kwargs.get("location_id"), "loc-casa")
        self.assertFalse(result.success)

    def test_ops_context_rejects_inaccessible_sticky_location(self):
        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "rest-1"
        mar = _loc("loc-marrakech", "Marrakech Restaurant")
        with patch(
            "miya.services.ops.scoping.visible_locations_for_user",
            return_value=[mar],
        ), patch(
            "miya.services.ops.scoping.user_can_access_location",
            side_effect=lambda u, r, lid: str(lid) == "loc-marrakech",
        ):
            ctx = OpsContext.from_session(
                user=user,
                restaurant=rest,
                session_context={
                    "location_id": "loc-secret",
                    "channel": "whatsapp",
                },
            )
        self.assertNotEqual(ctx.location_id, "loc-secret")
        # Auto-bind single visible
        self.assertEqual(ctx.location_id, "loc-marrakech")

    def test_user_can_access_location_empty_id(self):
        self.assertTrue(user_can_access_location(MagicMock(), MagicMock(), None))


class PersonaAndPromptTests(SimpleTestCase):
    def test_persona_mentions_establishment_switch(self):
        from miya.persona import MIYA_SUPER_AGENT_PERSONA

        self.assertIn("set_establishment_context", MIYA_SUPER_AGENT_PERSONA)
        self.assertIn("Which establishment", MIYA_SUPER_AGENT_PERSONA)
        self.assertIn("cross-branch leakage", MIYA_SUPER_AGENT_PERSONA)

    def test_establishment_block_in_prompt_builder(self):
        from miya.services.context import _establishment_context_block

        block = _establishment_context_block(
            {
                "location_id": "loc-marrakech",
                "location_name": "Marrakech Restaurant",
                "available_locations": [
                    {"id": "loc-marrakech", "name": "Marrakech Restaurant"},
                    {"id": "loc-casa", "name": "Casablanca"},
                ],
            }
        )
        self.assertIn("Marrakech Restaurant", block)
        self.assertIn("set_establishment_context", block)
