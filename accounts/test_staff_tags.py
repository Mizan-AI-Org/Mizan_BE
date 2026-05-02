"""Tests for ``accounts.staff_tags`` and the tag-aware staff endpoints."""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APIClient

from accounts.models import CustomUser, Restaurant, StaffProfile
from accounts.serializers import StaffProfileSerializer
from accounts.staff_tags import (
    CANONICAL_STAFF_TAGS,
    CANONICAL_STAFF_TAG_SET,
    normalize_tag,
    normalize_tags,
    tags_for_category,
)


class StaffTagHelperTests(SimpleTestCase):
    """Pure-Python helpers — no DB cost."""

    def test_normalize_tag_folds_case_and_separators(self):
        self.assertEqual(normalize_tag("kitchen"), "KITCHEN")
        self.assertEqual(normalize_tag("Front Office"), "FRONT_OFFICE")
        self.assertEqual(normalize_tag("back-office"), "BACK_OFFICE")
        self.assertEqual(normalize_tag("  PURCHASES  "), "PURCHASES")

    def test_normalize_tag_empties(self):
        self.assertIsNone(normalize_tag(None))
        self.assertIsNone(normalize_tag(""))
        self.assertIsNone(normalize_tag("   "))

    def test_normalize_tags_preserves_order_and_dedupes(self):
        self.assertEqual(
            normalize_tags(["kitchen", "Front Office", "kitchen", "", None]),
            ["KITCHEN", "FRONT_OFFICE"],
        )

    def test_normalize_tags_handles_none(self):
        self.assertEqual(normalize_tags(None), [])
        self.assertEqual(normalize_tags([]), [])

    def test_canonical_set_contains_ten_tags(self):
        self.assertEqual(len(CANONICAL_STAFF_TAGS), 10)
        for t in (
            "KITCHEN", "SERVICE", "FRONT_OFFICE", "BACK_OFFICE",
            "PURCHASES", "CONTROL", "ADMINISTRATION", "MANAGEMENT",
            "HOUSEKEEPING", "MARKETING",
        ):
            self.assertIn(t, CANONICAL_STAFF_TAG_SET)

    def test_tags_for_category_purchase_order_prefers_purchases(self):
        # The lead tag for PURCHASE_ORDER is PURCHASES — so the
        # tag-based routing fallback hits the buyer first when a
        # restaurant has tagged its team but hasn't filled in the
        # ``category_owners`` mapping.
        self.assertEqual(
            tags_for_category("PURCHASE_ORDER"),
            ("PURCHASES", "CONTROL", "MANAGEMENT"),
        )

    def test_tags_for_category_unknown_returns_empty(self):
        self.assertEqual(tags_for_category(""), ())
        self.assertEqual(tags_for_category(None), ())
        self.assertEqual(tags_for_category("UNKNOWN_BUCKET"), ())


class StaffProfileSerializerTagTests(TestCase):
    """The serializer is the gatekeeper between client payloads and
    the canonical vocabulary."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Test Bistro")
        self.user = CustomUser.objects.create_user(
            email="chef@test.com",
            password="x",
            first_name="Chef",
            last_name="Demo",
            role="CHEF",
            restaurant=self.restaurant,
        )
        # ``create_user`` may or may not create a profile depending on
        # signals — use ``get_or_create`` so the test is hermetic.
        self.profile, _ = StaffProfile.objects.get_or_create(user=self.user)

    def test_valid_tags_are_normalised_and_saved(self):
        s = StaffProfileSerializer(
            self.profile,
            data={"tags": ["kitchen", "back-office", "Front Office"]},
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tags, ["KITCHEN", "BACK_OFFICE", "FRONT_OFFICE"])

    def test_unknown_tags_raise_400(self):
        s = StaffProfileSerializer(
            self.profile,
            data={"tags": ["KITCHEN", "WAREHOUSE_5"]},
            partial=True,
        )
        self.assertFalse(s.is_valid())
        self.assertIn("tags", s.errors)

    def test_empty_tags_clears_the_field(self):
        self.profile.tags = ["KITCHEN"]
        self.profile.save(update_fields=["tags"])
        s = StaffProfileSerializer(self.profile, data={"tags": []}, partial=True)
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tags, [])

    def test_duplicate_tags_are_collapsed(self):
        s = StaffProfileSerializer(
            self.profile,
            data={"tags": ["kitchen", "KITCHEN", "Kitchen"]},
            partial=True,
        )
        self.assertTrue(s.is_valid(), s.errors)
        s.save()
        self.profile.refresh_from_db()
        self.assertEqual(self.profile.tags, ["KITCHEN"])


class StaffListTagFilterTests(TestCase):
    """``GET /api/staff/?tags=KITCHEN`` should return only chefs and
    similarly-tagged people. Multiple tags are ANDed."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Tag Bistro")
        self.manager = CustomUser.objects.create_user(
            email="manager@test.com",
            password="x",
            first_name="Mona",
            last_name="Manager",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        StaffProfile.objects.get_or_create(user=self.manager)

        def _make(email, first, last, role, tags):
            user = CustomUser.objects.create_user(
                email=email, password="x", first_name=first, last_name=last,
                role=role, restaurant=self.restaurant,
            )
            profile, _ = StaffProfile.objects.get_or_create(user=user)
            profile.tags = tags
            profile.save(update_fields=["tags"])
            return user

        self.chef = _make("chef@t.com", "Karim", "Chef", "CHEF", ["KITCHEN", "BACK_OFFICE"])
        self.waiter = _make("waiter@t.com", "Layla", "Waiter", "WAITER", ["SERVICE", "FRONT_OFFICE"])
        self.buyer = _make("buyer@t.com", "Reda", "Buyer", "MANAGER", ["PURCHASES", "CONTROL"])
        self.cleaner = _make("cleaner@t.com", "Sami", "Clean", "CLEANER", ["HOUSEKEEPING"])

        self.client = APIClient()
        self.client.force_authenticate(self.manager)

    def test_single_tag_filter(self):
        resp = self.client.get("/api/staff/?tags=KITCHEN&page_size=500")
        self.assertEqual(resp.status_code, status.HTTP_200_OK, resp.content)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        ids = {r["id"] for r in rows}
        self.assertIn(str(self.chef.id), ids)
        self.assertNotIn(str(self.waiter.id), ids)
        self.assertNotIn(str(self.buyer.id), ids)
        self.assertNotIn(str(self.cleaner.id), ids)

    def test_multiple_tags_are_anded(self):
        # Only the chef carries BOTH KITCHEN and BACK_OFFICE.
        resp = self.client.get("/api/staff/?tags=KITCHEN,BACK_OFFICE&page_size=500")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        ids = {r["id"] for r in rows}
        self.assertEqual(ids, {str(self.chef.id)})

    def test_lowercase_tag_is_normalised(self):
        resp = self.client.get("/api/staff/?tags=purchases&page_size=500")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        rows = resp.data.get("results", resp.data) if isinstance(resp.data, dict) else resp.data
        ids = {r["id"] for r in rows}
        self.assertIn(str(self.buyer.id), ids)

    def test_tags_endpoint_lists_canonical_set(self):
        resp = self.client.get("/api/staff/tags/")
        self.assertEqual(resp.status_code, status.HTTP_200_OK)
        ids = [t["id"] for t in resp.data["tags"]]
        for canonical in CANONICAL_STAFF_TAGS:
            self.assertIn(canonical, ids)
        # And the category mapping is exposed for FE smart filters.
        self.assertIn("PURCHASE_ORDER", resp.data["category_to_tags"])
        self.assertEqual(
            resp.data["category_to_tags"]["PURCHASE_ORDER"],
            ["PURCHASES", "CONTROL", "MANAGEMENT"],
        )


class TagRoutingFallbackTests(TestCase):
    """When ``category_owners`` isn't configured, the routing helper
    should fall through to whoever carries the matching tag."""

    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Routing Bistro")
        # Deliberately empty general_settings — the fallback path is
        # what we want to exercise.
        self.restaurant.general_settings = {}
        self.restaurant.save(update_fields=["general_settings"])

        def _make(email, first, last, role, tags):
            user = CustomUser.objects.create_user(
                email=email, password="x", first_name=first, last_name=last,
                role=role, restaurant=self.restaurant,
            )
            profile, _ = StaffProfile.objects.get_or_create(user=user)
            profile.tags = tags
            profile.save(update_fields=["tags"])
            return user

        self.buyer = _make("buyer@r.com", "Aisha", "Buyer", "MANAGER", ["PURCHASES"])
        self.chef = _make("chef@r.com", "Brahim", "Cook", "CHEF", ["KITCHEN", "BACK_OFFICE"])
        self.maintenance_owner = _make(
            "maint@r.com", "Yousra", "Maintenance", "MANAGER", ["BACK_OFFICE"],
        )

    def test_purchase_order_falls_back_to_purchases_tag(self):
        from staff.request_routing import resolve_default_assignee_for_category

        owner = resolve_default_assignee_for_category(self.restaurant, "PURCHASE_ORDER")
        self.assertIsNotNone(owner)
        self.assertEqual(owner.id, self.buyer.id)

    def test_inventory_falls_back_to_purchases_then_kitchen(self):
        # Drop the buyer; expect the chef (KITCHEN) to be picked instead.
        from staff.request_routing import resolve_default_assignee_for_category

        StaffProfile.objects.filter(user=self.buyer).update(tags=[])
        owner = resolve_default_assignee_for_category(self.restaurant, "INVENTORY")
        self.assertIsNotNone(owner)
        self.assertEqual(owner.id, self.chef.id)

    def test_maintenance_falls_back_to_back_office(self):
        from staff.request_routing import resolve_default_assignee_for_category

        owner = resolve_default_assignee_for_category(self.restaurant, "MAINTENANCE")
        self.assertIsNotNone(owner)
        # Both the chef and maintenance manager carry BACK_OFFICE; the
        # MANAGER should win on role-priority sorting.
        self.assertEqual(owner.id, self.maintenance_owner.id)

    def test_other_category_returns_none(self):
        from staff.request_routing import resolve_default_assignee_for_category

        self.assertIsNone(
            resolve_default_assignee_for_category(self.restaurant, "OTHER")
        )

    def test_explicit_category_owner_wins_over_tag(self):
        """If the manager explicitly mapped a category to a person, we
        respect that even when tagged staff exist."""
        from staff.request_routing import resolve_default_assignee_for_category

        self.restaurant.general_settings = {
            "category_owners": {"request.purchase_order": str(self.maintenance_owner.id)},
        }
        self.restaurant.save(update_fields=["general_settings"])

        owner = resolve_default_assignee_for_category(self.restaurant, "PURCHASE_ORDER")
        self.assertIsNotNone(owner)
        self.assertEqual(owner.id, self.maintenance_owner.id)
