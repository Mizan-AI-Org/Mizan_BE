"""Phase 5: incident photo lifecycle — attach, route, notify, Miya retrieve/show."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.ops.context import OpsContext
from miya.services.ops import CANONICAL_TOOL_NAMES, dispatch_canonical_tool
from staff.incident_evidence import (
    incident_has_photo_evidence,
    list_incident_photos,
)


def _ctx(*, channel="dashboard", role="MANAGER"):
    rest = MagicMock()
    rest.id = "rest-1"
    user = MagicMock()
    user.id = "mgr-1"
    user.pk = "mgr-1"
    user.role = role
    user.phone = "212600000001"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="rest-1",
        user_id="mgr-1",
        role=role,
        channel=channel,
    )


def _ticket(*, title="Refrigerator leaking", description="Fridge leaking water near kitchen"):
    t = MagicMock()
    t.id = "inc-fridge-001"
    t.title = title
    t.description = description
    t.status = "OPEN"
    t.incident_type = "Safety"
    t.severity = "MEDIUM"
    t.category = "Safety"
    t.priority = "MEDIUM"
    t.location = "Kitchen"
    t.created_at = MagicMock()
    t.created_at.isoformat.return_value = "2026-08-07T12:00:00+00:00"
    t.resolved_at = None
    t.resolution_notes = ""
    t.assigned_to = MagicMock()
    t.assigned_to.id = "owner-1"
    t.assigned_to.first_name = "Sara"
    t.assigned_to.last_name = "Owner"
    t.assigned_to.email = "sara@ex.com"
    t.assigned_to.role = "MANAGER"
    t.assigned_to.phone = "212611111111"
    t.photo = MagicMock()
    t.photo.name = "incidents/fridge.jpg"
    t.photo_evidence = [
        {
            "url": "incidents/fridge.jpg",
            "storage_key": "incidents/fridge.jpg",
            "mime_type": "image/jpeg",
            "filename": "fridge.jpg",
            "caption": "leak",
        }
    ]
    t.restaurant = MagicMock()
    t.restaurant.id = "rest-1"
    t.restaurant_id = "rest-1"
    return t


class IncidentPhotoEvidenceHelpersTests(SimpleTestCase):
    def test_has_photo_and_list_urls(self):
        ticket = _ticket()
        self.assertTrue(incident_has_photo_evidence(ticket))
        with patch(
            "staff.incident_evidence._resolve_public_url",
            side_effect=lambda raw: f"https://cdn.example/{raw.lstrip('/')}",
        ), patch(
            "core.s3_storage.file_field_download_url",
            return_value="https://cdn.example/incidents/fridge.jpg",
        ):
            photos = list_incident_photos(ticket)
        self.assertGreaterEqual(len(photos), 1)
        self.assertTrue(photos[0]["url"].startswith("https://"))


class AttachIncidentPhotoTests(SimpleTestCase):
    def test_attach_verifies_and_notifies(self):
        from miya.services.ops.incidents import attach_incident_photo_bytes

        ctx = _ctx(channel="whatsapp")
        ticket = _ticket()
        ticket.photo = None
        ticket.photo_evidence = []

        def _append(t, **kwargs):
            t.photo_evidence = [
                {
                    "url": "incidents/x.jpg",
                    "storage_key": "incidents/x.jpg",
                    "mime_type": "image/jpeg",
                    "filename": "x.jpg",
                }
            ]
            t.photo = MagicMock()
            t.photo.name = "incidents/x.jpg"

        with patch(
            "miya.services.ops.incidents.require_restaurant", return_value=None
        ), patch(
            "staff.models_task.SafetyConcernReport.objects"
        ) as mock_objs, patch(
            "staff.incident_evidence.append_incident_photo_evidence",
            side_effect=_append,
        ), patch(
            "staff.incident_evidence.incident_has_photo_evidence",
            return_value=True,
        ), patch(
            "staff.incident_evidence.notify_owners_photo_attached"
        ) as mock_notify, patch(
            "staff.incident_evidence.list_incident_photos",
            return_value=[
                {
                    "url": "https://cdn.example/x.jpg",
                    "storage_key": "incidents/x.jpg",
                    "filename": "x.jpg",
                    "mime_type": "image/jpeg",
                }
            ],
        ):
            mock_objs.filter.return_value.first.return_value = ticket
            result = attach_incident_photo_bytes(
                ctx,
                incident_id="inc-fridge-001",
                file_bytes=b"\xff\xd8fakejpeg",
                mime_type="image/jpeg",
                filename="x.jpg",
            )

        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertTrue(result.data.get("has_photo"))
        mock_notify.assert_called_once()


class GetIncidentPhotoTests(SimpleTestCase):
    def test_dashboard_returns_secure_refs(self):
        from miya.services.ops.incidents import get_incident_photo

        ctx = _ctx(channel="dashboard")
        ticket = _ticket()
        photos = [
            {
                "url": "https://cdn.example/incidents/fridge.jpg?X-Amz-Signature=abc",
                "storage_key": "incidents/fridge.jpg",
                "filename": "fridge.jpg",
                "mime_type": "image/jpeg",
            }
        ]
        with patch(
            "miya.services.ops.incidents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.incidents.require_permission", return_value=None
        ), patch(
            "staff.models_task.SafetyConcernReport.objects"
        ) as mock_objs, patch(
            "staff.incident_evidence.list_incident_photos", return_value=photos
        ):
            mock_objs.filter.return_value.first.return_value = ticket
            result = get_incident_photo(ctx, incident_id="inc-fridge-001")

        self.assertTrue(result.success)
        self.assertTrue(result.data.get("has_photo"))
        self.assertFalse(result.data.get("whatsapp_image_sent"))
        refs = result.data.get("secure_photo_refs") or []
        self.assertEqual(refs[0]["storage_key"], "incidents/fridge.jpg")
        self.assertIn("photo", result.message_for_user.lower())

    def test_whatsapp_sends_image_bytes(self):
        from miya.services.ops.incidents import get_incident_photo

        ctx = _ctx(channel="whatsapp")
        ticket = _ticket()
        photos = [
            {
                "url": "https://cdn.example/fridge.jpg",
                "storage_key": "incidents/fridge.jpg",
                "filename": "fridge.jpg",
                "mime_type": "image/jpeg",
            }
        ]
        with patch(
            "miya.services.ops.incidents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.incidents.require_permission", return_value=None
        ), patch(
            "staff.models_task.SafetyConcernReport.objects"
        ) as mock_objs, patch(
            "staff.incident_evidence.list_incident_photos", return_value=photos
        ), patch(
            "staff.incident_evidence.load_incident_photo_bytes",
            return_value=(b"\xff\xd8img", "image/jpeg", "fridge.jpg"),
        ), patch(
            "notifications.services.notification_service.send_whatsapp_media_attachment",
            return_value=(True, {"external_id": "wamid.1"}),
        ) as mock_send:
            mock_objs.filter.return_value.first.return_value = ticket
            result = get_incident_photo(
                ctx, incident_id="inc-fridge-001", phone="212600000001"
            )

        self.assertTrue(result.success)
        self.assertTrue(result.data.get("whatsapp_image_sent"))
        mock_send.assert_called_once()
        kwargs = mock_send.call_args.kwargs
        self.assertEqual(kwargs.get("file_bytes"), b"\xff\xd8img")
        self.assertTrue((kwargs.get("mime_type") or "").startswith("image/"))


class ResolveAndSerializeTests(SimpleTestCase):
    def test_serialize_includes_has_photo(self):
        from miya.services.ops.incidents import _serialize_concern

        ticket = _ticket()
        with patch(
            "staff.incident_evidence.incident_has_photo_evidence", return_value=True
        ), patch(
            "staff.incident_evidence.list_incident_photos",
            return_value=[{"url": "https://x/a.jpg", "storage_key": "a.jpg", "filename": "a.jpg"}],
        ):
            row = _serialize_concern(ticket, detail=True)
        self.assertTrue(row["has_photo"])
        self.assertGreaterEqual(row["photo_count"], 1)
        self.assertTrue(row["photo_urls"])

    def test_resolve_incident_closes(self):
        from miya.services.ops.incidents import resolve_incident

        ctx = _ctx()
        ticket = _ticket()

        def _save(*, update_fields=None):
            ticket.status = "RESOLVED"

        ticket.save = MagicMock(side_effect=_save)

        with patch(
            "miya.services.ops.incidents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.incidents.require_permission", return_value=None
        ), patch(
            "staff.models_task.SafetyConcernReport.objects"
        ) as mock_objs, patch(
            "staff.views_agent._invalidate_staff_incidents_cache"
        ):
            mock_objs.filter.return_value.first.side_effect = [ticket, ticket]
            result = resolve_incident(ctx, incident_id="inc-fridge-001", resolution_notes="Fixed")

        self.assertTrue(result.success)
        self.assertEqual(ticket.status, "RESOLVED")


class CanonicalDispatchPhase5Tests(SimpleTestCase):
    def test_tools_registered(self):
        for name in (
            "get_incident",
            "get_incident_photo",
            "close_incident",
            "resolve_incident",
            "report_incident",
            "route_incident",
        ):
            self.assertIn(name, CANONICAL_TOOL_NAMES)

    def test_dispatch_get_incident_photo(self):
        ctx = _ctx(channel="dashboard")
        with patch(
            "miya.services.ops.incidents.get_incident_photo"
        ) as mock_fn:
            from miya.services.ops.result import ok

            mock_fn.return_value = ok(
                message="photo",
                verified=True,
                data={"has_photo": True, "secure_photo_refs": [{"storage_key": "k"}]},
            )
            result = dispatch_canonical_tool(
                "get_incident_photo",
                {"q": "refrigerator"},
                ctx=ctx,
            )
        self.assertTrue(result.success)
        mock_fn.assert_called_once()
        self.assertEqual(mock_fn.call_args.kwargs.get("q"), "refrigerator")

    def test_miya_can_answer_refrigerator_photo_flow(self):
        """list → get_incident_photo path for 'Show me the photo on the refrigerator incident'."""
        from miya.services.ops.incidents import find_incidents, get_incident_photo

        ctx = _ctx(channel="whatsapp")
        ticket = _ticket()
        list_row = {
            "id": str(ticket.id),
            "title": ticket.title,
            "status": "OPEN",
            "has_photo": True,
            "photo_count": 1,
        }
        with patch(
            "miya.services.ops.incidents.require_restaurant", return_value=None
        ), patch(
            "miya.services.ops.incidents.require_permission", return_value=None
        ), patch(
            "staff.models_task.SafetyConcernReport.objects"
        ) as mock_objs:
            qs = MagicMock()
            qs.filter.return_value = qs
            qs.select_related.return_value = qs
            qs.order_by.return_value = [ticket]
            mock_objs.filter.return_value = qs

            with patch(
                "miya.services.ops.incidents._serialize_concern", return_value=list_row
            ):
                listed = find_incidents(ctx, q="refrigerator", limit=5)

            self.assertTrue(listed.success)
            self.assertTrue(listed.data["incidents"][0]["has_photo"])

            photos = [
                {
                    "url": "https://cdn.example/fridge.jpg",
                    "storage_key": "incidents/fridge.jpg",
                    "filename": "fridge.jpg",
                    "mime_type": "image/jpeg",
                }
            ]
            mock_objs.filter.return_value.first.return_value = ticket
            with patch(
                "staff.incident_evidence.list_incident_photos", return_value=photos
            ), patch(
                "staff.incident_evidence.load_incident_photo_bytes",
                return_value=(b"img", "image/jpeg", "fridge.jpg"),
            ), patch(
                "notifications.services.notification_service.send_whatsapp_media_attachment",
                return_value=(True, {}),
            ):
                photo = get_incident_photo(
                    ctx, incident_id=str(ticket.id), phone="212600000001"
                )

        self.assertTrue(photo.success)
        self.assertTrue(photo.data.get("whatsapp_image_sent"))
        self.assertTrue(photo.data.get("has_photo"))
