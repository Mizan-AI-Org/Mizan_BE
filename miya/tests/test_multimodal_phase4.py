"""Phase 4 — Multimodal Miya (voice / image / PDF through the same engine)."""
from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase

from miya.services.intelligence.multimodal import (
    KIND_COMPLIANCE,
    KIND_EQUIPMENT,
    KIND_INVOICE,
    MultimodalContext,
    build_multimodal_context,
)
from miya.services.intelligence.planning.classify import classify_message
from miya.services.intelligence.planning.types import (
    EntityType,
    IntentClass,
    PlanAction,
)
from miya.services.ops.context import OpsContext
from miya.services.ops.result import ok


def _ctx(*, location_id="loc-a"):
    user = MagicMock()
    user.id = "u1"
    user.pk = "u1"
    user.role = "MANAGER"
    rest = MagicMock()
    rest.id = "r1"
    return OpsContext(
        user=user,
        restaurant=rest,
        restaurant_id="r1",
        user_id="u1",
        role="MANAGER",
        channel="dashboard",
        language="en",
        location_id=location_id,
        location_name="Branch A",
        available_locations=[{"id": "loc-a", "name": "Branch A"}],
    )


def _mm_attachment(**kwargs):
    base = {
        "document_id": "doc-1",
        "title": "upload",
        "category": "",
        "mime_type": "image/jpeg",
        "kind": KIND_EQUIPMENT,
        "summary": "",
        "vendor": "",
        "amount": "",
        "currency": "",
        "invoice_number": "",
        "expiry_date": "",
        "invoice_id": "",
        "compliance_document_id": "",
        "structured": {},
        "tags": [],
        "has_file": True,
    }
    base.update(kwargs)
    return {
        "modalities": ["text", "image", "photo"],
        "attachments": [base],
        "primary_kind": base["kind"],
        "suggested_intent": kwargs.get("suggested_intent", "CREATE"),
        "suggested_entity": kwargs.get("suggested_entity", "incident"),
        "caption": kwargs.get("caption", "Report this."),
        "reasoning_hint": "OCR is evidence only",
        "ocr_is_not_final_intelligence": True,
    }


class MultimodalContextTests(SimpleTestCase):
    def test_ocr_is_not_final_intelligence_flag(self):
        ctx = MultimodalContext(modalities=["text"], reasoning_hint="hint")
        d = ctx.to_dict()
        self.assertTrue(d["ocr_is_not_final_intelligence"])

    @patch("miya.services.tenant_documents.documents_for_ids")
    @patch("miya.services.tenant_documents.serialize_tenant_document")
    def test_invoice_kind_from_category(self, ser, docs):
        docs.return_value = [MagicMock()]
        ser.return_value = {
            "id": "d1",
            "title": "Supplier invoice",
            "category": "invoice",
            "mime_type": "image/jpeg",
            "summary": "Invoice from Acme",
            "vendor": "Acme",
            "amount": "1200",
            "currency": "MAD",
            "invoice_number": "INV-9",
            "structured": {"vendor": "Acme", "amount": "1200"},
            "tags": [],
            "has_file": True,
        }
        mm = build_multimodal_context(
            user_message="File this",
            attachment_ids=["d1"],
            restaurant_id="r1",
        )
        self.assertEqual(mm.primary_kind, KIND_INVOICE)
        self.assertEqual(mm.suggested_intent, "CREATE")
        self.assertEqual(mm.suggested_entity, "invoice")


class PhotoToIncidentTests(SimpleTestCase):
    def test_report_this_with_photo_classifies_create_incident(self):
        mm = _mm_attachment(
            kind=KIND_EQUIPMENT,
            summary="Broken freezer door",
            suggested_intent="CREATE",
            suggested_entity="incident",
        )
        c = classify_message("Report this.", multimodal=mm)
        self.assertEqual(c.intent, IntentClass.CREATE)
        self.assertEqual(c.entity_type, EntityType.INCIDENT)
        self.assertEqual(c.slots.get("document_id"), "doc-1")
        self.assertIn("multimodal_attachment", c.reasons)

    def test_incident_from_media_workflow_create_attach_route(self):
        from miya.services.intelligence.planning.multimodal_workflows import (
            run_incident_from_media,
        )
        from miya.services.intelligence.planning.types import ExecutionPlan

        plan = ExecutionPlan(
            workflow="incident_from_media",
            action=PlanAction.EXECUTE,
            intent=classify_message(
                "Report this.",
                multimodal=_mm_attachment(summary="Broken freezer"),
            ),
            tool_args={
                "document_id": "doc-1",
                "description": "Report this.",
                "summary": "Broken freezer",
                "structured": {"summary": "Broken freezer"},
            },
        )
        create_res = ok(
            message="Incident logged (abc12345).",
            verified=True,
            data={"incident": {"id": "inc-1", "incident_type": "Maintenance"}},
        )
        attach_res = ok(message="Photo attached.", verified=True, data={"has_photo": True})
        route_res = ok(message="Routed to Ahmed.", verified=True)

        with patch(
            "miya.services.intelligence.planning.multimodal_workflows.execute_structured_action",
            side_effect=[create_res, attach_res, route_res],
        ) as exec_mock:
            result = run_incident_from_media(_ctx(), plan)
        self.assertTrue(result.success)
        self.assertTrue(result.verified)
        self.assertEqual(exec_mock.call_count, 3)
        self.assertEqual(exec_mock.call_args_list[0][0][0], "create_incident")
        self.assertEqual(exec_mock.call_args_list[1][0][0], "attach_incident_photo")
        self.assertEqual(exec_mock.call_args_list[2][0][0], "assign_incident")
        self.assertIn("VERIFY", result.stages_completed)


class InvoiceFromImageTests(SimpleTestCase):
    def test_invoice_image_classifies_create_invoice(self):
        mm = _mm_attachment(
            kind=KIND_INVOICE,
            category="invoice",
            vendor="Acme Foods",
            amount="450.00",
            suggested_intent="CREATE",
            suggested_entity="invoice",
            caption="",
        )
        c = classify_message("", multimodal=mm)
        self.assertEqual(c.intent, IntentClass.CREATE)
        self.assertEqual(c.entity_type, EntityType.INVOICE)
        self.assertEqual(c.slots.get("vendor"), "Acme Foods")

    def test_invoice_from_media_records_with_ocr_fields(self):
        from miya.services.intelligence.planning.multimodal_workflows import (
            run_invoice_from_media,
        )
        from miya.services.intelligence.planning.types import ExecutionPlan

        intent = classify_message(
            "Record this invoice",
            multimodal=_mm_attachment(
                kind=KIND_INVOICE,
                vendor="Acme",
                amount="100",
                suggested_intent="CREATE",
                suggested_entity="invoice",
            ),
        )
        plan = ExecutionPlan(
            workflow="invoice_from_media",
            action=PlanAction.EXECUTE,
            intent=intent,
            tool_args={
                "document_id": "doc-1",
                "vendor": "Acme",
                "amount": "100",
                "structured": {"vendor": "Acme", "amount": "100"},
            },
        )
        with patch(
            "miya.services.intelligence.planning.multimodal_workflows.execute_structured_action",
            return_value=ok(
                message="Logged MAD 100 invoice from Acme.",
                verified=True,
                data={"invoice": {"id": "inv-1"}, "created": True},
            ),
        ) as exec_mock:
            result = run_invoice_from_media(_ctx(), plan)
        self.assertTrue(result.verified)
        self.assertEqual(exec_mock.call_args[0][0], "record_invoice")
        args = exec_mock.call_args[0][1]
        self.assertEqual(args["vendor"], "Acme")
        self.assertEqual(args["document_id"], "doc-1")


class InsurancePdfReminderTests(SimpleTestCase):
    def test_insurance_pdf_suggests_remind(self):
        mm = _mm_attachment(
            kind=KIND_COMPLIANCE,
            title="Restaurant insurance 2026",
            category="insurance",
            mime_type="application/pdf",
            expiry_date="2026-12-31",
            compliance_document_id="comp-1",
            suggested_intent="REMIND",
            suggested_entity="reminder",
            caption="",
        )
        mm["modalities"] = ["pdf", "document"]
        c = classify_message("", multimodal=mm)
        self.assertEqual(c.intent, IntentClass.REMIND)
        self.assertEqual(c.entity_type, EntityType.REMINDER)
        self.assertEqual(c.slots.get("compliance_document_id"), "comp-1")

    def test_compliance_reminder_workflow(self):
        from miya.services.intelligence.planning.multimodal_workflows import (
            run_compliance_reminder_from_media,
        )
        from miya.services.intelligence.planning.types import ExecutionPlan

        intent = classify_message(
            "Set a reminder for this insurance",
            multimodal=_mm_attachment(
                kind=KIND_COMPLIANCE,
                compliance_document_id="comp-1",
                title="Insurance",
                suggested_intent="REMIND",
                suggested_entity="reminder",
            ),
        )
        plan = ExecutionPlan(
            workflow="compliance_reminder_from_media",
            action=PlanAction.EXECUTE,
            intent=intent,
            tool_args={
                "compliance_document_id": "comp-1",
                "title": "Insurance",
                "q": "insurance",
            },
        )
        with patch(
            "miya.services.intelligence.planning.multimodal_workflows.execute_structured_action",
            return_value=ok(
                message="Expiry reminder set for *Insurance*.",
                verified=True,
                data={"document_id": "comp-1"},
            ),
        ) as exec_mock:
            result = run_compliance_reminder_from_media(_ctx(), plan)
        self.assertTrue(result.verified)
        self.assertEqual(exec_mock.call_args[0][0], "sync_compliance_reminder")


class VoiceSameEngineTests(SimpleTestCase):
    """Voice transcript must use the same planning engine as text (no separate brain)."""

    def test_voice_task_completion_via_planning_engine(self):
        from miya.services.intelligence.planning.engine import try_planning_engine

        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "r1"
        user.restaurant = rest

        complete_res = ok(
            message="Done — decoration marked completed.",
            verified=True,
            data={"task": {"id": "t1", "status": "COMPLETED"}},
        )
        with (
            patch(
                "miya.services.intelligence.planning.engine.build_ops_context",
                return_value=_ctx(),
            ),
            patch(
                "miya.services.ops.tasks.get_task_state",
                return_value=ok(
                    message="Found",
                    verified=True,
                    data={"task": {"id": "t1", "title": "decoration"}},
                ),
            ),
            patch(
                "miya.services.intelligence.planning.workflows.execute_structured_action",
                return_value=complete_res,
            ),
        ):
            # Same path as Fish Audio STT → run_miya_chat → try_planning_engine
            result = try_planning_engine(
                user_message="Close the decoration task.",
                user=user,
                session_context={
                    "restaurant_id": "r1",
                    "location_id": "loc-a",
                    "voice": True,
                    "_multimodal": {
                        "modalities": ["voice", "text"],
                        "attachments": [],
                        "primary_kind": "unknown",
                    },
                },
                restaurant=rest,
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.get("presentation_only"))
        self.assertIn("Done", result.get("reply") or "")

    def test_voice_staff_lookup_via_planning_engine(self):
        from miya.services.intelligence.planning.engine import try_planning_engine

        user = MagicMock()
        user.id = "u1"
        user.role = "MANAGER"
        rest = MagicMock()
        rest.id = "r1"
        user.restaurant = rest

        staff_res = ok(
            message="Found Ahmed (Manager).",
            verified=True,
            data={"staff": [{"id": "s1", "name": "Ahmed", "role": "MANAGER"}]},
        )
        with (
            patch(
                "miya.services.intelligence.planning.engine.build_ops_context",
                return_value=_ctx(),
            ),
            patch(
                "miya.services.intelligence.planning.multimodal_workflows.execute_structured_action",
                return_value=staff_res,
            ),
        ):
            result = try_planning_engine(
                user_message="Who is Ahmed?",
                user=user,
                session_context={
                    "restaurant_id": "r1",
                    "location_id": "loc-a",
                    "voice": True,
                },
                restaurant=rest,
                multimodal={"modalities": ["voice", "text"], "attachments": []},
            )
        self.assertIsNotNone(result)
        self.assertIn("Ahmed", result.get("reply") or "")


class ImageIncidentRetrievalTests(SimpleTestCase):
    def test_find_incident_with_photo_classifies_retrieve(self):
        mm = _mm_attachment(
            kind=KIND_EQUIPMENT,
            summary="Broken freezer in kitchen",
            suggested_intent="CREATE",
            suggested_entity="incident",
            caption="Find the incident for this photo",
        )
        c = classify_message("Find the incident for this photo", multimodal=mm)
        self.assertEqual(c.intent, IntentClass.RETRIEVE)
        self.assertEqual(c.entity_type, EntityType.INCIDENT)

    def test_incident_lookup_workflow(self):
        from miya.services.intelligence.planning.multimodal_workflows import (
            run_incident_lookup,
        )
        from miya.services.intelligence.planning.types import ExecutionPlan

        intent = classify_message(
            "Show incident for this freezer photo",
            multimodal=_mm_attachment(summary="freezer leak"),
        )
        plan = ExecutionPlan(
            workflow="incident_lookup",
            action=PlanAction.EXECUTE,
            intent=intent,
            tool_args={"q": "freezer", "structured": {"summary": "freezer leak"}},
        )
        with patch(
            "miya.services.intelligence.planning.multimodal_workflows.execute_structured_action",
            return_value=ok(
                message="Open freezer incident.",
                verified=True,
                data={"incident": {"id": "inc-9"}},
            ),
        ) as exec_mock:
            result = run_incident_lookup(_ctx(), plan)
        self.assertTrue(result.success)
        self.assertEqual(exec_mock.call_args[0][0], "get_current_incident")


class PlanningEngineMultimodalGateTests(SimpleTestCase):
    def test_engine_routes_photo_report_to_incident_from_media(self):
        from miya.services.intelligence.planning.engine import try_planning_engine
        from miya.services.intelligence.planning.resolve import resolve_plan

        mm = _mm_attachment(summary="Broken freezer")
        classified = classify_message("Report this.", multimodal=mm)
        plan = resolve_plan(classified, ctx=_ctx(), session_context={"_multimodal": mm})
        self.assertEqual(plan.workflow, "incident_from_media")
        self.assertEqual(plan.action, PlanAction.EXECUTE)

        user = MagicMock()
        user.restaurant = MagicMock(id="r1")
        with (
            patch(
                "miya.services.intelligence.planning.engine.build_ops_context",
                return_value=_ctx(),
            ),
            patch(
                "miya.services.intelligence.planning.multimodal_workflows.execute_structured_action",
                side_effect=[
                    ok(
                        message="Incident logged.",
                        verified=True,
                        data={"incident": {"id": "inc-1"}},
                    ),
                    ok(message="Photo attached.", verified=True),
                    ok(message="Routed.", verified=True),
                ],
            ),
        ):
            result = try_planning_engine(
                user_message="Report this.",
                user=user,
                session_context={"restaurant_id": "r1", "location_id": "loc-a"},
                restaurant=user.restaurant,
                multimodal=mm,
            )
        self.assertIsNotNone(result)
        self.assertTrue(result.get("multimodal", {}).get("ocr_is_not_final_intelligence"))
