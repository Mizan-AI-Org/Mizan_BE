from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.utils import timezone
from django.db.models import Count, Q
from timeclock.models import ClockEvent
from scheduling.models import AssignedShift, ShiftSwapRequest
from dashboard.models import Task, Alert

class DashboardSummaryView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = request.user.restaurant
        today = timezone.now().date()
        now = timezone.now()

        # 1. Attendance & Staffing
        # Count unique staff who clocked in today
        attendance_count = ClockEvent.objects.filter(
            staff__restaurant=restaurant,
            event_type='in',
            timestamp__date=today
        ).values('staff').distinct().count()

        # Count staff currently on break
        # Logic: Get latest clock event for each staff today. If it's 'break_start', they are on break.
        # This is a bit expensive, simplified version:
        # We can look at `ClockEvent` for today.
        # Ideally, we should use a cached status, but for now we iterate recent events if needed.
        # For efficiency, let's just count 'break_start' without corresponding 'break_end' might be tricky.
        # Alternative: The existing StaffDashboard logic checks `is_on_break`.
        # Let's try query: Staff with 'break_start' today and NO 'break_end' or 'out' LATER than that break_start.
        # Simplified for MVP: Just count active shifts status first.
        
        # Staff on break (simplified approximation or skip if too complex for single query)
        # Let's stick to simple counters first.
        
        # 2. Shifts
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

        # 3. Tasks
        pending_tasks = Task.objects.filter(
            restaurant=restaurant,
            status='PENDING'
        )
        pending_tasks_count = pending_tasks.count()
        overdue_tasks_count = pending_tasks.filter(due_date__lt=today).count()

        # 4. Swap Requests
        swap_requests_count = ShiftSwapRequest.objects.filter(
            shift_to_swap__schedule__restaurant=restaurant,
            status='PENDING'
        ).count()
        
        # 5. Alerts/Insights (using Alerts model or hardcoded for now?)
        # User requirement mentioned "/api/dashboard/analytics/insights/". 
        # We can return a simple list here or rely on the frontend to call the other endpoint.
        # Let's return counts.

        data = {
            "attendance": {
                "present_count": attendance_count,
                "active_shifts": active_shifts_count,
                "no_shows": no_shows_count,
                # "on_break": on_break_count # TODO if needed
            },
            "tasks": {
                "pending": pending_tasks_count,
                "overdue": overdue_tasks_count
            },
            "requests": {
                "swaps_pending": swap_requests_count
            },
            "date": today.isoformat()
        }
        
        return Response(data)
