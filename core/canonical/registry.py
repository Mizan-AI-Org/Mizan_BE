"""Canonical entity registry — model, service, API, status, permission."""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class CanonicalEntity:
    """Single operational definition for a Mizan domain entity."""

    name: str
    canonical_model: str
    legacy_models: tuple[str, ...] = ()
    canonical_service: str = ""
    canonical_api: str = ""
    status_enum: str = ""
    read_permission: str = ""
    write_permission: str = ""
    notes: str = ""
    migration_status: str = "partial"  # partial | unified_read | unified


CANONICAL_ENTITIES: dict[str, CanonicalEntity] = {
    "task": CanonicalEntity(
        name="task",
        canonical_model="dashboard.Task",
        legacy_models=("scheduling.Task", "scheduling.ShiftTask", "staff.ScheduleTask"),
        canonical_service="miya.services.ops.tasks",
        canonical_api="/api/dashboard/tasks-demands/",
        status_enum="core.canonical.status.CANONICAL_TASK_STATUSES",
        read_permission="manage_widgets OR assignee",
        write_permission="manage_widgets OR assignee (status only)",
        notes=(
            "Reads merge dashboard.Task + scheduling.Task via core.canonical.tasks. "
            "Miya mutations (create/assign/status) target dashboard.Task; "
            "scheduling writes remain via dashboard PATCH router until Phase 15."
        ),
        migration_status="unified_read",
    ),
    "incident": CanonicalEntity(
        name="incident",
        canonical_model="staff.SafetyConcernReport",
        legacy_models=("reporting.Incident",),
        canonical_service="miya.services.ops.incidents",
        canonical_api="/api/staff/safety-concerns/",
        status_enum="OPEN, INVESTIGATING, RESOLVED, CLOSED, DISMISSED",
        read_permission="manage_widgets OR reporter",
        write_permission="manage_widgets OR create (staff)",
        notes="reporting.Incident is legacy — do not create via photo_router in production.",
        migration_status="partial",
    ),
    "invoice": CanonicalEntity(
        name="invoice",
        canonical_model="finance.Invoice",
        legacy_models=(),
        canonical_service="miya.services.ops.invoices",
        canonical_api="/api/finance/invoices/",
        status_enum="DRAFT, PENDING_APPROVAL, APPROVED, PAID, REJECTED, RETURNED",
        read_permission="run_reports",
        write_permission="run_reports + approval tier",
        migration_status="partial",
    ),
    "document": CanonicalEntity(
        name="document",
        canonical_model="miya.TenantDocument",
        legacy_models=("staff.StaffDocument", "payroll.ComplianceDocument"),
        canonical_service="miya.services.ops.documents",
        canonical_api="/api/dashboard/tenant-documents/",
        status_enum="active, expired, archived",
        read_permission="tenant member",
        write_permission="manage_settings OR upload",
        migration_status="partial",
    ),
    "reminder": CanonicalEntity(
        name="reminder",
        canonical_model="scheduling.PersonalReminder",
        legacy_models=("payroll.ComplianceReminder",),
        canonical_service="miya.services.ops.meetings",
        canonical_api="/api/dashboard/personal-reminders/",
        status_enum="pending, fired, cancelled",
        read_permission="owner OR manage_widgets",
        write_permission="owner OR manage_widgets",
        migration_status="partial",
    ),
    "meeting": CanonicalEntity(
        name="meeting",
        canonical_model="external:google_calendar",
        legacy_models=("dashboard.Task(category=MEETING)",),
        canonical_service="miya.services.ops.meetings",
        canonical_api="/api/dashboard/meetings-reminders/",
        status_enum="scheduled, confirmed, cancelled",
        read_permission="manage_widgets",
        write_permission="manage_widgets",
        notes="No Django Meeting model — Google Calendar is system of record.",
        migration_status="partial",
    ),
    "staff": CanonicalEntity(
        name="staff",
        canonical_model="accounts.CustomUser",
        legacy_models=("accounts.StaffProfile", "staff.StaffProfile"),
        canonical_service="miya.services.ops.staff",
        canonical_api="/api/accounts/staff/",
        status_enum="active, inactive",
        read_permission="tenant member",
        write_permission="manage_staff",
        migration_status="partial",
    ),
    "category": CanonicalEntity(
        name="category",
        canonical_model="config:restaurant.department_owners",
        legacy_models=("dashboard.DashboardCategory", "scheduling.TaskCategory"),
        canonical_service="miya.services.ops.categories",
        canonical_api="/api/dashboard/agent/department-owners/",
        status_enum="n/a",
        read_permission="manage_widgets",
        write_permission="manage_settings",
        migration_status="partial",
    ),
    "establishment": CanonicalEntity(
        name="establishment",
        canonical_model="accounts.BusinessLocation",
        legacy_models=(),
        canonical_service="miya.services.ops.establishments",
        canonical_api="/api/accounts/locations/",
        status_enum="active",
        read_permission="tenant member (scoped)",
        write_permission="manage_settings",
        migration_status="partial",
    ),
}


def get_canonical_entity(name: str) -> CanonicalEntity | None:
    return CANONICAL_ENTITIES.get((name or "").strip().lower())
