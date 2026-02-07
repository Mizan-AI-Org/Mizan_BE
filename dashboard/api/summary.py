from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Q, Avg
from timeclock.models import ClockEvent
from scheduling.models import AssignedShift, ShiftSwapRequest, ShiftTask
from attendance.models import ShiftReview
from dashboard.models import Task as DashboardTask, Alert
from accounts.models import CustomUser
from inventory.models import PurchaseOrder
from staff.models_task import SafetyConcernReport
from reporting.models import Incident


def _staff_name(u: CustomUser) -> str:
    return f"{u.first_name or ''} {u.last_name or ''}".strip() or (u.email or "Staff")


def _priority_score(level: str) -> int:
    # Higher = more urgent
    mapping = {
        "CRITICAL": 1000,
        "OPERATIONAL": 700,
        "PERFORMANCE": 400,
        "PREVENTIVE": 200,
    }
    return mapping.get(str(level).upper(), 100)


def _safe_iso(dt) -> str | None:
    try:
        return dt.isoformat() if dt else None
    except Exception:
        return None


def _build_insight(
    *,
    insight_id: str,
    level: str,
    summary: str,
    recommended_action: str,
    impacted: dict,
    urgency: int,
    category: str,
    action_url: str | None = None,
) -> dict:
    return {
        "id": insight_id,
        "level": level,
        "category": category,
        "urgency": urgency,
        "summary": summary,
        "impacted": impacted,
        "recommended_action": recommended_action,
        "action_url": action_url,
    }

class DashboardSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = request.user.restaurant
        if not restaurant:
            return Response({"error": "No restaurant associated"}, status=400)

        today = timezone.now().date()
        now = timezone.now()
        last_24h = now - timedelta(hours=24)
        last_7d = today - timedelta(days=7)

        # Week range (Mon..Sun) for OT/fatigue heuristics
        week_start = today - timedelta(days=today.weekday())
        week_end = week_start + timedelta(days=6)

        # 1. Staffing & Coverage
        # Count unique staff who clocked in today
        attendance_count = ClockEvent.objects.filter(
            staff__restaurant=restaurant,
            event_type__in=['in', 'CLOCK_IN'],
            timestamp__date=today
        ).values('staff').distinct().count()

        active_shifts_count = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date=today,
            status='IN_PROGRESS'
        ).count()
        
        no_shows_count = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date=today,
            status='NO_SHOW'
        ).count()

        # Potential no-shows: shift started, grace period passed, no clock-in yet (not yet marked NO_SHOW)
        grace_min = 10
        potential_no_shows = 0
        for s in AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date=today,
            status__in=['SCHEDULED', 'CONFIRMED'],
            staff__isnull=False
        ).select_related('staff'):
            if s.start_time and s.start_time <= now - timedelta(minutes=grace_min):
                ev = ClockEvent.objects.filter(
                    staff_id=s.staff_id,
                    event_type__in=['in', 'CLOCK_IN'],
                    timestamp__date=today
                ).exists()
                if not ev:
                    potential_no_shows += 1
        total_no_shows = no_shows_count + potential_no_shows

        # Shifts needing coverage: scheduled shifts with no assigned staff members
        shift_gaps_count = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date=today,
            status__in=['SCHEDULED', 'CONFIRMED']
        ).filter(
            Q(staff__isnull=True) & Q(staff_members__isnull=True)
        ).distinct().count()

        # OT Risk: staff exceeding standard weekly hours (uses labor compliance when available, else ClockEvent fallback)
        ot_risk_count = 0
        ot_risk_staff = []
        try:
            from reporting.services_labor import overtime_and_compliance
            compliance = overtime_and_compliance(restaurant, week_start, week_end)
            ot_risk_staff = compliance.get('overtime_incidents', [])[:5]
            ot_risk_count = len(compliance.get('overtime_incidents', []))
        except Exception:
            # Fallback: weekly hours from AssignedShift planned duration (less accurate)
            try:
                week_shifts = AssignedShift.objects.filter(
                    schedule__restaurant=restaurant,
                    shift_date__gte=week_start,
                    shift_date__lte=week_end,
                    status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED'],
                    staff__isnull=False
                ).select_related('staff')
                from reporting.models import LaborPolicy
                policy = LaborPolicy.objects.filter(restaurant=restaurant).first()
                max_week = float(getattr(policy, 'overtime_after_hours_per_week', None) or 40)
                hours_by_staff = {}
                for s in week_shifts:
                    sid = str(getattr(s, 'staff_id', None))
                    if not sid:
                        continue
                    try:
                        hrs = float(getattr(s, 'actual_hours', 0) or 0)
                        hours_by_staff[sid] = hours_by_staff.get(sid, 0.0) + hrs
                    except Exception:
                        continue
                ot_risk_count = sum(1 for _sid, hrs in hours_by_staff.items() if hrs >= max_week)
                for sid, hrs in hours_by_staff.items():
                    if hrs >= max_week and len(ot_risk_staff) < 5:
                        u = CustomUser.objects.filter(id=sid, restaurant=restaurant).first()
                        ot_risk_staff.append({'staff_id': sid, 'staff_name': _staff_name(u) if u else sid, 'hours': round(hrs, 2), 'threshold': max_week})
            except Exception:
                pass

        # Late staff today: staff with shifts today who clocked in late or missed clock-in
        late_staff_today = []
        try:
            from reporting.models import LaborPolicy
            policy = LaborPolicy.objects.filter(restaurant=restaurant).first()
            late_min = int(getattr(policy, 'late_threshold_minutes', 15) or 15)
            today_staff_shifts = AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
                status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED'],
                staff__isnull=False
            ).select_related('staff')
            for s in today_staff_shifts:
                if not s.staff or not s.start_time:
                    continue
                shift_start = s.start_time
                if timezone.is_naive(shift_start):
                    shift_start = timezone.make_aware(shift_start)
                if shift_start > now:
                    continue  # Future shift, not yet late
                ev = ClockEvent.objects.filter(
                    staff_id=s.staff_id,
                    event_type__in=['in', 'CLOCK_IN'],
                    timestamp__date=today
                ).order_by('timestamp').first()
                if not ev:
                    late_staff_today.append({'id': str(s.staff_id), 'name': _staff_name(s.staff), 'reason': 'missed_clock_in'})
                elif ev.timestamp > shift_start + timedelta(minutes=late_min):
                    late_staff_today.append({'id': str(s.staff_id), 'name': _staff_name(s.staff), 'reason': 'late'})
        except Exception:
            pass

        # 2. Operations & Forecast
        negative_reviews_count = ShiftReview.objects.filter(
            restaurant=restaurant,
            rating__lte=3,
            completed_at__gte=last_24h
        ).count()

        # Average rating for today/yesterday for trend
        avg_rating = ShiftReview.objects.filter(
            restaurant=restaurant,
            completed_at__gte=last_24h
        ).aggregate(Avg('rating'))['rating__avg'] or 0

        # Forecast: task completion rate today (ShiftTask)
        tasks_today = ShiftTask.objects.filter(
            shift__schedule__restaurant=restaurant,
            shift__shift_date=today
        )
        total_tasks_today = tasks_today.count()
        completed_tasks_today = tasks_today.filter(status='COMPLETED').count()
        completion_rate = (completed_tasks_today / total_tasks_today * 100) if total_tasks_today > 0 else 0

        # Next Delivery
        next_delivery = PurchaseOrder.objects.filter(
            restaurant=restaurant,
            status__in=['PENDING', 'ORDERED'],
            expected_delivery_date__gte=today
        ).order_by('expected_delivery_date').first()
        
        delivery_info = {
            "supplier": next_delivery.supplier.name if next_delivery else "None",
            "date": next_delivery.expected_delivery_date.isoformat() if next_delivery and next_delivery.expected_delivery_date else "None"
        }

        # 3. Staff Wellbeing
        # New hires in last 7 days
        new_hires_count = CustomUser.objects.filter(
            restaurant=restaurant,
            date_joined__gte=last_7d
        ).count()

        # Swap requests
        swap_requests_count = ShiftSwapRequest.objects.filter(
            shift_to_swap__schedule__restaurant=restaurant,
            status='PENDING'
        ).count()

        # Fatigue risk: staff with >= 45h scheduled this week (simple heuristic)
        risk_staff = []
        try:
            risk_ids = [sid for sid, hrs in hours_by_staff.items() if hrs >= 45.0] if 'hours_by_staff' in locals() else []
            if risk_ids:
                qs = CustomUser.objects.filter(restaurant=restaurant, id__in=risk_ids).only('id', 'first_name', 'last_name')
                for u in qs[:3]:
                    risk_staff.append({'id': str(u.id), 'name': f"{u.first_name} {u.last_name}".strip()})
        except Exception:
            risk_staff = []

        # 4. Mizan AI Insights (no inventory-based insights per constraints)
        insights: list[dict] = []

        # Pull today's shifts once for insight generation
        today_shifts = (
            AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
                status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'NO_SHOW']
            )
            .select_related('staff')
            .order_by('start_time')
        )

        # Clock events today (for attendance + geolocation compliance)
        clock_ins_today = ClockEvent.objects.filter(
            staff__restaurant=restaurant,
            event_type__in=['in', 'CLOCK_IN'],
            timestamp__date=today
        ).select_related('staff')

        clockin_by_staff: dict[str, ClockEvent] = {}
        for ev in clock_ins_today.order_by('timestamp'):
            sid = str(ev.staff_id)
            if sid not in clockin_by_staff:
                clockin_by_staff[sid] = ev

        # Critical: staff late today
        for lm in late_staff_today[:3]:
            insights.append(
                _build_insight(
                    insight_id=f"late_staff:{lm['id']}",
                    level="OPERATIONAL",
                    category="attendance",
                    urgency=_priority_score("OPERATIONAL") + 45,
                    summary=f"Staff late today: {lm['name']}" + (" (missed clock-in)" if lm.get('reason') == 'missed_clock_in' else " (clocked in late)"),
                    recommended_action="Follow up with the staff member. Consider coverage if unreachable.",
                    impacted={"staff": [{"id": lm["id"], "name": lm["name"]}]},
                    action_url="/dashboard/attendance",
                )
            )

        # Critical: no-shows today
        for s in today_shifts.filter(status='NO_SHOW')[:3]:
            staff = s.staff
            impacted = {
                "shift_id": str(s.id),
                "shift_title": (s.notes or "Shift"),
                "start_time": _safe_iso(s.start_time),
                "role": s.role,
                "staff": [{"id": str(staff.id), "name": _staff_name(staff)}] if staff else [],
            }
            insights.append(
                _build_insight(
                    insight_id=f"no_show:{s.id}",
                    level="CRITICAL",
                    category="attendance",
                    urgency=_priority_score("CRITICAL") + 50,
                    summary=f"No-show detected: {(s.notes or 'Shift')} ({_staff_name(staff) if staff else 'Unassigned'})",
                    recommended_action="Contact the staff member immediately and assign coverage if needed.",
                    impacted=impacted,
                    action_url="/dashboard/staff-scheduling",
                )
            )

        # Critical: missed clock-in (shift started, no clock-in event)
        grace_min = 10
        for s in today_shifts.filter(status__in=['SCHEDULED', 'CONFIRMED']):
            if not s.start_time:
                continue
            # If shift started more than grace minutes ago and no clock-in, flag it
            if s.start_time <= now - timedelta(minutes=grace_min):
                staff = s.staff
                if not staff:
                    continue
                ev = clockin_by_staff.get(str(staff.id))
                if not ev:
                    impacted = {
                        "shift_id": str(s.id),
                        "shift_title": (s.notes or "Shift"),
                        "start_time": _safe_iso(s.start_time),
                        "role": s.role,
                        "staff": [{"id": str(staff.id), "name": _staff_name(staff)}],
                    }
                    insights.append(
                        _build_insight(
                            insight_id=f"missed_clock_in:{s.id}",
                            level="CRITICAL",
                            category="attendance",
                            urgency=_priority_score("CRITICAL") + 40,
                            summary=f"Missed clock-in: {_staff_name(staff)} for {(s.notes or 'Shift')}",
                            recommended_action="Message the staff member to clock in now or mark as no-show if unresponsive.",
                            impacted=impacted,
                            action_url="/dashboard/attendance",
                        )
                    )

        # Operational: understaffed / uncovered shifts today
        if shift_gaps_count > 0:
            insights.append(
                _build_insight(
                    insight_id="coverage:gaps_today",
                    level="OPERATIONAL",
                    category="coverage",
                    urgency=_priority_score("OPERATIONAL") + min(100, shift_gaps_count * 10),
                    summary=f"{shift_gaps_count} shift(s) need coverage today",
                    recommended_action="Assign staff to uncovered shifts to avoid service disruption.",
                    impacted={"count": shift_gaps_count, "date": today.isoformat()},
                    action_url="/dashboard/staff-scheduling",
                )
            )

        # Operational: delayed/at-risk tasks today (ShiftTask)
        overdue_urgent = tasks_today.filter(priority='URGENT').exclude(status='COMPLETED').count()
        delayed = tasks_today.exclude(status='COMPLETED').filter(
            Q(priority__in=['HIGH', 'URGENT']) | Q(status='IN_PROGRESS')
        ).count()
        if overdue_urgent > 0:
            insights.append(
                _build_insight(
                    insight_id="tasks:urgent_overdue",
                    level="OPERATIONAL",
                    category="tasks",
                    urgency=_priority_score("OPERATIONAL") + 60,
                    summary=f"{overdue_urgent} urgent task(s) still incomplete today",
                    recommended_action="Open the task board and reassign urgent tasks to available staff.",
                    impacted={"urgent_open": overdue_urgent, "date": today.isoformat()},
                    action_url="/dashboard/processes-tasks-app",
                )
            )
        elif delayed >= 8 and completion_rate < 50:
            insights.append(
                _build_insight(
                    insight_id="tasks:completion_lag",
                    level="OPERATIONAL",
                    category="tasks",
                    urgency=_priority_score("OPERATIONAL") + 30,
                    summary=f"Task completion is lagging ({round(completion_rate, 1)}% today)",
                    recommended_action="Check blockers and redistribute workload to keep service on track.",
                    impacted={"completion_rate": round(completion_rate, 1), "open_tasks": delayed},
                    action_url="/dashboard/processes-tasks-app",
                )
            )

        # Operational: workload imbalance (open tasks per staff today)
        try:
            open_by_staff = {}
            for t in tasks_today.exclude(status='COMPLETED').select_related('assigned_to'):
                sid = str(t.assigned_to_id) if t.assigned_to_id else None
                if not sid:
                    continue
                open_by_staff[sid] = open_by_staff.get(sid, 0) + 1
            if open_by_staff:
                max_sid = max(open_by_staff, key=lambda k: open_by_staff[k])
                max_count = open_by_staff[max_sid]
                avg_count = sum(open_by_staff.values()) / max(1, len(open_by_staff))
                if max_count >= 5 and max_count >= avg_count * 2:
                    staff = CustomUser.objects.filter(id=max_sid, restaurant=restaurant).first()
                    insights.append(
                        _build_insight(
                            insight_id="tasks:workload_imbalance",
                            level="OPERATIONAL",
                            category="workload",
                            urgency=_priority_score("OPERATIONAL") + 20,
                            summary=f"Workload imbalance: {(_staff_name(staff) if staff else 'A staff member')} has {max_count} open task(s)",
                            recommended_action="Reassign some tasks to balance workload and prevent delays.",
                            impacted={"staff": [{"id": max_sid, "name": _staff_name(staff) if staff else "Staff"}], "open_tasks": max_count},
                            action_url="/dashboard/processes-tasks-app",
                        )
                    )
        except Exception:
            pass

        # Critical/Operational: unresolved incidents (SafetyConcernReport + Incident)
        open_safety = SafetyConcernReport.objects.filter(
            restaurant=restaurant,
            status__in=['REPORTED', 'UNDER_REVIEW'],
            severity__in=['HIGH', 'CRITICAL']
        ).order_by('-created_at')[:3]
        for r in open_safety:
            sev = str(r.severity).upper()
            lvl = "CRITICAL" if sev == "CRITICAL" else "OPERATIONAL"
            insights.append(
                _build_insight(
                    insight_id=f"safety:{r.id}",
                    level=lvl,
                    category="incidents",
                    urgency=_priority_score(lvl) + (80 if sev == "CRITICAL" else 40),
                    summary=f"{sev.title()} safety incident: {r.title}",
                    recommended_action="Open the incident, assign an owner, and document resolution steps.",
                    impacted={"incident_id": str(r.id), "location": r.location, "severity": sev},
                    action_url="/dashboard/analytics",
                )
            )

        open_incidents = Incident.objects.filter(
            restaurant=restaurant,
            status__in=['OPEN', 'INVESTIGATING'],
            priority__in=['HIGH', 'CRITICAL']
        ).order_by('-created_at')[:3]
        for r in open_incidents:
            sev = str(r.priority).upper()
            lvl = "CRITICAL" if sev == "CRITICAL" else "OPERATIONAL"
            insights.append(
                _build_insight(
                    insight_id=f"incident:{r.id}",
                    level=lvl,
                    category="incidents",
                    urgency=_priority_score(lvl) + (70 if sev == "CRITICAL" else 35),
                    summary=f"{sev.title()} incident open: {r.title}",
                    recommended_action="Review incident details and assign someone to resolve it.",
                    impacted={"incident_id": str(r.id), "category": r.category, "priority": sev},
                    action_url="/dashboard/analytics",
                )
            )

        # Compliance: clock-in missing geolocation
        bad_geo = []
        for sid, ev in clockin_by_staff.items():
            if ev.latitude is None or ev.longitude is None:
                bad_geo.append(ev)
        if bad_geo:
            sample = bad_geo[0]
            insights.append(
                _build_insight(
                    insight_id="compliance:clockin_missing_geo",
                    level="PREVENTIVE",
                    category="compliance",
                    urgency=_priority_score("PREVENTIVE") + min(50, len(bad_geo) * 10),
                    summary=f"{len(bad_geo)} clock-in(s) missing location verification today",
                    recommended_action="Follow up with staff to re-clock-in with location enabled if required by policy.",
                    impacted={"count": len(bad_geo)},
                    action_url="/dashboard/attendance",
                )
            )

        # Performance: repeated late arrivals (last 7 days)
        late_counts = {}
        try:
            recent_shifts = AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date__gte=last_7d,
                shift_date__lte=today,
                status__in=['COMPLETED', 'IN_PROGRESS', 'CONFIRMED', 'SCHEDULED']
            ).select_related('staff')
            # For each shift date, get first clock-in for that staff on that date
            for s in recent_shifts:
                if not s.staff_id or not s.start_time:
                    continue
                ev = ClockEvent.objects.filter(
                    staff_id=s.staff_id,
                    event_type__in=['in', 'CLOCK_IN'],
                    timestamp__date=s.shift_date
                ).order_by('timestamp').first()
                if not ev:
                    continue
                if ev.timestamp > s.start_time + timedelta(minutes=5):
                    late_counts[str(s.staff_id)] = late_counts.get(str(s.staff_id), 0) + 1
            top_late = [(sid, c) for sid, c in late_counts.items() if c >= 3]
            if top_late:
                sid, c = sorted(top_late, key=lambda x: x[1], reverse=True)[0]
                staff = CustomUser.objects.filter(id=sid, restaurant=restaurant).first()
                insights.append(
                    _build_insight(
                        insight_id=f"performance:late:{sid}",
                        level="PERFORMANCE",
                        category="attendance",
                        urgency=_priority_score("PERFORMANCE") + 20 + c * 5,
                        summary=f"Repeated late clock-ins: {(_staff_name(staff) if staff else 'Staff')} ({c} times in 7 days)",
                        recommended_action="Review schedule reliability and address punctuality with the staff member.",
                        impacted={"staff": [{"id": sid, "name": _staff_name(staff) if staff else 'Staff'}], "late_count_7d": c},
                        action_url="/dashboard/attendance",
                    )
                )
        except Exception:
            pass

        # Preventive: upcoming shifts with instructions not confirmed
        upcoming = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            start_time__gte=now,
            start_time__lte=now + timedelta(hours=2),
            status__in=['SCHEDULED', 'CONFIRMED']
        ).select_related('staff')
        pending_instr = upcoming.exclude(preparation_instructions__isnull=True).exclude(preparation_instructions__exact='').filter(is_confirmed=False)[:3]
        for s in pending_instr:
            if not s.staff:
                continue
            insights.append(
                _build_insight(
                    insight_id=f"instructions:unconfirmed:{s.id}",
                    level="PREVENTIVE",
                    category="instructions",
                    urgency=_priority_score("PREVENTIVE") + 15,
                    summary=f"Instructions not acknowledged for {(s.notes or 'Shift')} ({_staff_name(s.staff)})",
                    recommended_action="Ask staff to confirm they read the shift instructions before start.",
                    impacted={"shift_id": str(s.id), "staff": [{"id": str(s.staff_id), "name": _staff_name(s.staff)}]},
                    action_url="/dashboard/staff-scheduling",
                )
            )

        # Sort insights by urgency and return only top items to avoid overwhelming
        insights.sort(key=lambda x: int(x.get("urgency") or 0), reverse=True)
        insights_top = insights[:5]
        counts_by_level = {}
        for it in insights:
            lvl = str(it.get("level") or "OTHER").upper()
            counts_by_level[lvl] = counts_by_level.get(lvl, 0) + 1

        # 5. Tasks Due Today: ShiftTasks, Dashboard Tasks, ProcessTasks, scheduling Task (merged, prioritized)
        tasks_list = []
        # ShiftTasks from shifts today
        for t in ShiftTask.objects.filter(
            shift__schedule__restaurant=restaurant,
            shift__shift_date=today
        ).exclude(status='COMPLETED').order_by('-priority', 'created_at')[:5]:
            status_text = "OVERDUE" if t.priority == 'URGENT' and t.status != 'COMPLETED' else t.status
            tasks_list.append({"label": t.title, "status": status_text, "priority": t.priority or 'MEDIUM'})
        # Dashboard tasks due today
        for t in DashboardTask.objects.filter(restaurant=restaurant, due_date=today).exclude(status__in=['COMPLETED', 'CANCELLED']).order_by('-priority', 'created_at')[:3]:
            prio = getattr(t, 'priority', 'MEDIUM') or 'MEDIUM'
            status_text = "OVERDUE" if prio == 'HIGH' and t.status not in ('COMPLETED', 'Completed') else t.status
            tasks_list.append({"label": t.title, "status": status_text, "priority": prio})
        # Scheduling Task (from task_templates) due today
        try:
            from scheduling.task_templates import Task as SchedulingTask
            for t in SchedulingTask.objects.filter(restaurant=restaurant, due_date=today).exclude(status__in=['COMPLETED', 'CANCELLED']).order_by('-priority', 'created_at')[:3]:
                prio = getattr(t, 'priority', 'MEDIUM') or 'MEDIUM'
                tasks_list.append({"label": t.title, "status": t.status or "PENDING", "priority": prio})
        except Exception:
            pass
        # ProcessTasks due today
        try:
            from scheduling.process_models import ProcessTask
            for t in ProcessTask.objects.filter(process__restaurant=restaurant, due_date=today).exclude(status__in=['COMPLETED', 'CANCELLED']).select_related('process').order_by('-priority', 'created_at')[:3]:
                prio = getattr(t, 'priority', 'MEDIUM') or 'MEDIUM'
                tasks_list.append({"label": t.title, "status": t.status or "PENDING", "priority": prio})
        except Exception:
            pass
        # Sort by priority (URGENT > HIGH > MEDIUM > LOW) and take top 5
        prio_order = {'URGENT': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
        tasks_list.sort(key=lambda x: prio_order.get(str(x.get('priority', 'MEDIUM')).upper(), 0), reverse=True)
        tasks_list = tasks_list[:5]

        # Morning no-shows: explicit NO_SHOW with start before 12, plus potential (missed clock-in) for morning shifts
        morning_no_shows = 0
        try:
            morning_no_shows = AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
                status='NO_SHOW',
                start_time__hour__lt=12
            ).count()
            for s in AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=today,
                status__in=['SCHEDULED', 'CONFIRMED'],
                staff__isnull=False,
                start_time__hour__lt=12
            ):
                if s.start_time and s.start_time <= now - timedelta(minutes=grace_min):
                    if not ClockEvent.objects.filter(staff_id=s.staff_id, event_type__in=['in', 'CLOCK_IN'], timestamp__date=today).exists():
                        morning_no_shows += 1
        except Exception:
            morning_no_shows = total_no_shows

        data = {
            "attendance": {
                "present_count": attendance_count,
                "active_shifts": active_shifts_count,
                "no_shows": total_no_shows,
                "morning_no_shows": morning_no_shows,
                "shift_gaps": shift_gaps_count,
                "ot_risk": ot_risk_count,
                "ot_risk_staff": ot_risk_staff,
                "late_staff_today": late_staff_today
            },
            "operations": {
                "negative_reviews": negative_reviews_count,
                "avg_rating": round(avg_rating, 1),
                "completion_rate": round(completion_rate, 1),
                "next_delivery": delivery_info
            },
            "wellbeing": {
                "new_hires": new_hires_count,
                "swap_requests": swap_requests_count,
                "risk_staff": risk_staff
            },
            "insights": {
                "items": insights_top,
                "counts": counts_by_level,
            },
            "tasks_due": tasks_list,
            "date": today.isoformat()
        }
        
        return Response(data)
