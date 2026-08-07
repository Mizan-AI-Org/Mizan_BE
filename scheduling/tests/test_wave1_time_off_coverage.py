"""Wave 1 domain tests — request_time_off and assign_coverage notifications."""

from __future__ import annotations

from datetime import timedelta
from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from accounts.models import BusinessLocation, CustomUser, Restaurant
from notifications.models import Notification
from scheduling.models import AssignedShift, TimeOffRequest, WeeklySchedule
from scheduling.services import SchedulingService


class TimeOffDomainServiceTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Wave1 Cafe", email="w1@cafe.test")
        gs = dict(self.restaurant.general_settings or {})
        gs["category_owners"] = {"request.scheduling": []}
        self.restaurant.general_settings = gs
        self.restaurant.save(update_fields=["general_settings"])

        self.loc_a = BusinessLocation.objects.create(
            restaurant=self.restaurant, name="Branch A", is_primary=True, is_active=True
        )
        self.loc_b = BusinessLocation.objects.create(
            restaurant=self.restaurant, name="Branch B", is_active=True
        )

        self.manager = CustomUser.objects.create_user(
            email="mgr-w1@cafe.test",
            password="pass12345",
            first_name="Scheduling",
            last_name="Manager",
            role="MANAGER",
            restaurant=self.restaurant,
            phone="+212611111111",
        )
        self.staff = CustomUser.objects.create_user(
            email="staff-w1@cafe.test",
            password="pass12345",
            first_name="Line",
            last_name="Cook",
            role="WAITER",
            restaurant=self.restaurant,
            phone="+212622222222",
            primary_location=self.loc_a,
        )
        self.other_staff = CustomUser.objects.create_user(
            email="other-w1@cafe.test",
            password="pass12345",
            first_name="Other",
            last_name="Branch",
            role="WAITER",
            restaurant=self.restaurant,
            primary_location=self.loc_b,
        )
        self.other_staff.allowed_locations.set([self.loc_b])

        gs = dict(self.restaurant.general_settings or {})
        gs["category_owners"] = {"request.scheduling": [str(self.manager.id)]}
        self.restaurant.general_settings = gs
        self.restaurant.save(update_fields=["general_settings"])

        self.start = timezone.localdate() + timedelta(days=14)
        self.end = self.start + timedelta(days=2)

    @patch("notifications.services.notification_service.send_whatsapp_text", return_value=(True, {}))
    @patch("notifications.services.notification_service.send_custom_notification", return_value=(True, None))
    def test_successful_request_notifies_manager_once(self, _app, _wa):
        result = SchedulingService.create_time_off_request(
            restaurant=self.restaurant,
            staff=self.staff,
            start_date=self.start,
            end_date=self.end,
            request_type="VACATION",
            reason="Family trip",
        )
        tor = result["time_off_request"]
        self.assertFalse(result["idempotent"])
        self.assertTrue(result["manager_notified"])
        self.assertEqual(result["manager_id"], str(self.manager.id))
        self.assertEqual(tor.status, "PENDING")

        notifs = Notification.objects.filter(
            notification_type="AVAILABILITY_REQUEST",
            data__time_off_request_id=str(tor.id),
        )
        self.assertEqual(notifs.count(), 1)
        self.assertEqual(str(notifs.first().recipient_id), str(self.manager.id))

    @patch("notifications.services.notification_service.send_whatsapp_text", return_value=(True, {}))
    @patch("notifications.services.notification_service.send_custom_notification", return_value=(True, None))
    def test_duplicate_retry_is_idempotent_single_notification(self, _app, _wa):
        first = SchedulingService.create_time_off_request(
            restaurant=self.restaurant,
            staff=self.staff,
            start_date=self.start,
            end_date=self.end,
        )
        second = SchedulingService.create_time_off_request(
            restaurant=self.restaurant,
            staff=self.staff,
            start_date=self.start,
            end_date=self.end,
        )
        self.assertTrue(second["idempotent"])
        self.assertFalse(second["manager_notified"])
        self.assertEqual(first["time_off_request"].id, second["time_off_request"].id)
        self.assertEqual(TimeOffRequest.objects.filter(staff=self.staff, status="PENDING").count(), 1)
        self.assertEqual(
            Notification.objects.filter(
                notification_type="AVAILABILITY_REQUEST",
                data__time_off_request_id=str(first["time_off_request"].id),
            ).count(),
            1,
        )

    def test_wrong_establishment_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            SchedulingService.create_time_off_request(
                restaurant=self.restaurant,
                staff=self.other_staff,
                start_date=self.start,
                end_date=self.end,
                location_id=str(self.loc_a.id),
            )
        self.assertEqual(str(ctx.exception), "location_mismatch")
        self.assertEqual(TimeOffRequest.objects.count(), 0)

    def test_staff_not_in_restaurant_rejected(self):
        outsider = CustomUser.objects.create_user(
            email="outsider@cafe.test",
            password="pass12345",
            first_name="Out",
            last_name="Side",
            role="WAITER",
            restaurant=Restaurant.objects.create(name="Other Org", email="other@test"),
        )
        with self.assertRaises(ValueError) as ctx:
            SchedulingService.create_time_off_request(
                restaurant=self.restaurant,
                staff=outsider,
                start_date=self.start,
                end_date=self.end,
            )
        self.assertEqual(str(ctx.exception), "staff_not_in_restaurant")


class AssignCoverageDomainServiceTests(TestCase):
    def setUp(self):
        self.restaurant = Restaurant.objects.create(name="Coverage Cafe", email="cov@cafe.test")
        self.manager = CustomUser.objects.create_user(
            email="mgr-cov@cafe.test",
            password="pass12345",
            first_name="Mgr",
            last_name="Cov",
            role="MANAGER",
            restaurant=self.restaurant,
        )
        self.original = CustomUser.objects.create_user(
            email="orig@cafe.test",
            password="pass12345",
            first_name="Original",
            last_name="Staff",
            role="WAITER",
            restaurant=self.restaurant,
        )
        self.cover = CustomUser.objects.create_user(
            email="cover@cafe.test",
            password="pass12345",
            first_name="Cover",
            last_name="Staff",
            role="WAITER",
            restaurant=self.restaurant,
            phone="+212633333333",
        )
        schedule = WeeklySchedule.objects.create(
            restaurant=self.restaurant,
            week_start=timezone.localdate(),
            week_end=timezone.localdate() + timedelta(days=6),
        )
        self.shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=self.original,
            shift_date=timezone.localdate() + timedelta(days=1),
            role="WAITER",
            status="NO_SHOW",
        )

    @patch("scheduling.services.SchedulingService.notify_shift_assignment")
    def test_successful_assignment_notifies_assignee(self, mock_notify):
        result = SchedulingService.assign_shift_coverage(
            self.shift, self.cover, restaurant=self.restaurant
        )
        self.shift.refresh_from_db()
        self.assertFalse(result["idempotent"])
        self.assertTrue(result["notification_sent"])
        self.assertEqual(self.shift.staff_id, self.cover.id)
        self.assertEqual(self.shift.status, "CONFIRMED")
        mock_notify.assert_called_once()

    @patch("scheduling.services.SchedulingService.notify_shift_assignment")
    def test_duplicate_retry_skips_second_notification(self, mock_notify):
        first = SchedulingService.assign_shift_coverage(
            self.shift, self.cover, restaurant=self.restaurant
        )
        second = SchedulingService.assign_shift_coverage(
            self.shift, self.cover, restaurant=self.restaurant
        )
        self.assertFalse(first["idempotent"])
        self.assertTrue(second["idempotent"])
        self.assertFalse(second["notification_sent"])
        mock_notify.assert_called_once()

    @patch("notifications.services.notification_service.send_whatsapp_text", return_value=(True, {}))
    @patch("notifications.services.notification_service.send_custom_notification", return_value=(True, None))
    def test_assign_coverage_creates_shift_assigned_notification(self, _app, _wa):
        SchedulingService.assign_shift_coverage(
            self.shift, self.cover, restaurant=self.restaurant
        )
        notifs = Notification.objects.filter(
            recipient=self.cover,
            notification_type="SHIFT_ASSIGNED",
            related_shift_id=self.shift.id,
        )
        self.assertEqual(notifs.count(), 1)

    def test_staff_not_in_restaurant_rejected(self):
        outsider = CustomUser.objects.create_user(
            email="outsider2@cafe.test",
            password="pass12345",
            first_name="Out",
            last_name="Side",
            role="WAITER",
            restaurant=Restaurant.objects.create(name="Elsewhere", email="e@test"),
        )
        with self.assertRaises(ValueError) as ctx:
            SchedulingService.assign_shift_coverage(
                self.shift, outsider, restaurant=self.restaurant
            )
        self.assertEqual(str(ctx.exception), "staff_not_in_restaurant")

    def test_shift_not_in_restaurant_rejected(self):
        other_rest = Restaurant.objects.create(name="Other Rest", email="or@test")
        with self.assertRaises(ValueError) as ctx:
            SchedulingService.assign_shift_coverage(
                self.shift, self.cover, restaurant=other_rest
            )
        self.assertEqual(str(ctx.exception), "shift_not_in_restaurant")
