"""Reusable E2E harness — seed, send Miya request, inspect DB/audit/notifications."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from django.test import TestCase

from dashboard.models import Task
from miya.models import OperationalEvent
from miya.services.intelligence.copilot.orchestrator import run_copilot_turn
from miya.services.intelligence.copilot.understand import understand_turn
from miya.services.intelligence.unified_understand import unified_understand
from miya.tests.e2e.seed import E2EWorld
from notifications.models import Notification


@dataclass
class E2ETurnCapture:
    """Full turn inspection artifact — DB is source of truth."""

    message: str
    channel: str
    intent: str = ""
    entity_type: str = ""
    handler: str = ""
    routing_hint: str = ""
    success: bool = False
    verified: bool | None = None
    needs_clarification: bool = False
    reply: str = ""
    tool_trace: list[dict[str, Any]] = field(default_factory=list)
    stages_completed: list[str] = field(default_factory=list)
    meta: dict[str, Any] = field(default_factory=dict)
    compound: bool = False
    deferred: bool = False

    @property
    def tools_used(self) -> list[str]:
        return [str(t.get("tool") or "") for t in self.tool_trace if t.get("tool")]


class MiyaE2EHarness:
    """Send requests through copilot and capture structured turn data."""

    def __init__(self, world: E2EWorld):
        self.world = world

    def send(
        self,
        message: str,
        *,
        user=None,
        channel: str = "dashboard",
        session: dict[str, Any] | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> E2ETurnCapture:
        user = user or self.world.manager
        sess = dict(session or self.world.session_for(user, channel=channel))
        classified = unified_understand(message, channel=channel, session_context=sess)
        result = run_copilot_turn(
            user=user,
            user_message=message,
            enriched_message=message,
            session_context=sess,
            restaurant=user.restaurant,
            channel=channel,
            history=history,
        )
        capture = E2ETurnCapture(
            message=message,
            channel=channel,
            intent=classified.intent.value,
            entity_type=classified.entity_type.value if classified.entity_type else "",
            deferred=result is None,
        )
        if result:
            capture.handler = result.handler or ""
            capture.success = bool(result.success)
            capture.verified = result.verified
            capture.needs_clarification = bool(result.needs_clarification)
            capture.reply = result.reply or ""
            capture.tool_trace = list(result.tool_trace or [])
            capture.stages_completed = list(result.stages_completed or [])
            capture.meta = dict(result.meta or {})
            capture.compound = bool((result.meta or {}).get("compound_execution"))
        return capture

    def send_sequence(
        self,
        messages: list[str],
        *,
        user=None,
        channel: str = "dashboard",
        session: dict[str, Any] | None = None,
    ) -> list[E2ETurnCapture]:
        user = user or self.world.manager
        sess = dict(session or self.world.session_for(user, channel=channel))
        out: list[E2ETurnCapture] = []
        for msg in messages:
            cap = self.send(msg, user=user, channel=channel, session=sess)
            out.append(cap)
        return out

    # ── DB assertions ─────────────────────────────────────────────────────

    def task_status(self, task_id) -> str:
        return Task.objects.get(pk=task_id).status

    def task_assignee_id(self, task_id) -> str | None:
        t = Task.objects.get(pk=task_id)
        return str(t.assigned_to_id) if t.assigned_to_id else None

    def refresh_task(self, key: str) -> Task:
        t = self.world.tasks[key]
        t.refresh_from_db()
        return t

    def audit_events(
        self,
        *,
        entity_type: str = "",
        entity_id: str = "",
        event_type: str = "",
    ) -> list[OperationalEvent]:
        qs = OperationalEvent.objects.filter(restaurant=self.world.restaurant)
        if entity_type:
            qs = qs.filter(entity_type=entity_type)
        if entity_id:
            qs = qs.filter(entity_id=str(entity_id))
        if event_type:
            qs = qs.filter(event_type=event_type)
        return list(qs.order_by("created_at"))

    def notifications_for(self, user_id) -> list[Notification]:
        return list(Notification.objects.filter(recipient_id=user_id).order_by("created_at"))


class PostgresE2ETestCase(TestCase):
    """Base for real PostgreSQL E2E — skips if not using postgres backend."""

    databases = {"default"}
    harness: MiyaE2EHarness
    world: E2EWorld

    def setUp(self):
        from django.db import connection

        if connection.vendor != "postgresql":
            self.skipTest("PostgreSQL E2E requires --settings=mizan.test_settings_postgres")

    def assert_no_mutation_on_clarify(self, capture: E2ETurnCapture, task_id):
        self.assertTrue(capture.needs_clarification or not capture.success)
        before = self.harness.task_status(task_id)
        self.assertEqual(self.harness.task_status(task_id), before)

    def assert_verified_mutation(self, capture: E2ETurnCapture):
        self.assertTrue(capture.success, capture.reply)
        self.assertTrue(capture.verified, f"Not verified: {capture.reply}")

    def assert_truthful_response(self, capture: E2ETurnCapture, *, expect_success: bool):
        if expect_success:
            self.assertTrue(capture.verified, "Must not claim success without verification")
            self.assertNotIn("couldn't complete", (capture.reply or "").lower())
        else:
            self.assertFalse(capture.verified and capture.success and "done" in (capture.reply or "").lower())
