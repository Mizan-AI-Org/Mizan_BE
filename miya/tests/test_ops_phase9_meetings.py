"""Phase 9: reminders & meetings reliability — Dashboard / Miya / WhatsApp / Calendar parity."""
from __future__ import annotations

from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase
from django.utils import timezone

from miya.services.ops import CANONICAL_TOOL_NAMES, dispatch_canonical_tool
from miya.services.ops.context import OpsContext
from scheduling.calendar_reminder_sync import (
    cancel_calendar_event_reminder,
    meeting_kind_from_text,
    normalize_meeting_kind,
    title_with_meeting_kind,
)


def _ctx(*, role="OWNER"):
    rest = MagicMock()
    rest.id = "rest-1"
    rest.name = "Mizan Group"
    rest.timezone = "Africa/Casablanca"
    user = MagicMock()
    user.id = "u-mgr"
    user.pk = "u-mgr"
    user.role = role
    user.is_active = True
    user.phone = "+212600000001"
    user.email = "mgr@ex.com"
    user.first_name = "Maya"
    user.last_name = "Manager"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="rest-1",
        user_id="u-mgr",
        role=role,
        channel="dashboard",
    )


def _reminder(*, title="Task reminder", recurrence="none", body="", status="pending", due=None):
    rem = MagicMock()
    rem.id = "rem-1"
    rem.title = title
    rem.body = body
    rem.status = status
    rem.recurrence = recurrence
    rem.due_at = due or (timezone.now() + timedelta(hours=2))
    rem.linked_compliance_document_id = None
    rem.updated_at = timezone.now()
    return rem


class DepartmentMeetingKindTests(SimpleTestCase):
    def test_normalize_foh_kitchen_manager(self):
        self.assertEqual(normalize_meeting_kind("FOH"), "FOH")
        self.assertEqual(normalize_meeting_kind("front of house"), "FOH")
        self.assertEqual(normalize_meeting_kind("kitchen"), "KITCHEN")
        self.assertEqual(normalize_meeting_kind("Manager"), "MANAGER")

    def test_detect_from_title(self):
        self.assertEqual(meeting_kind_from_text("Front of House meeting tomorrow"), "FOH")
        self.assertEqual(meeting_kind_from_text("Kitchen standup"), "KITCHEN")
        self.assertEqual(meeting_kind_from_text("Manager meeting — weekly"), "MANAGER")
        self.assertEqual(meeting_kind_from_text("misc", "meeting_kind:FOH"), "FOH")

    def test_title_with_kind(self):
        self.assertEqual(title_with_meeting_kind("Weekly sync", "FOH"), "FOH meeting — Weekly sync")
        self.assertTrue(title_with_meeting_kind("", "KITCHEN").startswith("Kitchen"))


class CanonicalRegistrationTests(SimpleTestCase):
    def test_tools_registered(self):
        for name in (
            "confirm_meeting",
            "list_meetings",
            "list_calendar_events",
            "create_calendar_event",
            "update_calendar_event",
            "delete_calendar_event",
            "create_personal_reminder",
            "list_reminders",
            "cancel_reminder",
            "sync_compliance_reminder",
        ):
            self.assertIn(name, CANONICAL_TOOL_NAMES, name)


class PersonalReminderOpsTests(SimpleTestCase):
    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("scheduling.memory_models.PersonalReminder")
    def test_create_task_reminder(self, model, _rr):
        from miya.services.ops.meetings import create_personal_reminder

        due = timezone.now() + timedelta(days=1)
        created = _reminder(title="Follow up prep list", due=due, body="reminder_kind:task")
        model.objects.create.return_value = created
        model.objects.filter.return_value.first.return_value = created

        result = create_personal_reminder(
            _ctx(),
            title="Follow up prep list",
            due_at=due.isoformat(),
            reminder_kind="task",
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertIn("dashboard", result.data["surfaces"])
        self.assertIn("whatsapp", result.data["surfaces"])
        self.assertIn("miya", result.data["surfaces"])

    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("scheduling.memory_models.PersonalReminder")
    def test_create_daily_reminder(self, model, _rr):
        from miya.services.ops.meetings import create_personal_reminder

        due = timezone.now() + timedelta(hours=5)
        created = _reminder(title="Open checklist", due=due, recurrence="daily")
        model.objects.create.return_value = created
        model.objects.filter.return_value.first.return_value = created

        result = create_personal_reminder(
            _ctx(),
            title="Open checklist",
            due_at=due.isoformat(),
            reminder_kind="daily",
        )
        self.assertTrue(result.success)
        kwargs = model.objects.create.call_args.kwargs
        self.assertEqual(kwargs.get("recurrence"), "daily")

    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("scheduling.memory_models.PersonalReminder")
    def test_cancel_reminder_clarify_multi(self, model, _rr):
        from miya.services.ops.meetings import cancel_reminder

        a = _reminder(title="Insurance check")
        a.id = "a"
        b = _reminder(title="Insurance renewal")
        b.id = "b"
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.order_by.return_value = [a, b]
        model.objects.filter.return_value = qs

        result = cancel_reminder(_ctx(), q="Insurance")
        self.assertFalse(result.success)
        self.assertTrue(result.needs_clarification)


class ListMeetingsParityTests(SimpleTestCase):
    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("dashboard.api.meetings_reminders._get_valid_access_token", return_value=(None, {}))
    @patch("scheduling.memory_models.PersonalReminder")
    def test_list_includes_personal_when_calendar_disconnected(self, model, _tok, _rr):
        from miya.services.ops.meetings import list_meetings

        rem = _reminder(title="Task reminder: stock count")

        class _QS(list):
            def filter(self, *a, **k):
                return self

            def order_by(self, *a, **k):
                return self

        model.objects.filter.return_value = _QS([rem])

        result = list_meetings(_ctx(), q="stock")
        self.assertTrue(result.success, result.message_for_user)
        self.assertEqual(result.data["count"], 1)
        self.assertEqual(result.data["reminders"][0]["title"], "Task reminder: stock count")
        self.assertFalse(result.data["calendar_connected"])

    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("dashboard.api.calendar_write._fetch_calendar_events_for_agent")
    @patch("dashboard.api.meetings_reminders._get_valid_access_token")
    @patch("scheduling.memory_models.PersonalReminder")
    def test_list_filters_department_meetings(self, model, tok, fetch, _rr):
        from miya.services.ops.meetings import list_meetings

        tok.return_value = ("token", {"connected": True})
        fetch.return_value = [
            {
                "id": "ev-foh",
                "title": "FOH meeting — service brief",
                "start": (timezone.now() + timedelta(hours=3)).isoformat(),
                "description": "meeting_kind:FOH",
                "location": "",
            },
            {
                "id": "ev-kit",
                "title": "Kitchen meeting — prep",
                "start": (timezone.now() + timedelta(hours=4)).isoformat(),
                "description": "meeting_kind:KITCHEN",
                "location": "",
            },
            {
                "id": "ev-mgr",
                "title": "Manager meeting — ops",
                "start": (timezone.now() + timedelta(hours=5)).isoformat(),
                "description": "meeting_kind:MANAGER",
                "location": "",
            },
        ]
        qs = MagicMock()
        qs.filter.return_value = qs
        ordered = MagicMock()
        ordered.__getitem__ = lambda self, s: []
        qs.order_by.return_value = ordered
        model.objects.filter.return_value = qs

        foh = list_meetings(_ctx(), meeting_kind="FOH")
        self.assertTrue(foh.success)
        self.assertEqual(foh.data["count"], 1)
        self.assertEqual(foh.data["events"][0]["meeting_kind"], "FOH")

        kit = list_meetings(_ctx(), meeting_kind="KITCHEN")
        self.assertTrue(kit.success)
        self.assertEqual(kit.data["events"][0]["meeting_kind"], "KITCHEN")

        mgr = list_meetings(_ctx(), meeting_kind="MANAGER")
        self.assertTrue(mgr.success)
        self.assertEqual(mgr.data["events"][0]["meeting_kind"], "MANAGER")


class CalendarCreateVerifyTests(SimpleTestCase):
    @patch("miya.services.ops.meetings.require_permission", return_value=None)
    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("scheduling.memory_models.PersonalReminder")
    @patch("dashboard.api.calendar_write._create_single_calendar_event")
    def test_create_foh_meeting_verifies_reminder_sync(self, create, model, *_):
        from miya.services.ops.meetings import create_calendar_event

        create.return_value = {
            "success": True,
            "event_id": "gcal-foh-1",
            "message_for_user": 'Created meeting "FOH meeting — brief".',
            "calendar_event": {"id": "gcal-foh-1", "summary": "FOH meeting — brief"},
            "html_link": "https://calendar.google.com/event?eid=1",
        }
        rem = _reminder(
            title="FOH meeting — brief",
            body="gcal_event_id:gcal-foh-1\nmeeting_kind:FOH",
        )
        filt = MagicMock()
        filt.order_by.return_value.first.return_value = rem
        model.objects.filter.return_value = filt

        result = create_calendar_event(
            _ctx(),
            title="brief",
            start=(timezone.now() + timedelta(days=1)).isoformat(),
            meeting_kind="FOH",
        )
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual(result.data["meeting_kind"], "FOH")
        self.assertEqual(
            set(result.data["surfaces"]),
            {"google_calendar", "dashboard", "whatsapp", "miya"},
        )
        # Underlying create received department kind
        payload = create.call_args.args[1]
        self.assertEqual(payload.get("meeting_kind"), "FOH")

    @patch("miya.services.ops.meetings.require_permission", return_value=None)
    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("dashboard.api.calendar_write._create_single_calendar_event")
    def test_batch_multiple_meetings(self, create, *_):
        from miya.services.ops.meetings import create_calendar_event

        create.side_effect = [
            {
                "success": True,
                "event_id": f"e{i}",
                "message_for_user": f"ok {i}",
                "calendar_event": {},
            }
            for i in range(3)
        ]

        with patch("scheduling.memory_models.PersonalReminder") as model:
            rem = _reminder()
            filt = MagicMock()
            filt.order_by.return_value.first.return_value = rem
            model.objects.filter.return_value = filt
            start = (timezone.now() + timedelta(days=1)).isoformat()
            result = create_calendar_event(
                _ctx(),
                events=[
                    {"title": "FOH meeting", "start": start, "meeting_kind": "FOH"},
                    {"title": "Kitchen meeting", "start": start, "meeting_kind": "KITCHEN"},
                    {"title": "Manager meeting", "start": start, "meeting_kind": "MANAGER"},
                ],
            )
        self.assertTrue(result.success)
        self.assertEqual(result.data["created_count"], 3)


class DeleteSyncTests(SimpleTestCase):
    @patch("scheduling.memory_models.PersonalReminder")
    def test_cancel_calendar_event_reminder(self, model):
        qs = MagicMock()
        qs.filter.return_value = qs
        qs.update.return_value = 2
        model.objects.filter.return_value = qs
        out = cancel_calendar_event_reminder(restaurant=MagicMock(), event_id="abc")
        self.assertEqual(out["cancelled"], 2)

    @patch("miya.services.ops.meetings.require_permission", return_value=None)
    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("scheduling.memory_models.PersonalReminder")
    @patch("dashboard.api.calendar_write._delete_single_calendar_event")
    def test_delete_verifies_reminder_cancelled(self, delete, model, *_):
        from miya.services.ops.meetings import delete_calendar_event

        delete.return_value = {
            "success": True,
            "event_id": "gone-1",
            "deleted_title": "Kitchen meeting",
            "message_for_user": "Removed.",
        }
        model.objects.filter.return_value.count.return_value = 0
        result = delete_calendar_event(_ctx(), event_id="gone-1")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)


class ComplianceReminderOpsTests(SimpleTestCase):
    @patch("miya.services.ops.meetings.require_permission", return_value=None)
    @patch("miya.services.ops.meetings.require_restaurant", return_value=None)
    @patch("payroll.services.compliance_reminder_sync.sync_compliance_document_reminder")
    @patch("scheduling.memory_models.PersonalReminder")
    @patch("payroll.models.ComplianceDocument")
    def test_sync_insurance_expiry(self, doc_model, rem_model, sync, *_):
        from miya.services.ops.meetings import sync_compliance_reminder

        doc = MagicMock()
        doc.id = "doc-ins"
        doc.title = "Business Insurance"
        doc.expires_at = timezone.now().date() + timedelta(days=20)
        doc_model.objects.filter.return_value.first.return_value = doc
        sync.return_value = {"created": 1, "updated": 0, "cancelled": 0, "skipped": 0}
        rem = _reminder(title="Business Insurance Expiration Reminder")
        rem.linked_compliance_document_id = "doc-ins"
        rem_model.objects.filter.return_value.first.return_value = rem

        result = sync_compliance_reminder(_ctx(), document_id="doc-ins")
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertIn("whatsapp", result.data["surfaces"])


class DispatchSmokeTests(SimpleTestCase):
    def test_dispatch_list_meetings(self):
        with patch("miya.services.ops.meetings.list_meetings") as fn:
            from miya.services.ops.result import ok

            fn.return_value = ok(message="ok", verified=True, data={"items": [], "count": 0})
            result = dispatch_canonical_tool("list_meetings", {"q": "FOH"}, ctx=_ctx())
            self.assertTrue(result.success)
            fn.assert_called_once()

    def test_persona_mentions_department_meetings(self):
        from miya.persona import MIYA_SUPER_AGENT_PERSONA

        self.assertIn("meeting_kind", MIYA_SUPER_AGENT_PERSONA)
        self.assertIn("FOH", MIYA_SUPER_AGENT_PERSONA)
        self.assertIn("list_meetings", MIYA_SUPER_AGENT_PERSONA)
        self.assertIn("sync_compliance_reminder", MIYA_SUPER_AGENT_PERSONA)
