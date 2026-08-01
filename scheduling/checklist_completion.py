"""Finalize and archive shift checklist completions (responses, photos, compliance)."""

from __future__ import annotations

import logging
from typing import Any

from django.utils import timezone

logger = logging.getLogger(__name__)


def _verification_record_for(task, staff_user):
    from scheduling.models import TaskVerificationRecord

    return (
        TaskVerificationRecord.objects.filter(task=task, submitted_by=staff_user)
        .order_by("-submitted_at")
        .first()
    )


def build_checklist_completion_summary(prog, staff_user) -> dict[str, Any]:
    """Assemble a durable snapshot of every task, response, photo, and branch outcome."""
    from scheduling.checklist_photo import task_requires_photo
    from scheduling.models import ShiftTask

    task_ids = [str(t) for t in (prog.task_ids or [])]
    responses = dict(prog.responses or {})
    tasks_out: list[dict[str, Any]] = []
    missing_photo_task_ids: list[str] = []
    yes_count = no_count = na_count = 0

    for tid in task_ids:
        task = ShiftTask.objects.filter(id=tid).first()
        if not task:
            continue

        response = responses.get(tid) or responses.get(str(tid))
        if response == "yes":
            yes_count += 1
        elif response == "no":
            no_count += 1
        elif response == "n_a":
            na_count += 1

        requires_photo = task_requires_photo(task)
        record = _verification_record_for(task, staff_user)
        photos = list((record.photo_evidence if record else None) or [])
        branch = (record.checklist_responses or {}).get("branch") if record else None

        if response == "yes" and requires_photo and not photos:
            missing_photo_task_ids.append(str(task.id))

        tasks_out.append(
            {
                "task_id": str(task.id),
                "title": task.title,
                "description": task.description or "",
                "response": response,
                "task_status": task.status,
                "requires_photo": requires_photo,
                "photo_count": len(photos),
                "photo_evidence": photos,
                "branch_action": branch,
                "completed_at": (
                    task.completed_at.isoformat() if task.completed_at else None
                ),
                "verification_record_id": str(record.id) if record else None,
            }
        )

    total = len(task_ids)
    answered = sum(1 for tid in task_ids if tid in responses or str(tid) in responses)
    fully_compliant = (
        answered >= total
        and not missing_photo_task_ids
        and all(
            t.get("response") in ("yes", "no", "n_a") for t in tasks_out if t.get("response")
        )
    )

    shift = prog.shift
    template_names = []
    try:
        template_names = list(shift.task_templates.values_list("name", flat=True))
    except Exception:
        pass

    return {
        "progress_id": str(prog.id),
        "shift_id": str(prog.shift_id),
        "staff_id": str(staff_user.id),
        "staff_name": (
            f"{(staff_user.first_name or '').strip()} {(staff_user.last_name or '').strip()}".strip()
            or getattr(staff_user, "email", "")
        ),
        "shift_date": (
            shift.shift_date.isoformat()
            if shift and getattr(shift, "shift_date", None)
            else None
        ),
        "channel": prog.channel or "whatsapp",
        "template_names": template_names,
        "completed_at": timezone.now().isoformat(),
        "summary": {
            "total": total,
            "answered": answered,
            "yes": yes_count,
            "no": no_count,
            "n_a": na_count,
        },
        "fully_compliant": fully_compliant,
        "missing_photo_task_ids": missing_photo_task_ids,
        "tasks": tasks_out,
    }


def finalize_shift_checklist_completion(prog, staff_user, *, force: bool = False) -> dict[str, Any]:
    """
    Mark checklist complete and persist a full compliance archive on ShiftChecklistProgress.
    Idempotent when already finalized unless force=True.
    """
    from scheduling.audit import AuditActionType, AuditSeverity, AuditTrailService

    if (
        prog.status == "COMPLETED"
        and getattr(prog, "completion_summary", None)
        and not force
    ):
        return prog.completion_summary

    summary = build_checklist_completion_summary(prog, staff_user)
    now = timezone.now()

    prog.responses = prog.responses or {}
    prog.status = "COMPLETED"
    prog.completed_at = prog.completed_at or now
    prog.current_task_id = ""
    prog.completion_summary = summary
    prog.save(
        update_fields=[
            "responses",
            "status",
            "completed_at",
            "current_task_id",
            "completion_summary",
            "updated_at",
        ]
    )

    try:
        AuditTrailService.log_activity(
            user=staff_user,
            action=AuditActionType.COMPLETE,
            description=(
                f"Checklist completed — {summary['summary']['yes']}/{summary['summary']['total']} "
                f"confirmed, {summary['summary']['no']} flagged, "
                f"{sum(t['photo_count'] for t in summary['tasks'])} photo(s) stored."
            ),
            content_object=prog,
            new_values={
                "progress_id": str(prog.id),
                "shift_id": str(prog.shift_id),
                "fully_compliant": summary["fully_compliant"],
                "missing_photo_task_ids": summary["missing_photo_task_ids"],
                "photo_count": sum(t["photo_count"] for t in summary["tasks"]),
            },
            severity=(
                AuditSeverity.HIGH
                if summary["missing_photo_task_ids"]
                else AuditSeverity.MEDIUM
            ),
            metadata={"source": "checklist_completion", "channel": prog.channel or "whatsapp"},
        )
    except Exception:
        logger.exception("checklist finalize audit failed progress=%s", prog.id)

    logger.info(
        "checklist finalized progress=%s staff=%s compliant=%s photos=%s",
        prog.id,
        staff_user.id,
        summary["fully_compliant"],
        sum(t["photo_count"] for t in summary["tasks"]),
    )
    return summary
