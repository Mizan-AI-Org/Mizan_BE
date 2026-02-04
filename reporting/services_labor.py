"""
Labor analytics: real labor cost, planned vs actual, compliance, sales → labor recommendation.
All computations are additive and do not change existing data.
"""
from datetime import datetime, timedelta
from decimal import Decimal
from django.db.models import Sum, Q
from django.utils import timezone

from accounts.models import Restaurant, CustomUser
from scheduling.models import AssignedShift, Timesheet
from timeclock.models import ClockEvent
from reporting.models import LaborBudget, LaborPolicy


def get_staff_hourly_rate(user):
    """Resolve hourly rate from profile or timesheet default."""
    if hasattr(user, 'profile') and user.profile and getattr(user.profile, 'hourly_rate', None):
        rate = user.profile.hourly_rate
        if rate is not None and rate > 0:
            return float(rate)
    try:
        from staff.models import StaffProfile
        sp = StaffProfile.objects.filter(user=user).first()
        if sp and getattr(sp, 'hourly_rate', None) and sp.hourly_rate > 0:
            return float(sp.hourly_rate)
    except Exception:
        pass
    return 15.0


def labor_cost_from_real_data(restaurant, start_date, end_date):
    """
    Compute labor cost from timesheets (preferred) and from clock events + hourly rate.
    Returns: total_hours, total_cost, by_staff, by_role, currency.
    """
    currency = getattr(restaurant, 'currency', 'USD') or 'USD'
    # 1) From Timesheets (approved/submitted/paid)
    timesheets = Timesheet.objects.filter(
        restaurant=restaurant,
        start_date__lte=end_date,
        end_date__gte=start_date,
        status__in=['SUBMITTED', 'APPROVED', 'PAID']
    )
    ts_hours = timesheets.aggregate(Sum('total_hours'))['total_hours__sum'] or Decimal('0')
    ts_earnings = timesheets.aggregate(Sum('total_earnings'))['total_earnings__sum'] or Decimal('0')
    total_hours = float(ts_hours)
    total_cost = float(ts_earnings)

    # 2) If no timesheets, fall back to AssignedShift actual_hours * profile hourly_rate
    if total_hours == 0 and total_cost == 0:
        shifts = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date__gte=start_date,
            shift_date__lte=end_date,
            status__in=['COMPLETED', 'CONFIRMED', 'IN_PROGRESS']
        ).select_related('staff')
        by_staff = {}
        by_role = {}
        for s in shifts:
            staff = s.staff
            if not staff:
                continue
            hrs = getattr(s, 'actual_hours', 0) or 0
            if not isinstance(hrs, (int, float)):
                hrs = float(hrs)
            rate = get_staff_hourly_rate(staff)
            cost = hrs * rate
            total_hours += hrs
            total_cost += cost
            sid = str(staff.id)
            by_staff[sid] = by_staff.get(sid, {'hours': 0, 'cost': 0, 'name': f'{staff.first_name} {staff.last_name}'})
            by_staff[sid]['hours'] += hrs
            by_staff[sid]['cost'] += cost
            role = getattr(s, 'role', '') or 'Other'
            by_role[role] = by_role.get(role, {'hours': 0, 'cost': 0})
            by_role[role]['hours'] += hrs
            by_role[role]['cost'] += cost
        return {
            'total_hours': round(total_hours, 2),
            'total_cost': round(total_cost, 2),
            'by_staff': by_staff,
            'by_role': by_role,
            'currency': currency,
            'source': 'shifts',
        }

    by_staff = {}
    by_role = {}
    for ts in timesheets:
        sid = str(ts.staff_id)
        name = f'{ts.staff.first_name} {ts.staff.last_name}' if ts.staff else sid
        by_staff[sid] = {'hours': float(ts.total_hours), 'cost': float(ts.total_earnings), 'name': name}
        # Role from recent shift if needed
        recent = AssignedShift.objects.filter(staff=ts.staff, shift_date__gte=start_date, shift_date__lte=end_date).first()
        role = getattr(recent, 'role', '') or 'Other'
        by_role[role] = by_role.get(role, {'hours': 0, 'cost': 0})
        by_role[role]['hours'] += float(ts.total_hours)
        by_role[role]['cost'] += float(ts.total_earnings)

    return {
        'total_hours': round(total_hours, 2),
        'total_cost': round(total_cost, 2),
        'by_staff': by_staff,
        'by_role': by_role,
        'currency': currency,
        'source': 'timesheets',
    }


def labor_budget_for_period(restaurant, start_date, end_date):
    """Get labor budget for period if set."""
    budget = LaborBudget.objects.filter(
        restaurant=restaurant,
        period_start__lte=end_date,
        period_end__gte=start_date
    ).order_by('-period_end').first()
    if budget:
        return {
            'target_hours': float(budget.target_hours) if budget.target_hours else None,
            'target_amount': float(budget.target_amount) if budget.target_amount else None,
            'currency': budget.currency or 'USD',
        }
    return None


def planned_vs_actual_hours(restaurant, start_date, end_date):
    """
    Planned hours from AssignedShift (scheduled), actual from ClockEvent (clock in/out).
    Returns: summary + list of staff with planned_hours, actual_hours, variance, late_count, no_show_count.
    """
    from collections import defaultdict
    planned_by_staff = defaultdict(float)
    shifts_by_staff = defaultdict(list)
    for s in AssignedShift.objects.filter(
        schedule__restaurant=restaurant,
        shift_date__gte=start_date,
        shift_date__lte=end_date,
        status__in=['SCHEDULED', 'CONFIRMED', 'COMPLETED', 'IN_PROGRESS']
    ).select_related('staff'):
        if not s.staff:
            continue
        hrs = getattr(s, 'actual_hours', 0) or 0
        if not isinstance(hrs, (int, float)):
            hrs = float(hrs)
        planned_by_staff[str(s.staff_id)] += hrs
        shifts_by_staff[str(s.staff_id)].append(s)

    # Actual from clock events: pair in/out per day
    actual_by_staff = defaultdict(float)
    events = ClockEvent.objects.filter(
        staff__restaurant=restaurant,
        timestamp__date__gte=start_date,
        timestamp__date__lte=end_date
    ).order_by('staff_id', 'timestamp').values('staff_id', 'event_type', 'timestamp')
    current_in = {}
    for e in events:
        sid = str(e['staff_id'])
        if e['event_type'] == 'in':
            current_in[sid] = e['timestamp']
        elif e['event_type'] == 'out' and sid in current_in:
            delta = e['timestamp'] - current_in[sid]
            actual_by_staff[sid] += delta.total_seconds() / 3600
            del current_in[sid]

    policy = getattr(restaurant, 'labor_policy', None) or None
    if not policy:
        try:
            policy = LaborPolicy.objects.filter(restaurant=restaurant).first()
        except Exception:
            policy = None
    late_minutes = getattr(policy, 'late_threshold_minutes', 15) if policy else 15

    result = []
    all_staff_ids = set(planned_by_staff.keys()) | set(actual_by_staff.keys())
    total_planned = 0
    total_actual = 0
    late_count = 0
    no_show_count = 0
    for sid in all_staff_ids:
        planned = planned_by_staff.get(sid, 0)
        actual = actual_by_staff.get(sid, 0)
        total_planned += planned
        total_actual += actual
        variance = round(actual - planned, 2)
        shifts = shifts_by_staff.get(sid, [])
        staff_late = 0
        staff_no_show = 0
        for sh in shifts:
            if not sh.staff:
                continue
            shift_start = sh.start_time
            if shift_start is None:
                continue
            if hasattr(shift_start, 'date'):
                shift_dt = shift_start
            else:
                shift_dt = timezone.make_aware(timezone.datetime.combine(sh.shift_date, shift_start)) if shift_start else None
            if not shift_dt:
                continue
            first_clock = ClockEvent.objects.filter(
                staff=sh.staff,
                event_type='in',
                timestamp__date=sh.shift_date
            ).order_by('timestamp').values_list('timestamp', flat=True).first()
            if not first_clock:
                staff_no_show += 1
                no_show_count += 1
                continue
            if first_clock > shift_dt + timedelta(minutes=late_minutes):
                staff_late += 1
                late_count += 1
        try:
            user = CustomUser.objects.get(id=sid)
            name = f'{user.first_name} {user.last_name}'.strip() or user.email
        except Exception:
            name = sid
        result.append({
            'staff_id': sid,
            'staff_name': name,
            'planned_hours': round(planned, 2),
            'actual_hours': round(actual, 2),
            'variance': variance,
            'late_count': staff_late,
            'no_show_count': staff_no_show,
        })
    return {
        'summary': {
            'total_planned_hours': round(total_planned, 2),
            'total_actual_hours': round(total_actual, 2),
            'total_variance': round(total_actual - total_planned, 2),
            'late_arrivals': late_count,
            'no_shows': no_show_count,
        },
        'by_staff': result,
    }


def overtime_and_compliance(restaurant, start_date, end_date):
    """
    Detect overtime (hours > policy.overtime_after_hours_per_week) and return compliance summary.
    """
    policy = None
    try:
        policy = LaborPolicy.objects.get(restaurant=restaurant)
    except (LaborPolicy.DoesNotExist, Exception):
        policy = None
    max_week = float(getattr(policy, 'overtime_after_hours_per_week', None) or 40)
    max_day = float(getattr(policy, 'max_hours_per_day', None) or 8) if policy else 8

    # Weekly hours per staff from clock or timesheet
    from collections import defaultdict
    weekly_hours = defaultdict(lambda: defaultdict(float))
    for ts in Timesheet.objects.filter(
        restaurant=restaurant,
        start_date__lte=end_date,
        end_date__gte=start_date,
        status__in=['SUBMITTED', 'APPROVED', 'PAID']
    ):
        sid = str(ts.staff_id)
        w = ts.start_date.isocalendar()[1]
        weekly_hours[sid][w] += float(ts.total_hours)
    overtime_staff = []
    for sid, weeks in weekly_hours.items():
        for week_num, hrs in weeks.items():
            if hrs > max_week:
                try:
                    user = CustomUser.objects.get(id=sid)
                    name = f'{user.first_name} {user.last_name}'.strip() or user.email
                except Exception:
                    name = sid
                overtime_staff.append({'staff_id': sid, 'staff_name': name, 'week': week_num, 'hours': round(hrs, 2), 'threshold': max_week})
    return {
        'overtime_threshold_hours_per_week': max_week,
        'overtime_incidents': overtime_staff,
    }


def certifications_expiring(restaurant, within_days=30):
    """Staff with certifications expiring within N days. StaffProfile.certifications is JSON list of {name, expiry}."""
    result = []
    try:
        from staff.models import StaffProfile
        for sp in StaffProfile.objects.filter(user__restaurant=restaurant).select_related('user'):
            certs = getattr(sp, 'certifications', None) or []
            if not isinstance(certs, list):
                continue
            for c in certs:
                if not isinstance(c, dict):
                    continue
                expiry = c.get('expiry') or c.get('expiry_date')
                if not expiry:
                    continue
                try:
                    if isinstance(expiry, str):
                        exp_date = datetime.strptime(expiry[:10], '%Y-%m-%d').date()
                    else:
                        exp_date = expiry
                except Exception:
                    continue
                if timezone.now().date() <= exp_date <= (timezone.now().date() + timedelta(days=within_days)):
                    result.append({
                        'staff_id': str(sp.user_id),
                        'staff_name': f'{sp.user.first_name} {sp.user.last_name}'.strip() or sp.user.email,
                        'certification_name': c.get('name', 'Certificate'),
                        'expiry_date': exp_date.isoformat(),
                    })
    except Exception:
        pass
    return result


def sales_labor_recommendation(restaurant, week_start=None):
    """
    Recommended labor budget from sales forecast and labor_target_percent.
    Uses AIScheduler demand forecast and POS/sales data if available.
    """
    from scheduling.ai_scheduler import AIScheduler
    target_pct = getattr(restaurant, 'labor_target_percent', None)
    if target_pct is None:
        target_pct = Decimal('30')
    target_pct = float(target_pct)
    if week_start is None:
        week_start = timezone.now().date()
    # Align to Monday
    if week_start.weekday() != 0:
        week_start = week_start - timedelta(days=week_start.weekday())
    scheduler = AIScheduler(restaurant)
    demand = scheduler._get_demand_forecast(week_start)
    # Estimate revenue for week from POS or historical
    try:
        from pos.models import Order
        from django.db.models import Sum
        week_end = week_start + timedelta(days=6)
        agg = Order.objects.filter(
            restaurant=restaurant,
            order_time__date__gte=week_start,
            order_time__date__lte=week_end,
            status='COMPLETED'
        ).aggregate(s=Sum('total_amount'))
        total_revenue = float(agg['s'] or 0)
    except Exception:
        total_revenue = 0
    # If no historical revenue, use demand level heuristic
    if not total_revenue:
        high_days = sum(1 for v in demand.values() if v == 'HIGH')
        med_days = sum(1 for v in demand.values() if v == 'MEDIUM')
        # Rough estimate: HIGH ~2000, MEDIUM ~1200, LOW ~600 per day
        total_revenue = high_days * 2000 + med_days * 1200 + (7 - high_days - med_days) * 600
    recommended_labor = (float(total_revenue) * target_pct / 100) if total_revenue else 0
    return {
        'week_start': week_start.isoformat(),
        'week_end': (week_start + timedelta(days=6)).isoformat(),
        'demand_forecast': demand,
        'estimated_revenue': round(float(total_revenue), 2),
        'labor_target_percent': target_pct,
        'recommended_labor_budget': round(recommended_labor, 2),
        'currency': getattr(restaurant, 'currency', 'USD') or 'USD',
    }
