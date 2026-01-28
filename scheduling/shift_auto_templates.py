"""
Auto-attach TaskTemplates and generate per-shift tasks/checklists.

Used by the agent shift-creation endpoint to ensure that when a shift is created,
the relevant Process/Task templates are automatically associated and the resulting
tasks are tracked against the shift for dashboard visibility and conversational
checklist execution.
"""

from __future__ import annotations

from dataclasses import dataclass
import re
import uuid
from typing import Iterable, List, Optional, Tuple

from django.db import transaction
from django.utils import timezone

from .models import AssignedShift, ShiftTask
from .task_templates import TaskTemplate
from core.i18n import get_effective_language, normalize_language


@dataclass(frozen=True)
class AutoAttachResult:
    shift_context: str
    used_templates: List[TaskTemplate]
    created_shift_tasks: int
    created_checklist_executions: int
    used_fallback_custom_template: bool


SHIFT_CONTEXTS = {
    "OPENING",
    "CLOSING",
    "BREAKFAST",
    "LUNCH",
    "DINNER",
    "GENERAL",
}

def infer_shift_area_label(*, staff_role: str, department: str | None = None, workspace_location: str | None = None) -> str:
    """
    Infer a human-facing area label (e.g., Front of House, Kitchen, Bar, Café).
    Prefers explicit department/workspace strings when provided.
    """
    dept = _safe_text(department)
    ws = _safe_text(workspace_location)
    combined = f"{dept} {ws}".strip().lower()

    # Prefer explicit area mentions
    if combined:
        if "cafe" in combined or "café" in combined:
            return "Café"
        if "bar" in combined:
            return "Bar"
        if "kitchen" in combined:
            return "Kitchen"
        if "front" in combined or "foh" in combined or "floor" in combined:
            return "Front of House"
        # Use provided department/workspace as-is if it looks like a label
        # (keep short; title-case it)
        if len(dept) <= 30 and dept:
            return dept.title()
        if len(ws) <= 30 and ws:
            return ws.title()

    role = _safe_text(staff_role).upper()
    # Role → area mapping
    if role in {"CHEF", "KITCHEN_STAFF", "SOUS_CHEF"}:
        return "Kitchen"
    if role in {"BARTENDER"}:
        return "Bar"
    if role in {"WAITER", "SERVER", "HOST", "CASHIER"}:
        return "Front of House"
    if role in {"CLEANER"}:
        return "Operations"
    if role in {"DELIVERY"}:
        return "Delivery"
    if role in {"MANAGER", "SUPERVISOR", "OWNER", "ADMIN"}:
        return "Operations"

    return "Front of House"


def generate_shift_title(
    *,
    shift_context: str,
    staff_role: str,
    department: str | None = None,
    workspace_location: str | None = None,
) -> str:
    """
    Generate a descriptive, context-aware shift title like:
    - "Breakfast Service – Front of House"
    - "Dinner Service – Kitchen"
    - "Opening Shift – Café"
    - "Closing Shift – Bar"
    """
    ctx = _safe_text(shift_context).upper() or "GENERAL"
    area = infer_shift_area_label(staff_role=staff_role, department=department, workspace_location=workspace_location)

    ctx_label = {
        "BREAKFAST": "Breakfast Service",
        "LUNCH": "Lunch Service",
        "DINNER": "Dinner Service",
        "OPENING": "Opening Shift",
        "CLOSING": "Closing Shift",
        "GENERAL": "Shift",
    }.get(ctx, "Shift")

    return f"{ctx_label} – {area}".strip()


def _safe_text(x: object | None) -> str:
    return str(x or "").strip()


def detect_shift_context(
    *,
    shift_title: str | None,
    shift_notes: str | None,
    start_dt,
    end_dt,
) -> str:
    """
    Identify the shift context from title/notes first, then time heuristics.
    """
    text = f"{_safe_text(shift_title)} {_safe_text(shift_notes)}".lower()

    # Keyword-based signals (strongest)
    if re.search(r"\b(open|opening|open up|set up)\b", text):
        return "OPENING"
    if re.search(r"\b(close|closing|shutdown|shut down|lock up)\b", text):
        return "CLOSING"
    if re.search(r"\b(breakfast|morning service)\b", text):
        return "BREAKFAST"
    if re.search(r"\b(lunch|midday service)\b", text):
        return "LUNCH"
    if re.search(r"\b(dinner|evening service)\b", text):
        return "DINNER"

    # Time-based fallback (start/end are datetimes in AssignedShift)
    try:
        start_local = timezone.localtime(start_dt) if start_dt else None
        end_local = timezone.localtime(end_dt) if end_dt else None
        if start_local:
            h = start_local.hour
            # Very early shifts are usually opening/breakfast prep
            if h <= 8:
                return "OPENING"
            if 5 <= h < 11:
                return "BREAKFAST"
            if 11 <= h < 16:
                return "LUNCH"
            if 16 <= h < 22:
                return "DINNER"
        if end_local and end_local.hour >= 22:
            return "CLOSING"
    except Exception:
        pass

    return "GENERAL"


def _score_template(
    *,
    template: TaskTemplate,
    shift_context: str,
    shift_title: str,
    staff_role: str,
) -> int:
    """
    Heuristic scoring for selecting relevant TaskTemplates.
    Higher score => more likely relevant.
    """
    score = 0

    name = _safe_text(template.name).lower()
    desc = _safe_text(getattr(template, "description", None)).lower()
    title = _safe_text(shift_title).lower()
    role = _safe_text(staff_role).upper()

    # Strong: exact template_type match for opening/closing
    if shift_context in {"OPENING", "CLOSING"}:
        if _safe_text(getattr(template, "template_type", "")).upper() == shift_context:
            score += 100

    # Strong: name contains shift title
    if title and title in name:
        score += 80

    # Keyword matches by context
    keywords = {
        "OPENING": ["opening", "open", "setup", "set up", "pre-service", "pre service", "morning opening"],
        "CLOSING": ["closing", "close", "shutdown", "lock up", "end of day", "clean down"],
        "BREAKFAST": ["breakfast", "morning service", "morning prep"],
        "LUNCH": ["lunch", "midday service"],
        "DINNER": ["dinner", "evening service", "service"],
        "GENERAL": ["service", "daily", "checklist", "standard"],
    }.get(shift_context, [])

    for kw in keywords:
        if kw in name:
            score += 35
        if kw in desc:
            score += 10

    # Role hints (soft)
    if role in {"CHEF", "KITCHEN_STAFF"}:
        if any(k in name for k in ["kitchen", "prep", "line", "station", "food"]):
            score += 10
    if role in {"WAITER", "SERVER", "HOST", "BARTENDER"}:
        if any(k in name for k in ["floor", "service", "tables", "bar", "front"]):
            score += 10
    if role in {"CLEANER"}:
        if any(k in name for k in ["clean", "sanitize", "deep clean"]):
            score += 10
    if role in {"MANAGER", "SUPERVISOR", "OWNER", "ADMIN"}:
        if any(k in name for k in ["audit", "manager", "supervisor", "compliance"]):
            score += 10

    # De-prioritize inactive templates defensively (should already be filtered)
    if not getattr(template, "is_active", True):
        score -= 1000

    return score


def find_relevant_task_templates(
    *,
    restaurant,
    shift_context: str,
    staff_role: str,
    shift_title: str | None,
    limit: int = 3,
) -> List[TaskTemplate]:
    """
    Return top matching TaskTemplates for the given context.
    """
    title = _safe_text(shift_title)
    qs = TaskTemplate.objects.filter(restaurant=restaurant, is_active=True)

    # Light pre-filter for opening/closing to reduce noise
    if shift_context in {"OPENING", "CLOSING"}:
        qs = qs.filter(template_type=shift_context) | qs.filter(name__icontains=shift_context.lower())

    candidates = list(qs.distinct())
    ranked: List[Tuple[int, TaskTemplate]] = []
    for t in candidates:
        ranked.append(
            (
                _score_template(
                    template=t,
                    shift_context=shift_context,
                    shift_title=title,
                    staff_role=staff_role,
                ),
                t,
            )
        )

    ranked.sort(key=lambda x: x[0], reverse=True)
    # Only return templates with a positive score to avoid attaching irrelevant items
    picked = [t for (s, t) in ranked if s > 0][: max(0, int(limit))]
    return picked


def _normalize_task_item(item: dict) -> dict:
    """
    Normalize JSON task item from a TaskTemplate.tasks list into title/description/priority.
    """
    title = _safe_text(item.get("title") or item.get("name") or item.get("task") or "")
    description = _safe_text(item.get("description") or item.get("details") or "")
    priority = _safe_text(item.get("priority") or "").upper() or "MEDIUM"
    if priority not in {"LOW", "MEDIUM", "HIGH", "URGENT"}:
        priority = "MEDIUM"
    return {"title": title, "description": description, "priority": priority}


def _template_i18n_block(task_template: TaskTemplate, lang: str) -> dict:
    try:
        raw = getattr(task_template, "i18n", None) or {}
        if isinstance(raw, dict):
            block = raw.get(lang)
            return block if isinstance(block, dict) else {}
    except Exception:
        pass
    return {}


def _template_localized_tasks(task_template: TaskTemplate, lang: str):
    block = _template_i18n_block(task_template, lang)
    tasks = block.get("tasks")
    if isinstance(tasks, list) and tasks:
        return tasks
    return getattr(task_template, "tasks", None) or []


def instantiate_shift_tasks_from_template(
    *,
    shift: AssignedShift,
    assignee,
    task_template: TaskTemplate,
    created_by=None,
    language: str | None = None,
) -> int:
    """
    Create ShiftTask rows for this shift from TaskTemplate.tasks.
    Returns number created.
    """
    created = 0
    lang = normalize_language(language) if language else "en"
    tasks_data = _template_localized_tasks(task_template, lang)
    if not isinstance(tasks_data, list) or not tasks_data:
        return 0

    for raw in tasks_data:
        if not isinstance(raw, dict):
            continue
        t = _normalize_task_item(raw)
        if not t["title"]:
            continue
        ShiftTask.objects.create(
            shift=shift,
            title=t["title"],
            description=t["description"],
            priority=t["priority"],
            status="TODO",
            assigned_to=assignee,
            created_by=created_by,
            # Copy SOP hints when present on template
            sop_document=getattr(task_template, "sop_document", None) or None,
            sop_steps=getattr(task_template, "sop_steps", None) or [],
            is_critical=getattr(task_template, "is_critical", False) or False,
        )
        created += 1
    return created


def ensure_checklist_for_task_template(
    *,
    restaurant,
    task_template: TaskTemplate,
    created_by=None,
    language: str | None = None,
):
    """
    Ensure a ChecklistTemplate exists for a TaskTemplate (creates one if missing).
    Returns ChecklistTemplate or None if checklists app is unavailable.
    """
    try:
        from checklists.models import ChecklistTemplate, ChecklistStep
    except Exception:
        return None

    existing = (
        ChecklistTemplate.objects.filter(restaurant=restaurant, task_template=task_template, is_active=True)
        .prefetch_related("steps")
        .first()
    )
    if existing:
        return existing

    lang = normalize_language(language) if language else "en"
    block = _template_i18n_block(task_template, lang)
    name = _safe_text(block.get("name")) or _safe_text(task_template.name) or "Shift Checklist"
    desc = _safe_text(block.get("description")) or _safe_text(getattr(task_template, "description", "")) or None

    # Create a checklist template derived from the task template (localized where available)
    checklist = ChecklistTemplate.objects.create(
        restaurant=restaurant,
        name=name,
        description=desc,
        category=_safe_text(getattr(task_template, "template_type", "")) or None,
        task_template=task_template,
        is_active=True,
        created_by=created_by,
    )

    # Create steps from task_template.tasks (ordered)
    tasks_data = _template_localized_tasks(task_template, lang)
    order = 1
    for raw in tasks_data:
        if not isinstance(raw, dict):
            continue
        t = _normalize_task_item(raw)
        if not t["title"]:
            continue
        ChecklistStep.objects.create(
            template=checklist,
            title=t["title"],
            description=t["description"] or None,
            step_type="CHECK",
            order=order,
            is_required=True,
        )
        order += 1

    return checklist


def ensure_checklist_execution_for_shift(
    *,
    checklist_template,
    assignee,
    shift: AssignedShift,
) -> int:
    """
    Ensure a ChecklistExecution exists for this (template, shift, assignee).
    Returns 1 if created, 0 if already existed or unavailable.
    """
    if checklist_template is None:
        return 0

    try:
        from checklists.models import ChecklistExecution, ChecklistStepResponse
    except Exception:
        return 0

    existing = ChecklistExecution.objects.filter(
        template=checklist_template, assigned_shift=shift, assigned_to=assignee
    ).first()
    if existing:
        return 0

    execution = ChecklistExecution.objects.create(
        template=checklist_template,
        assigned_to=assignee,
        assigned_shift=shift,
        status="NOT_STARTED",
        due_date=getattr(shift, "end_time", None) or None,
    )

    # Pre-create step responses for conversational checklist flow
    steps = list(checklist_template.steps.all().order_by("order"))
    for step in steps:
        ChecklistStepResponse.objects.create(execution=execution, step=step)

    return 1


def _generate_fallback_tasks(*, shift_context: str, staff_role: str, instructions: str) -> List[dict]:
    """
    Generate a clear, actionable task list when no predefined template exists.
    """
    role = _safe_text(staff_role).upper()
    ctx = _safe_text(shift_context).upper()
    instr = _safe_text(instructions)

    base: List[dict] = []

    if ctx == "OPENING":
        base = [
            {"title": "Arrive and confirm station assignment", "description": "", "priority": "MEDIUM"},
            {"title": "Complete opening safety and hygiene check", "description": "", "priority": "HIGH"},
            {"title": "Set up your work area and required equipment", "description": "", "priority": "MEDIUM"},
        ]
    elif ctx == "CLOSING":
        base = [
            {"title": "Complete end-of-shift cleanup for your area", "description": "", "priority": "HIGH"},
            {"title": "Restock and secure equipment/supplies", "description": "", "priority": "MEDIUM"},
            {"title": "Report any issues to manager (inventory, maintenance, incidents)", "description": "", "priority": "MEDIUM"},
        ]
    elif ctx in {"BREAKFAST", "LUNCH", "DINNER"}:
        base = [
            {"title": "Pre-shift prep and station setup", "description": "", "priority": "MEDIUM"},
            {"title": "Execute core duties during service (quality + speed)", "description": "", "priority": "HIGH"},
            {"title": "Post-service reset and quick clean", "description": "", "priority": "MEDIUM"},
        ]
    else:
        base = [
            {"title": "Review shift notes and confirm responsibilities", "description": "", "priority": "MEDIUM"},
            {"title": "Complete core shift duties", "description": "", "priority": "MEDIUM"},
            {"title": "End-of-shift handoff/update to manager", "description": "", "priority": "LOW"},
        ]

    # Role-specific refinement
    if role in {"CHEF", "KITCHEN_STAFF"}:
        base.insert(1, {"title": "Verify prep list and mise en place", "description": "", "priority": "HIGH"})
        base.append({"title": "Temperature & food safety check (as applicable)", "description": "", "priority": "HIGH"})
    elif role in {"WAITER", "SERVER", "HOST", "BARTENDER"}:
        base.insert(1, {"title": "Check floor readiness (tables, menus, POS, supplies)", "description": "", "priority": "MEDIUM"})
        base.append({"title": "Guest experience check-in (resolve issues quickly)", "description": "", "priority": "MEDIUM"})
    elif role in {"CLEANER"}:
        base = [
            {"title": "Clean and sanitize assigned areas", "description": "", "priority": "HIGH"},
            {"title": "Restroom check and restock", "description": "", "priority": "HIGH"},
            {"title": "Waste removal and floor cleaning", "description": "", "priority": "MEDIUM"},
        ]

    if instr:
        base.append({"title": "Follow special shift instructions", "description": instr, "priority": "HIGH"})

    # Ensure IDs and shape
    out = []
    for t in base:
        out.append(
            {
                "id": str(uuid.uuid4()),
                "title": _safe_text(t.get("title")),
                "description": _safe_text(t.get("description")),
                "priority": _safe_text(t.get("priority")).upper() or "MEDIUM",
                "completed": False,
            }
        )
    return out


@transaction.atomic
def auto_attach_templates_and_tasks(
    *,
    shift: AssignedShift,
    restaurant,
    assignee,
    staff_role: str,
    shift_title: str | None = None,
    instructions: str | None = None,
    created_by=None,
) -> AutoAttachResult:
    """
    Main entrypoint: attach relevant TaskTemplates and create ShiftTasks + checklist executions.
    """
    context = detect_shift_context(
        shift_title=shift_title,
        shift_notes=instructions,
        start_dt=getattr(shift, "start_time", None),
        end_dt=getattr(shift, "end_time", None),
    )

    # Prefer staff language override; else restaurant language; else English
    lang = get_effective_language(user=assignee, restaurant=restaurant)

    templates = find_relevant_task_templates(
        restaurant=restaurant,
        shift_context=context,
        staff_role=staff_role,
        shift_title=shift_title,
        limit=3,
    )

    created_shift_tasks = 0
    created_executions = 0
    used_fallback = False

    if templates:
        shift.task_templates.add(*templates)

        for t in templates:
            created_shift_tasks += instantiate_shift_tasks_from_template(
                shift=shift,
                assignee=assignee,
                task_template=t,
                created_by=created_by,
                language=lang,
            )
            checklist_template = ensure_checklist_for_task_template(
                restaurant=restaurant,
                task_template=t,
                created_by=created_by,
                language=lang,
            )
            created_executions += ensure_checklist_execution_for_shift(
                checklist_template=checklist_template,
                assignee=assignee,
                shift=shift,
            )
    else:
        # Fallback: generate a custom template + checklist + shift tasks
        used_fallback = True
        task_items = _generate_fallback_tasks(
            shift_context=context, staff_role=staff_role, instructions=_safe_text(instructions)
        )

        custom_template = TaskTemplate.objects.create(
            restaurant=restaurant,
            name=_safe_text(shift_title) or f"Custom {context.title()} Checklist",
            description=_safe_text(instructions) or None,
            template_type="CUSTOM",
            frequency="CUSTOM",
            tasks=task_items,
            ai_generated=True,
            ai_prompt=_safe_text(instructions) or None,
            created_by=created_by,
            is_active=True,
        )

        shift.task_templates.add(custom_template)
        templates = [custom_template]

        # ShiftTasks (for dashboard metrics + per-shift tracking)
        created_shift_tasks += instantiate_shift_tasks_from_template(
            shift=shift,
            assignee=assignee,
            task_template=custom_template,
            created_by=created_by,
            language=lang,
        )

        checklist_template = ensure_checklist_for_task_template(
            restaurant=restaurant,
            task_template=custom_template,
            created_by=created_by,
            language=lang,
        )
        created_executions += ensure_checklist_execution_for_shift(
            checklist_template=checklist_template,
            assignee=assignee,
            shift=shift,
        )

    return AutoAttachResult(
        shift_context=context,
        used_templates=templates,
        created_shift_tasks=created_shift_tasks,
        created_checklist_executions=created_executions,
        used_fallback_custom_template=used_fallback,
    )

