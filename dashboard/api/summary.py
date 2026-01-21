from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.utils import timezone
from datetime import timedelta
from django.db.models import Count, Q, Avg, F
from timeclock.models import ClockEvent
from scheduling.models import AssignedShift, ShiftSwapRequest, ShiftTask
from attendance.models import ShiftReview
from dashboard.models import Task, Alert
from accounts.models import CustomUser
from inventory.models import InventoryItem, PurchaseOrder

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

        shift_gaps_count = AssignedShift.objects.filter(
            schedule__restaurant=restaurant,
            shift_date=today,
            status='SCHEDULED'
        ).count()

        # OT Risk (Simplified: staff with > 40h this week)
        # We'd need to sum actual hours from AssignedShift for the current week.
        # For now, let's keep it as 0 or a simple placeholder if too complex for a single view.
        ot_risk_count = 0 

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

        # Forecast: Simple task completion rate today
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

        # 4. Mizan AI Insights
        # Low stock items
        low_stock_items = InventoryItem.objects.filter(
            restaurant=restaurant,
            current_stock__lte=F('reorder_level'),
            is_active=True
        ).values('name', 'current_stock', 'unit')[:3]

        # 5. Tasks Due Today (First 3 for dashboard)
        tasks_due = ShiftTask.objects.filter(
            shift__schedule__restaurant=restaurant,
            shift__shift_date=today
        ).order_by('priority', 'created_at')[:3]
        
        tasks_list = []
        for t in tasks_due:
            status_text = "OVERDUE" if t.status != 'COMPLETED' and t.priority == 'URGENT' else t.status
            tasks_list.append({
                "label": t.title,
                "status": status_text,
                "priority": t.priority
            })

        data = {
            "attendance": {
                "present_count": attendance_count,
                "active_shifts": active_shifts_count,
                "no_shows": no_shows_count,
                "shift_gaps": shift_gaps_count,
                "ot_risk": ot_risk_count
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
                "risk_staff": [] # Add logic if needed
            },
            "insights": {
                "low_stock": list(low_stock_items),
                "understaffing_risk": shift_gaps_count > 0
            },
            "tasks_due": tasks_list,
            "date": today.isoformat()
        }
        
        return Response(data)
