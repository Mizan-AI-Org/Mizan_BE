"""Phase 8 security — unauthorized cross-establishment queries/mutations MUST fail."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.establishments import (
    build_establishment_scope,
    deny_cross_establishment_entity,
    deny_inaccessible_establishment,
    ensure_establishment_for_ops,
    looks_like_establishment_switch,
    try_establishment_switch,
)
from miya.services.ops.context import (
    OpsContext,
    assert_location_access,
    guard_entity_location,
    require_establishment_context,
)
from miya.services.ops.result import fail, ok


def _ctx(
    *,
    location_id=None,
    location_name=None,
    available=None,
    role="MANAGER",
):
    rest = MagicMock()
    rest.id = "org-1"
    rest.name = "Mizan Group"
    user = MagicMock()
    user.id = "u1"
    user.pk = "u1"
    user.role = role
    locs = available or [
        {"id": "loc-casa", "name": "Casablanca"},
        {"id": "loc-rabat", "name": "Rabat"},
    ]
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="org-1",
        user_id="u1",
        role=role,
        channel="dashboard",
        location_id=location_id,
        location_name=location_name,
        available_locations=locs,
    )


class HierarchyScopeTests(SimpleTestCase):
    def test_single_establishment_no_clarify(self):
        ctx = _ctx(
            location_id=None,
            available=[{"id": "only", "name": "Solo Branch"}],
        )
        scope = build_establishment_scope(ctx)
        self.assertFalse(scope.needs_establishment_choice)
        self.assertIsNone(ensure_establishment_for_ops(ctx, for_action="incidents", message="What are today's incidents?"))

    def test_multi_without_context_asks_which(self):
        ctx = _ctx(location_id=None)
        gate = ensure_establishment_for_ops(
            ctx, for_action="today's incidents", message="What are today's incidents?"
        )
        self.assertIsNotNone(gate)
        self.assertTrue(gate.needs_clarification)
        self.assertIn("Which establishment do you mean", gate.message_for_user)
        self.assertIn("Casablanca", gate.message_for_user)

    def test_multi_with_active_answers_directly(self):
        ctx = _ctx(location_id="loc-casa", location_name="Casablanca")
        self.assertIsNone(
            ensure_establishment_for_ops(
                ctx, for_action="incidents", message="What are today's incidents?"
            )
        )


class SwitchContextTests(SimpleTestCase):
    def test_what_about_casablanca_detected(self):
        self.assertEqual(looks_like_establishment_switch("What about Casablanca?"), "Casablanca")
        self.assertEqual(looks_like_establishment_switch("switch to Rabat"), "Rabat")
        self.assertIsNone(
            looks_like_establishment_switch("What about the freezer incident?")
        )

    def test_switch_calls_set_establishment(self):
        ctx = _ctx(location_id="loc-rabat", location_name="Rabat")
        with patch(
            "miya.services.ops.establishments.set_establishment_context",
            return_value=ok(
                message="Switched context to Casablanca.",
                verified=True,
                data={
                    "session_patch": {
                        "location_id": "loc-casa",
                        "location_name": "Casablanca",
                    }
                },
            ),
        ) as setter:
            result = try_establishment_switch(ctx, "What about Casablanca?")
        self.assertIsNotNone(result)
        self.assertTrue(result.success)
        setter.assert_called_once()
        self.assertEqual(setter.call_args.kwargs.get("q"), "Casablanca")


class CrossEstablishmentQueryDenialTests(SimpleTestCase):
    """Unauthorized READ attempts across establishments — all must fail."""

    def test_assert_access_denies_invisible_branch(self):
        ctx = _ctx(location_id="loc-casa")
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=False,
        ):
            denied = deny_inaccessible_establishment(ctx, "loc-enemy")
        self.assertFalse(denied.success)
        self.assertEqual(denied.code, "location_forbidden")

    def test_guard_blocks_entity_from_other_active_branch(self):
        ctx = _ctx(location_id="loc-casa", location_name="Casablanca")
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=True,
        ):
            denied = deny_cross_establishment_entity(
                ctx, entity_location_id="loc-rabat", entity_type="incident"
            )
        self.assertIsNotNone(denied)
        self.assertEqual(denied.code, "location_mismatch")
        self.assertNotIn("secret", (denied.message_for_user or "").lower())

    def test_find_incidents_requires_establishment_when_multi(self):
        from miya.services.ops.incidents import find_incidents

        ctx = _ctx(location_id=None)
        result = find_incidents(ctx, q="", status="OPEN")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_find_tasks_requires_establishment_when_multi(self):
        from miya.services.ops.tasks import find_tasks

        ctx = _ctx(location_id=None)
        result = find_tasks(ctx, q="", status="OPEN")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_find_invoices_requires_establishment_when_multi(self):
        from miya.services.ops.invoices import find_invoices

        ctx = _ctx(location_id=None)
        result = find_invoices(ctx, q="")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_find_documents_requires_establishment_when_multi(self):
        from miya.services.ops.documents import find_documents

        ctx = _ctx(location_id=None)
        result = find_documents(ctx, q="insurance")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_find_staff_requires_establishment_when_multi(self):
        from miya.services.ops.staff import find_staff

        ctx = _ctx(location_id=None)
        result = find_staff(ctx, q="Ahmed")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_apply_location_scope_never_returns_other_branch_filter(self):
        from miya.services.ops.scoping import apply_location_scope

        qs = MagicMock()
        qs.filter.return_value = "scoped-casa"
        out = apply_location_scope(qs, location_id="loc-casa", field="location_id")
        self.assertEqual(out, "scoped-casa")
        qs.filter.assert_called_once_with(location_id="loc-casa")


class CrossEstablishmentMutationDenialTests(SimpleTestCase):
    """Unauthorized WRITE attempts across establishments — all must fail."""

    def test_create_incident_requires_establishment_when_multi(self):
        from miya.services.ops.incidents import create_incident

        ctx = _ctx(location_id=None)
        result = create_incident(ctx, description="Broken freezer")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_create_task_requires_establishment_when_multi(self):
        from miya.services.ops.tasks import create_task

        ctx = _ctx(location_id=None)
        result = create_task(ctx, title="Decorate")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "needs_establishment")

    def test_record_invoice_requires_establishment_when_multi(self):
        from miya.services.ops.invoices import record_invoice

        ctx = _ctx(location_id=None)
        with patch(
            "miya.services.ops.invoices.require_permission",
            return_value=None,
        ):
            result = record_invoice(ctx, vendor="Acme", amount="100")
        self.assertFalse(result.success)
        self.assertTrue(
            result.needs_clarification
            or result.code
            in (
                "needs_establishment",
                "location_required",
                "establishment_required",
            )
        )

    def test_cannot_mutate_entity_tagged_to_other_branch(self):
        ctx = _ctx(location_id="loc-casa", location_name="Casablanca")
        entity = MagicMock()
        entity.location_id = "loc-rabat"
        entity.business_location_id = None
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=True,
        ):
            err = guard_entity_location(ctx, entity)
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "location_mismatch")
        self.assertFalse(err.success)

    def test_set_establishment_to_forbidden_fails(self):
        from miya.services.ops.establishments import set_establishment_context

        ctx = _ctx(location_id="loc-casa")
        denied = fail(
            code="location_forbidden",
            message="You don't have access to that establishment.",
        )
        with patch(
            "miya.services.ops.establishments.visible_locations_for_user",
            return_value=[],
        ), patch(
            "miya.services.ops.establishments.assert_location_access",
            return_value=denied,
        ):
            result = set_establishment_context(ctx, location_id="loc-enemy")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "location_forbidden")

    def test_update_task_status_guards_foreign_location(self):
        """Simulated: resolved task on other branch → location_mismatch."""
        ctx = _ctx(location_id="loc-casa")
        task = MagicMock()
        task.location_id = "loc-rabat"
        task.id = "t-foreign"
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=True,
        ):
            err = guard_entity_location(ctx, task)
        self.assertIsNotNone(err)
        self.assertEqual(err.code, "location_mismatch")

    @patch("miya.services.ops.tasks.require_task_status_permission", return_value=None)
    @patch("miya.services.ops.tasks.require_restaurant", return_value=None)
    @patch("miya.services.ops.tasks._resolve_task")
    def test_update_task_status_denies_cross_establishment_write(
        self, mock_resolve, *_patches
    ):
        from miya.services.ops.tasks import update_task_status

        task = MagicMock()
        task.id = "t-foreign"
        task.location_id = "loc-rabat"
        task.status = "PENDING"
        task.title = "Foreign task"
        task.assigned_to = None
        task.due_date = None
        task.updated_at = None
        task.priority = "MEDIUM"
        task.category = ""
        mock_resolve.return_value = (task, None)
        ctx = _ctx(location_id="loc-casa")
        with patch(
            "miya.services.ops.scoping.user_can_access_location",
            return_value=True,
        ):
            result = update_task_status(ctx, status="COMPLETED", task_id="t-foreign")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "location_mismatch")


class LeakMatrixTests(SimpleTestCase):
    """Explicit matrix: tasks/staff/incidents/invoices/documents must not cross."""

    DOMAINS = ("tasks", "staff", "incidents", "invoices", "documents")

    def test_require_establishment_blocks_all_domains_when_multi_unset(self):
        ctx = _ctx(location_id=None)
        for domain in self.DOMAINS:
            err = require_establishment_context(ctx, for_action=domain)
            self.assertIsNotNone(err, domain)
            self.assertFalse(err.success, domain)
            self.assertEqual(err.code, "needs_establishment", domain)

    def test_active_context_does_not_include_other_branch_ids_in_scope_filter(self):
        from miya.services.ops.scoping import apply_location_scope

        for field in ("location_id", "business_location_id"):
            qs = MagicMock()
            apply_location_scope(qs, location_id="loc-casa", field=field)
            kwargs = qs.filter.call_args.kwargs or {}
            # Must pin exactly to active establishment
            self.assertEqual(kwargs.get(field), "loc-casa")
            self.assertNotIn("loc-rabat", str(kwargs))
