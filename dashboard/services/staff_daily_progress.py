"""Compute and archive per-staff daily task progress for manager accountability."""

from __future__ import annotations

from datetime import date, datetime, time, timedelta

from django.db.models import Q
from django.utils import timezone

from accounts.models import CustomUser
from dashboard.models import StaffDailyProgressReport, Task
from dashboard.views_ops_memory import _is_user_absent


def _day_bounds(on_date: date) -> tuple[datetime, datetime]:
    tz = timezone.get_current_timezone()
    start = timezone.make_aware(datetime.combine(on_date, time.min), tz)
    return start, start + timedelta(days=1)


def compute_staff_daily_progress(restaurant, on_date: date | None = None) -> list[dict]:
    """
    Progress for tasks tied to a calendar day (created that day or due that day).
    Does not include stale open tasks from prior days — those belong in archives only.
    """
    on_date = on_date or timezone.localdate()
    day_start, day_end = _day_bounds(on_date)

    staff = list(
        CustomUser.objects.filter(restaurant=restaurant, is_active=True).order_by(
            "first_name", "last_name"
        )[:80]
    )

    rows: list[dict] = []
    for u in staff:
        day_qs = Task.objects.filter(restaurant=restaurant, assigned_to=u).filter(
            Q(created_at__gte=day_start, created_at__lt=day_end) | Q(due_date=on_date)
        )
        total = day_qs.count()
        if total == 0:
            continue

        done = day_qs.filter(status="COMPLETED").count()
        open_count = day_qs.filter(
            status__in=["PENDING", "ACCEPTED", "IN_PROGRESS"]
        ).count()
        pct = int(round((done / total) * 100)) if total else 0
        rows.append(
            {
                "id": str(u.id),
                "name": f"{u.first_name or ''} {u.last_name or ''}".strip() or u.email,
                "role": getattr(u, "role", "") or "",
                "is_absent": _is_user_absent(u, restaurant, on_date=on_date),
                "total": total,
                "done": done,
                "open": open_count,
                "pct": pct,
            }
        )

    rows.sort(key=lambda r: (-r["open"], r["name"].lower()))
    return rows


def snapshot_staff_daily_progress(restaurant, on_date: date) -> int:
    """Persist end-of-day progress rows; returns number of staff rows saved."""
    rows = compute_staff_daily_progress(restaurant, on_date=on_date)
    saved = 0
    for row in rows:
        StaffDailyProgressReport.objects.update_or_create(
            restaurant=restaurant,
            report_date=on_date,
            staff_id=row["id"],
            defaults={
                "staff_name": row["name"],
                "role": row["role"],
                "is_absent": row["is_absent"],
                "total": row["total"],
                "done": row["done"],
                "open": row["open"],
                "pct": row["pct"],
            },
        )
        saved += 1
    return saved


def load_staff_daily_progress_snapshot(restaurant, on_date: date) -> list[dict]:
    qs = StaffDailyProgressReport.objects.filter(
        restaurant=restaurant,
        report_date=on_date,
    ).order_by("-open", "staff_name")
    return [
        {
            "id": str(r.staff_id),
            "name": r.staff_name,
            "role": r.role,
            "is_absent": r.is_absent,
            "total": r.total,
            "done": r.done,
            "open": r.open,
            "pct": r.pct,
        }
        for r in qs
    ]


def staff_has_today_live_activity(restaurant, staff, on_date: date | None = None) -> bool:
    """Whether a staff member should appear on today's live board (matches widget rules)."""
    on_date = on_date or timezone.localdate()
    day_start, day_end = _day_bounds(on_date)

    if Task.objects.filter(restaurant=restaurant, assigned_to=staff).filter(
        Q(created_at__gte=day_start, created_at__lt=day_end) | Q(due_date=on_date)
    ).exists():
        return True

    from scheduling.models import ShiftChecklistProgress, ShiftTask

    if ShiftChecklistProgress.objects.filter(
        staff=staff,
        shift__schedule__restaurant=restaurant,
        shift__shift_date=on_date,
        status="IN_PROGRESS",
    ).filter(
        Q(created_at__gte=day_start, created_at__lt=day_end)
        | Q(updated_at__gte=day_start, updated_at__lt=day_end)
    ).exists():
        return True

    if ShiftTask.objects.filter(
        shift__schedule__restaurant=restaurant,
        shift__shift_date=on_date,
        assigned_to=staff,
        created_at__gte=day_start,
        created_at__lt=day_end,
    ).exists():
        return True

    return False


def close_stale_shift_checklists(*, restaurant=None) -> int:
    """Mark in-progress checklists on past shifts as incomplete (end-of-day cleanup)."""
    from scheduling.models import ShiftChecklistProgress

    today = timezone.localdate()
    qs = ShiftChecklistProgress.objects.filter(
        status="IN_PROGRESS",
        shift__shift_date__lt=today,
    )
    if restaurant is not None:
        qs = qs.filter(staff__restaurant=restaurant)
    return qs.update(status="INCOMPLETE_SHIFT_END")


def progress_history_summaries(restaurant, *, days: int = 30) -> list[dict]:
    """Daily rollups for manager history picker (excludes today — live only)."""
    today = timezone.localdate()
    since = today - timedelta(days=max(1, min(days, 90)))
    by_date: dict[date, dict] = {}
    for row in StaffDailyProgressReport.objects.filter(
        restaurant=restaurant,
        report_date__gte=since,
        report_date__lt=today,
    ).order_by("-report_date"):
        bucket = by_date.setdefault(
            row.report_date,
            {"date": str(row.report_date), "staff_count": 0, "incomplete": 0, "pct_sum": 0},
        )
        bucket["staff_count"] += 1
        bucket["incomplete"] += row.open
        bucket["pct_sum"] += row.pct

    out = []
    for d in sorted(by_date.keys(), reverse=True):
        b = by_date[d]
        count = b["staff_count"] or 1
        out.append(
            {
                "date": b["date"],
                "staff_count": b["staff_count"],
                "incomplete": b["incomplete"],
                "avg_pct": int(round(b["pct_sum"] / count)),
            }
        )
    return out
