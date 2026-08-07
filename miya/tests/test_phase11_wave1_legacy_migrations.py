"""Phase 11 Wave 1 — legacy mutation migration regression tests."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.mutation_pipeline import finalize_legacy_tool_response
from miya.services.intelligence.mutation_registry import is_legacy_http_mutation
from miya.services.ops import CANONICAL_TOOL_NAMES, dispatch_canonical_tool
from miya.services.ops.result import fail, ok


WAVE1_TOOLS = (
    "staff_clock_in",
    "staff_clock_out",
    "staff_request",
    "approve_staff_request",
    "reject_staff_request",
    "request_time_off",
    "create_shift",
    "assign_coverage",
    "mark_no_show",
    "assign_invoice",
    "send_announcement",
    "notify_manager_urgent",
    "chase_operational_record",
    "report_waste",
    "update_compliance_document",
    "recognize_staff",
)


class Wave1RegistryTests(SimpleTestCase):
    def test_all_sixteen_are_canonical(self):
        for tool in WAVE1_TOOLS:
            self.assertIn(tool, CANONICAL_TOOL_NAMES, tool)

    def test_none_are_legacy_http(self):
        for tool in WAVE1_TOOLS:
            self.assertFalse(is_legacy_http_mutation(tool), tool)

    def test_deferred_tools_not_legacy_blocked(self):
        """Deferred OCR/admin tools remain on legacy path until Phase 14."""
        self.assertFalse(is_legacy_http_mutation("parse_photo"))

    def test_unknown_route_not_treated_as_verified_mutation(self):
        body = finalize_legacy_tool_response(
            "totally_unknown_tool",
            status_code=200,
            body={"success": True},
        )
        # Unknown tools are reads or routing gaps — not verified mutations
        self.assertTrue(body.get("success"))


class Wave1DispatchTests(SimpleTestCase):
    def _ctx(self, *, role="MANAGER", location_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        user.pk = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        user.role = role
        user.phone = "+212600000000"
        rest = MagicMock()
        rest.id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            role=role,
            channel="dashboard",
            language="en",
            location_id=location_id,
            available_locations=[
                {"id": "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa", "name": "Branch A"},
                {"id": "dddddddd-dddd-dddd-dddd-dddddddddddd", "name": "Branch B"},
            ],
        )

    @patch("miya.services.ops.wave1_mutations.run_verified_agent_mutation")
    @patch("miya.services.ops.wave1_mutations._load_shift")
    def test_mark_no_show_dispatches_canonical(self, mock_load, mock_run):
        shift = MagicMock()
        shift.id = "11111111-1111-1111-1111-111111111111"
        mock_load.return_value = shift
        mock_run.return_value = ok(
            message="ok", verified=True, data={"shift_id": "11111111-1111-1111-1111-111111111111"}
        )
        result = dispatch_canonical_tool(
            "mark_no_show",
            {"shift_id": "11111111-1111-1111-1111-111111111111"},
            ctx=self._ctx(),
        )
        self.assertIsNotNone(result)
        self.assertTrue(result.verified)
        mock_run.assert_called_once()

    @patch("miya.services.ops.agent_bridge.require_permission")
    def test_assign_invoice_requires_permission(self, mock_perm):
        mock_perm.return_value = fail(code="permission_denied", message="denied")
        from miya.services.ops.wave1_mutations import assign_invoice

        result = assign_invoice(
            self._ctx(),
            invoice_id="11111111-1111-1111-1111-111111111111",
            assignee_id="22222222-2222-2222-2222-222222222222",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "permission_denied")

    @patch("miya.services.ops.agent_bridge.guard_entity_location")
    @patch("miya.services.ops.wave1_mutations._load_shift")
    def test_assign_coverage_blocks_wrong_establishment(self, mock_load, mock_guard):
        from miya.services.ops.wave1_mutations import assign_coverage

        shift = MagicMock()
        shift.id = "11111111-1111-1111-1111-111111111111"
        shift.location_id = "dddddddd-dddd-dddd-dddd-dddddddddddd"
        mock_load.return_value = shift
        mock_guard.return_value = fail(
            code="location_mismatch",
            message="That record belongs to another establishment.",
        )
        with patch("miya.services.ops.agent_bridge.require_permission", return_value=None):
            result = assign_coverage(
                self._ctx(location_id="aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
                shift_id="11111111-1111-1111-1111-111111111111",
                staff_id="22222222-2222-2222-2222-222222222222",
            )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "location_mismatch")


class Wave1VerificationTests(SimpleTestCase):
    def _ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("scheduling.models.AssignedShift")
    def test_mark_no_show_verify_success(self, ShiftModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import mark_no_show

        mock_dispatch.return_value = (200, {"success": True, "shift_id": "11111111-1111-1111-1111-111111111111", "message": "done"})
        shift = MagicMock()
        shift.id = "11111111-1111-1111-1111-111111111111"
        shift.status = "NO_SHOW"
        ShiftModel.objects.filter.return_value.first.return_value = shift

        with patch("miya.services.ops.wave1_mutations._load_shift", return_value=shift), patch(
            "miya.services.ops.wave1_mutations.require_permission", return_value=None
        ):
            result = mark_no_show(self._ctx(), shift_id="11111111-1111-1111-1111-111111111111")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("scheduling.models.AssignedShift")
    def test_mark_no_show_verify_failure(self, ShiftModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import mark_no_show

        mock_dispatch.return_value = (200, {"success": True, "shift_id": "11111111-1111-1111-1111-111111111111"})
        shift = MagicMock()
        shift.id = "11111111-1111-1111-1111-111111111111"
        shift.status = "NO_SHOW"
        stale = MagicMock()
        stale.id = "11111111-1111-1111-1111-111111111111"
        stale.status = "SCHEDULED"
        ShiftModel.objects.filter.return_value.first.return_value = stale

        with patch("miya.services.ops.wave1_mutations._load_shift", return_value=shift), patch(
            "miya.services.ops.wave1_mutations.require_permission", return_value=None
        ):
            result = mark_no_show(self._ctx(), shift_id="11111111-1111-1111-1111-111111111111")
        self.assertFalse(result.success)
        self.assertEqual(result.code, "verify_failed")

    @patch("notifications.services.notification_service.send_announcement_to_audience")
    @patch("notifications.models.Notification")
    def test_send_announcement_verified(self, NotifModel, mock_send):
        from miya.services.ops.wave1_mutations import send_announcement

        mock_send.return_value = (True, 3, None, {"whatsapp_sent": 2})
        qs = MagicMock()
        qs.count.side_effect = [0, 3]
        NotifModel.objects.filter.return_value = qs

        with patch("miya.services.ops.wave1_mutations.require_permission", return_value=None):
            result = send_announcement(self._ctx(), message="Holiday tomorrow", audience="all")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    @patch("staff.models.StaffRequest")
    def test_staff_request_external_id_dedup(self, StaffRequestModel):
        from miya.services.ops.wave1_mutations import staff_request

        existing = MagicMock()
        existing.id = "req-existing"
        StaffRequestModel.objects.filter.return_value.first.return_value = existing

        result = staff_request(self._ctx(), description="Need leave", external_id="wa-msg-99")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertTrue((result.data or {}).get("deduplicated"))


class HighTrafficWave1Tests(SimpleTestCase):
    """staff_clock_in/out, staff_request, send_announcement — Wave 1 high-traffic paths."""

    def _ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb"
        user.role = "STAFF"
        user.phone = "+212600000000"
        rest = MagicMock()
        rest.id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id="bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
            role="STAFF",
            channel="whatsapp",
            language="en",
        )

    def test_high_traffic_tools_are_canonical(self):
        for tool in ("staff_clock_in", "staff_clock_out", "staff_request", "send_announcement"):
            self.assertIn(tool, CANONICAL_TOOL_NAMES)
            self.assertFalse(is_legacy_http_mutation(tool))

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("timeclock.models.ClockEvent")
    def test_staff_clock_out_verifies_by_event_id(self, ClockEventModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import staff_clock_out

        event_id = "eeeeeeee-eeee-eeee-eeee-eeeeeeeeeeee"
        mock_dispatch.return_value = (
            200,
            {
                "success": True,
                "staff_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "clock_event_id": event_id,
                "message_for_user": "Clocked out.",
            },
        )
        ev = MagicMock()
        ev.id = event_id
        ClockEventModel.objects.filter.return_value.first.return_value = ev

        result = staff_clock_out(self._ctx())
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual((result.data or {}).get("clock_event_id"), event_id)

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("timeclock.models.ClockEvent")
    def test_staff_clock_in_verifies_by_event_id(self, ClockEventModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import staff_clock_in

        event_id = "ffffffff-ffff-ffff-ffff-ffffffffffff"
        mock_dispatch.return_value = (
            200,
            {
                "success": True,
                "staff_id": "bbbbbbbb-bbbb-bbbb-bbbb-bbbbbbbbbbbb",
                "clock_event_id": event_id,
                "message_for_user": "Clocked in.",
            },
        )
        ev = MagicMock()
        ev.id = event_id
        ClockEventModel.objects.filter.return_value.first.return_value = ev

        result = staff_clock_in(self._ctx(), latitude=33.5, longitude=-7.6)
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("staff.models.StaffRequest")
    def test_staff_request_verifies_pending_row(self, StaffRequestModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import staff_request

        req_id = "aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"
        mock_dispatch.return_value = (201, {"success": True, "id": req_id, "message_for_user": "Logged."})
        req = MagicMock()
        req.id = req_id
        req.status = "PENDING"
        StaffRequestModel.objects.filter.return_value.first.return_value = req

        result = staff_request(self._ctx(), description="I need next Friday off")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual((result.data or {}).get("request_id"), req_id)


class RequestTimeOffWave1Tests(SimpleTestCase):
    def _ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "staff-1"
        user.role = "STAFF"
        user.phone = "+212600000000"
        rest = MagicMock()
        rest.id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id="staff-1",
            role="STAFF",
            channel="whatsapp",
            language="en",
        )

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("scheduling.models.TimeOffRequest")
    def test_request_time_off_verifies_pending_row(self, TorModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import request_time_off

        tor_id = "tor-1111-1111-1111-111111111111"
        mock_dispatch.return_value = (
            201,
            {
                "success": True,
                "id": tor_id,
                "manager_notified": True,
                "idempotent": False,
            },
        )
        tor = MagicMock()
        tor.id = tor_id
        tor.status = "PENDING"
        TorModel.objects.filter.return_value.first.return_value = tor

        result = request_time_off(
            self._ctx(),
            phone="+212600000000",
            start_date="2026-09-01",
            end_date="2026-09-03",
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual((result.data or {}).get("time_off_id"), tor_id)

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("scheduling.models.TimeOffRequest")
    def test_request_time_off_verify_failure(self, TorModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import request_time_off

        mock_dispatch.return_value = (201, {"success": True, "id": "tor-x"})
        TorModel.objects.filter.return_value.first.return_value = None

        result = request_time_off(
            self._ctx(),
            phone="+212600000000",
            start_date="2026-09-01",
            end_date="2026-09-03",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "verify_failed")


class AssignCoverageWave1Tests(SimpleTestCase):
    def _ctx(self):
        from miya.services.ops.context import OpsContext

        user = MagicMock()
        user.id = "mgr-1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "cccccccc-cccc-cccc-cccc-cccccccccccc"
        return OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="cccccccc-cccc-cccc-cccc-cccccccccccc",
            user_id="mgr-1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )

    @patch("miya.services.ops.agent_bridge.dispatch_agent_post")
    @patch("scheduling.models.AssignedShift")
    def test_assign_coverage_verifies_confirmed_staff(self, ShiftModel, mock_dispatch):
        from miya.services.ops.wave1_mutations import assign_coverage

        shift_id = "11111111-1111-1111-1111-111111111111"
        staff_id = "22222222-2222-2222-2222-222222222222"
        mock_dispatch.return_value = (
            200,
            {
                "success": True,
                "shift_id": shift_id,
                "staff_id": staff_id,
                "notification_sent": True,
            },
        )
        shift = MagicMock()
        shift.id = shift_id
        shift.staff_id = staff_id
        shift.status = "CONFIRMED"
        ShiftModel.objects.filter.return_value.first.return_value = shift

        with patch("miya.services.ops.wave1_mutations._load_shift", return_value=shift), patch(
            "miya.services.ops.wave1_mutations.require_permission", return_value=None
        ):
            result = assign_coverage(self._ctx(), shift_id=shift_id, staff_id=staff_id)
        self.assertTrue(result.success)
        self.assertTrue(result.verified)

    @patch("miya.services.ops.wave1_mutations.require_permission")
    def test_assign_coverage_unauthorized(self, mock_perm):
        from miya.services.ops.wave1_mutations import assign_coverage

        mock_perm.return_value = fail(code="permission_denied", message="denied")
        result = assign_coverage(
            self._ctx(),
            shift_id="11111111-1111-1111-1111-111111111111",
            staff_id="22222222-2222-2222-2222-222222222222",
        )
        self.assertFalse(result.success)
        self.assertEqual(result.code, "permission_denied")


class Wave1StructuredActionTests(SimpleTestCase):
    @patch("miya.services.intelligence.actions._finish")
    @patch("miya.services.intelligence.actions.claim_operation_once", return_value=True)
    @patch("miya.services.ops.wave1_mutations.mark_no_show")
    def test_structured_action_routes_mark_no_show(self, mock_handler, *_mocks):
        from miya.services.intelligence.actions import execute_structured_action
        from miya.services.ops.context import OpsContext

        user = MagicMock(id="u1", pk="u1", role="MANAGER")
        rest = MagicMock(id="r1")
        ctx = OpsContext(
            user=user,
            restaurant=rest,
            restaurant_id="r1",
            user_id="u1",
            role="MANAGER",
            channel="dashboard",
            language="en",
        )
        mock_handler.return_value = ok(
            message="ok", verified=True, data={"shift_id": "11111111-1111-1111-1111-111111111111"}
        )
        result = execute_structured_action(
            "mark_no_show",
            {"shift_id": "11111111-1111-1111-1111-111111111111"},
            ctx=ctx,
            execution_context={"message_id": "m1"},
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        mock_handler.assert_called_once()
