"""E2E world seeding — tenant, establishments, users, operational records."""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from django.utils import timezone

from accounts.models import BusinessLocation, CustomUser, Restaurant
from dashboard.models import Task
from finance.models import Invoice
from miya.models import OperationalEvent, TenantDocument
from scheduling.memory_models import PersonalReminder
from staff.models_task import SafetyConcernReport


def _uid(prefix: str = "") -> str:
    return f"{prefix}{uuid.uuid4().hex[:8]}"


@dataclass
class E2EWorld:
    """Known DB state for deterministic E2E assertions."""

    suffix: str
    restaurant_a: Restaurant
    restaurant_b: Restaurant | None = None
    loc_a: BusinessLocation | None = None
    loc_b: BusinessLocation | None = None
    loc_b2: BusinessLocation | None = None
    manager_a: CustomUser | None = None
    manager_multi: CustomUser | None = None
    staff_ahmed: CustomUser | None = None
    staff_b_only: CustomUser | None = None
    tasks: dict[str, Task] = field(default_factory=dict)
    incidents: dict[str, SafetyConcernReport] = field(default_factory=dict)
    invoices: dict[str, Invoice] = field(default_factory=dict)
    documents: dict[str, TenantDocument] = field(default_factory=dict)
    reminders: dict[str, PersonalReminder] = field(default_factory=dict)

    @property
    def restaurant(self) -> Restaurant:
        return self.restaurant_a

    @property
    def location(self) -> BusinessLocation:
        assert self.loc_a is not None
        return self.loc_a

    @property
    def manager(self) -> CustomUser:
        assert self.manager_a is not None
        return self.manager_a

    def session_for(
        self,
        user: CustomUser,
        *,
        location: BusinessLocation | None = None,
        channel: str = "dashboard",
    ) -> dict[str, Any]:
        loc = location or self.loc_a
        locs = []
        if self.loc_a:
            locs.append({"id": str(self.loc_a.id), "name": self.loc_a.name})
        if self.loc_b:
            locs.append({"id": str(self.loc_b.id), "name": self.loc_b.name})
        if self.loc_b2:
            locs.append({"id": str(self.loc_b2.id), "name": self.loc_b2.name})
        return {
            "user_id": str(user.id),
            "restaurant_id": str(user.restaurant_id),
            "role": user.role,
            "location_id": str(loc.id) if loc else None,
            "location_name": loc.name if loc else "",
            "available_locations": locs,
            "channel": channel,
            "_pipeline_message_id": f"msg-{_uid()}",
            "_pipeline_conversation_id": f"conv-{_uid()}",
            "language": "en",
        }


def seed_single_establishment(*, suffix: str | None = None) -> E2EWorld:
    """Restaurant A with one location, manager, staff Ahmed, sample tasks."""
    sfx = suffix or _uid()
    rest = Restaurant.objects.create(
        name=f"E2E Restaurant {sfx}",
        email=f"e2e-{sfx}@test.mizan.local",
        timezone="Africa/Casablanca",
        currency="MAD",
        language="en",
    )
    loc = BusinessLocation.objects.create(
        restaurant=rest,
        name="Casablanca",
        is_primary=True,
        is_active=True,
    )
    manager = CustomUser.objects.create_user(
        email=f"manager-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Manager",
        last_name="Test",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc,
    )
    manager.managed_locations.add(loc)
    ahmed = CustomUser.objects.create_user(
        email=f"ahmed-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Ahmed",
        last_name="Benali",
        role="WAITER",
        restaurant=rest,
        primary_location=loc,
    )
    closing = Task.objects.create(
        restaurant=rest,
        title="Closing checklist",
        status="PENDING",
        priority="MEDIUM",
        location=loc,
        created_by=manager,
    )
    decoration = Task.objects.create(
        restaurant=rest,
        title="Decoration setup",
        status="PENDING",
        priority="MEDIUM",
        location=loc,
        created_by=manager,
    )
    return E2EWorld(
        suffix=sfx,
        restaurant_a=rest,
        loc_a=loc,
        manager_a=manager,
        staff_ahmed=ahmed,
        tasks={"closing": closing, "decoration": decoration},
    )


def seed_barometre_zamazama_stage_setup(*, suffix: str | None = None) -> E2EWorld:
    """Two-branch tenant matching Stage Setup close bug report."""
    from datetime import date

    sfx = suffix or _uid()
    rest = Restaurant.objects.create(
        name=f"Barometre Group {sfx}",
        email=f"barometre-{sfx}@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc_main = BusinessLocation.objects.create(
        restaurant=rest,
        name="Barometre - Main",
        is_primary=True,
        is_active=True,
    )
    loc_zama = BusinessLocation.objects.create(
        restaurant=rest,
        name="Zama Zama",
        is_primary=False,
        is_active=True,
    )
    manager = CustomUser.objects.create_user(
        email=f"mgr-barometre-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Manager",
        last_name="Test",
        role="MANAGER",
        restaurant=rest,
        primary_location=loc_main,
    )
    manager.managed_locations.add(loc_main, loc_zama)
    manager.allowed_locations.add(loc_main, loc_zama)
    ahmed = CustomUser.objects.create_user(
        email=f"ahmed-hassan-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Ahmed",
        last_name="Hassan",
        role="WAITER",
        restaurant=rest,
        primary_location=loc_zama,
    )
    stage = Task.objects.create(
        restaurant=rest,
        title="Stage Setup",
        description="Set up the stage for the ceremony in the backyard.",
        status="PENDING",
        priority="HIGH",
        location=loc_zama,
        assigned_to=ahmed,
        created_by=manager,
        due_date=date(2026, 8, 1),
    )
    stage.assignees.add(ahmed)
    return E2EWorld(
        suffix=sfx,
        restaurant_a=rest,
        loc_a=loc_main,
        loc_b=loc_zama,
        manager_a=manager,
        manager_multi=manager,
        staff_ahmed=ahmed,
        tasks={"stage_setup": stage},
    )


def seed_barometre_seating_arrangement(*, suffix: str | None = None) -> E2EWorld:
    """Barometre - Main seating task — matches Operations Live close bug."""
    from datetime import date

    world = seed_barometre_zamazama_stage_setup(suffix=suffix)
    seating = Task.objects.create(
        restaurant=world.restaurant,
        title="Seating Arrangement",
        description="Arrange seating for the wedding ceremony.",
        status="PENDING",
        priority="HIGH",
        location=world.loc_a,
        assigned_to=world.manager,
        created_by=world.manager,
        due_date=date(2026, 8, 1),
    )
    seating.assignees.add(world.manager)
    world.tasks["seating_arrangement"] = seating
    return world


def seed_three_decoration_tasks(*, suffix: str | None = None) -> E2EWorld:
    world = seed_single_establishment(suffix=suffix)
    for i in range(3):
        t = Task.objects.create(
            restaurant=world.restaurant_a,
            title="Decoration",
            status="PENDING",
            priority="MEDIUM",
            location=world.loc_a,
            created_by=world.manager_a,
        )
        world.tasks[f"decoration_{i}"] = t
    return world


def seed_multi_establishment(*, suffix: str | None = None) -> E2EWorld:
    """Manager with access to A and B; staff only in A."""
    sfx = suffix or _uid()
    rest_a = Restaurant.objects.create(
        name=f"E2E RestA {sfx}",
        email=f"e2e-a-{sfx}@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    rest_b = Restaurant.objects.create(
        name=f"E2E RestB {sfx}",
        email=f"e2e-b-{sfx}@test.mizan.local",
        timezone="Africa/Casablanca",
    )
    loc_a = BusinessLocation.objects.create(
        restaurant=rest_a, name="Site A", is_primary=True, is_active=True
    )
    loc_b = BusinessLocation.objects.create(
        restaurant=rest_b, name="Site B", is_primary=True, is_active=True
    )
    manager = CustomUser.objects.create_user(
        email=f"multi-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Multi",
        last_name="Manager",
        role="MANAGER",
        restaurant=rest_a,
        primary_location=loc_a,
    )
    manager.managed_locations.add(loc_a, loc_b)
    manager.allowed_locations.add(loc_a, loc_b)
    staff_a = CustomUser.objects.create_user(
        email=f"staffa-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Ahmed",
        last_name="A",
        role="WAITER",
        restaurant=rest_a,
        primary_location=loc_a,
    )
    staff_b = CustomUser.objects.create_user(
        email=f"staffb-{sfx}@test.mizan.local",
        password="testpass",
        first_name="Sara",
        last_name="B",
        role="WAITER",
        restaurant=rest_b,
        primary_location=loc_b,
    )
    task_a = Task.objects.create(
        restaurant=rest_a,
        title="Closing checklist",
        status="PENDING",
        location=loc_a,
        created_by=manager,
    )
    task_b = Task.objects.create(
        restaurant=rest_b,
        title="Closing checklist",
        status="PENDING",
        location=loc_b,
        created_by=staff_b,
    )
    return E2EWorld(
        suffix=sfx,
        restaurant_a=rest_a,
        restaurant_b=rest_b,
        loc_a=loc_a,
        loc_b=loc_b,
        manager_a=manager,
        manager_multi=manager,
        staff_ahmed=staff_a,
        staff_b_only=staff_b,
        tasks={"closing_a": task_a, "closing_b": task_b},
    )


def seed_incident(*, world: E2EWorld, title: str = "Freezer broken") -> SafetyConcernReport:
    inc = SafetyConcernReport.objects.create(
        restaurant=world.restaurant_a,
        title=title,
        description=title,
        incident_type="EQUIPMENT",
        severity="HIGH",
        status="OPEN",
        business_location=world.loc_a,
        reporter=world.manager_a,
    )
    world.incidents[title] = inc
    return inc


def seed_invoice(*, world: E2EWorld, vendor: str = "ABC Foods") -> Invoice:
    inv = Invoice.objects.create(
        restaurant=world.restaurant_a,
        vendor_name=vendor,
        amount="1500.00",
        due_date=timezone.now().date(),
        status="SUBMITTED",
        location=world.loc_a,
        created_by=world.manager_a,
        invoice_number=f"INV-{_uid()}",
    )
    world.invoices[vendor] = inv
    return inv


def seed_document(*, world: E2EWorld, title: str = "Insurance policy") -> TenantDocument:
    doc = TenantDocument.objects.create(
        restaurant=world.restaurant_a,
        location=world.loc_a,
        uploaded_by=world.manager_a,
        title=title,
        category="insurance",
    )
    world.documents[title] = doc
    return doc


def seed_reminder(*, world: E2EWorld, title: str = "Insurance renewal") -> PersonalReminder:
    rem = PersonalReminder.objects.create(
        restaurant=world.restaurant_a,
        owner=world.manager_a,
        title=title,
        body=title,
        due_at=timezone.now(),
        timezone_name="Africa/Casablanca",
        status="pending",
    )
    world.reminders[title] = rem
    return rem


def count_audit_events(
    *,
    restaurant_id,
    entity_type: str = "",
    entity_id: str = "",
    event_type: str = "",
) -> int:
    qs = OperationalEvent.objects.filter(restaurant_id=restaurant_id)
    if entity_type:
        qs = qs.filter(entity_type=entity_type)
    if entity_id:
        qs = qs.filter(entity_id=str(entity_id))
    if event_type:
        qs = qs.filter(event_type=event_type)
    return qs.count()
