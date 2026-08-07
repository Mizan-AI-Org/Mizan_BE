"""Phase 4: responsibility routing — multi-owner, slugs, isolation, audit contract."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from staff.responsibility import (
    normalize_category_code,
    slugs_for_responsibility,
)


class SlugContractTests(SimpleTestCase):
    def test_finance_maps_to_onboarding_slugs(self):
        slugs = slugs_for_responsibility(None, "finance")
        self.assertIn("request.finance", slugs)
        self.assertIn("task.finance", slugs)

    def test_incident_multi_slugs(self):
        slugs = slugs_for_responsibility(None, "INCIDENT")
        self.assertTrue(any(s.startswith("incident.") for s in slugs))

    def test_deliveries_alias(self):
        self.assertEqual(normalize_category_code("deliveries"), "DELIVERIES")
        slugs = slugs_for_responsibility(None, "DELIVERIES")
        self.assertTrue(slugs)


class AssignResponsibilityMultiOwnerTests(SimpleTestCase):
    def test_assign_writes_canonical_slugs_not_finance_uppercase(self):
        from miya.services.ops.categories import assign_responsibility
        from miya.services.ops.context import OpsContext

        rest = MagicMock()
        rest.id = "r1"
        rest.general_settings = {}
        user = MagicMock()
        user.id = "mgr"
        user.pk = "mgr"
        user.role = "MANAGER"
        ctx = OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="mgr",
            role="MANAGER",
            channel="dashboard",
        )

        a = MagicMock()
        a.id = "u-finance"
        a.first_name = "Fatima"
        a.last_name = "F"
        a.email = "f@ex.com"
        a.role = "MANAGER"
        a.phone = "212611111111"

        with patch(
            "miya.services.ops.categories.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.categories.require_permission", return_value=None
        ), patch(
            "dashboard.views_agent._resolve_assignee", return_value=(a, None)
        ), patch(
            "staff.responsibility.set_responsible_people"
        ) as mock_set:
            mock_set.return_value = {
                "category": "FINANCE",
                "slugs": ["request.finance", "task.finance"],
                "owner_ids": ["u-finance"],
                "owners": [{"id": "u-finance", "name": "Fatima F", "role": "MANAGER"}],
                "strategy": "first_available",
                "location_id": None,
            }
            result = assign_responsibility(ctx, category="finance", owner_name="Fatima")

        self.assertTrue(result.success)
        mock_set.assert_called_once()
        kwargs = mock_set.call_args.kwargs
        self.assertEqual(kwargs["category"], "finance")
        self.assertEqual(kwargs["owner_ids"], ["u-finance"])

    def test_multi_owner_names(self):
        from miya.services.ops.categories import assign_responsibility
        from miya.services.ops.context import OpsContext

        rest = MagicMock()
        rest.id = "r1"
        rest.general_settings = {}
        ctx = OpsContext(
            user=MagicMock(id="m", pk="m", role="MANAGER"),
            restaurant=rest,
            restaurant_id="r1",
            user_id="m",
            role="MANAGER",
        )
        mgr = MagicMock(id="u1", first_name="Ali", last_name="M", email="a@x.com", role="MANAGER", phone="1")
        hr = MagicMock(id="u2", first_name="Sara", last_name="H", email="s@x.com", role="MANAGER", phone="2")

        def resolve(data, restaurant):
            name = (data.get("assignee_name") or "").lower()
            if "ali" in name:
                return mgr, None
            if "sara" in name:
                return hr, None
            return None, "not found"

        with patch(
            "miya.services.ops.categories.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.categories.require_permission", return_value=None
        ), patch(
            "dashboard.views_agent._resolve_assignee", side_effect=resolve
        ), patch(
            "staff.responsibility.set_responsible_people"
        ) as mock_set:
            mock_set.return_value = {
                "category": "INCIDENT",
                "slugs": ["incident.safety"],
                "owner_ids": ["u1", "u2"],
                "owners": [
                    {"id": "u1", "name": "Ali M"},
                    {"id": "u2", "name": "Sara H"},
                ],
                "strategy": "notify_all",
                "location_id": None,
            }
            result = assign_responsibility(
                ctx,
                category="INCIDENT",
                owner_names=["Ali", "Sara"],
            )

        self.assertTrue(result.success)
        self.assertEqual(set(mock_set.call_args.kwargs["owner_ids"]), {"u1", "u2"})


class LocationIsolationTests(SimpleTestCase):
    def test_location_owners_do_not_leak(self):
        from staff.responsibility import resolve_responsibility

        rest = MagicMock()
        rest.id = "r1"
        loc_a = "loc-a"
        loc_b = "loc-b"
        owner_a = "user-a"
        owner_b = "user-b"
        rest.general_settings = {
            "category_owners": {"request.finance": [owner_b]},
            "category_owners_by_location": {
                loc_a: {"request.finance": [owner_a]},
            },
            "category_routing": {"request.finance": {"strategy": "notify_all"}},
        }

        user_a = MagicMock()
        user_a.id = owner_a
        user_a.first_name = "A"
        user_a.last_name = ""
        user_a.email = "a@x.com"
        user_a.role = "MANAGER"
        user_a.restaurant_id = "r1"

        user_b = MagicMock()
        user_b.id = owner_b
        user_b.first_name = "B"
        user_b.last_name = ""
        user_b.email = "b@x.com"
        user_b.role = "MANAGER"
        user_b.restaurant_id = "r1"

        def lookup(restaurant, uid):
            if str(uid) == owner_a:
                return user_a
            if str(uid) == owner_b:
                return user_b
            return None

        with patch("staff.category_routing_engine._lookup_user", side_effect=lookup):
            at_a = resolve_responsibility(rest, category="FINANCE", location_id=loc_a)
            # loc_b has no overlay → tenant default owner_b
            at_b = resolve_responsibility(rest, category="FINANCE", location_id=loc_b)

        self.assertEqual([str(u.id) for u in at_a.owners], [owner_a])
        self.assertEqual([str(u.id) for u in at_b.owners], [owner_b])
        self.assertNotEqual(
            [str(u.id) for u in at_a.owners],
            [str(u.id) for u in at_b.owners],
        )


class RouteEventContractTests(SimpleTestCase):
    def test_route_event_creates_task_and_notifies(self):
        from staff.responsibility import route_event

        rest = MagicMock()
        rest.id = "r1"
        rest.general_settings = {
            "category_owners": {"request.orders": ["u1", "u2"]},
            "category_routing": {"request.orders": {"strategy": "notify_all"}},
            "responsibility_categories": {
                "ORDERS": {"label": "Orders", "kind": "request", "slugs": ["request.orders"]}
            },
        }
        primary = MagicMock()
        primary.id = "u1"
        primary.first_name = "Kitchen"
        primary.last_name = "Mgr"
        primary.email = "k@x.com"
        primary.phone = "212600000001"
        informed = MagicMock()
        informed.id = "u2"
        informed.first_name = "Purch"
        informed.last_name = "Mgr"
        informed.email = "p@x.com"
        informed.phone = "212600000002"

        routing = MagicMock()
        routing.primary = primary
        routing.owners = [primary, informed]
        routing.informed = [informed]
        routing.notify_targets = [primary, informed]
        routing.strategy = "notify_all"
        routing.slug = "request.orders"

        task = MagicMock()
        task.id = "task-1"
        task.assignees = MagicMock()

        with patch(
            "staff.responsibility.resolve_responsibility", return_value=routing
        ), patch(
            "dashboard.models.Task"
        ) as TaskModel, patch(
            "dashboard.task_assign_notify.notify_task_assignment"
        ) as notify, patch(
            "dashboard.task_sync.broadcast_tasks_invalidate"
        ), patch(
            "staff.responsibility._audit"
        ) as audit:
            TaskModel.objects.create.return_value = task
            out = route_event(
                rest,
                category="ORDERS",
                kind="task",
                title="Prep dinner mise",
                create_task=True,
                notify=True,
                actor=MagicMock(id="mgr", pk="mgr"),
            )

        self.assertTrue(out["success"])
        self.assertEqual(out["task_id"], "task-1")
        notify.assert_called_once()
        self.assertEqual(notify.call_args.kwargs.get("informed_owners"), [informed])
        audit.assert_called()


class CreateCategoryTests(SimpleTestCase):
    def test_create_orders_category(self):
        from miya.services.ops.categories import create_category
        from miya.services.ops.context import OpsContext

        rest = MagicMock()
        rest.id = "r1"
        rest.general_settings = {}
        ctx = OpsContext(
            user=MagicMock(id="m", pk="m", role="OWNER"),
            restaurant=rest,
            restaurant_id="r1",
            user_id="m",
            role="OWNER",
        )
        with patch(
            "miya.services.ops.categories.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.categories.require_permission", return_value=None
        ), patch(
            "staff.responsibility.create_responsibility_category"
        ) as mock_create:
            mock_create.return_value = {
                "code": "ORDERS",
                "label": "Orders",
                "kind": "request",
                "slugs": ["request.orders"],
            }
            result = create_category(ctx, code="ORDERS", label="Orders")
        self.assertTrue(result.success)
        mock_create.assert_called_once()


class DashboardMiyaWhatsAppSameResolverTests(SimpleTestCase):
    def test_same_resolve_entrypoints(self):
        """Dashboard agent, Miya tools, and WA all use staff.responsibility."""
        import inspect

        import dashboard.views_ops_memory as ops_memory
        from miya.services.ops.categories import assign_responsibility, find_category_owners

        self.assertIn("set_responsible_people", inspect.getsource(ops_memory))
        self.assertIn("set_responsible_people", inspect.getsource(assign_responsibility))
        self.assertIn("resolve_responsibility", inspect.getsource(find_category_owners))
