"""
Enhanced Scheduling Views with AI-powered scheduling
"""
from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from datetime import datetime, timedelta

from .models import WeeklySchedule, AssignedShift, ShiftTask
from .serializers import (
    WeeklyScheduleSerializer, AssignedShiftSerializer,
    ShiftTaskSerializer, AIScheduleRequestSerializer
)
from .ai_scheduler import AIScheduler
from .services import SchedulingService
from core.permissions import IsRestaurantOwnerOrManager
import logging

logger = logging.getLogger(__name__)


class EnhancedSchedulingViewSet(viewsets.ModelViewSet):
    """
    Enhanced scheduling with AI-powered features
    
    Endpoints:
    - POST /api/schedules/generate_ai/ - Generate AI-powered schedule
    - GET /api/schedules/{id}/calendar_view/ - Get calendar-formatted data
    - POST /api/schedules/{id}/auto_assign_tasks/ - Auto-assign tasks to shifts
    - GET /api/schedules/demand_forecast/ - Get demand forecast
    """
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [IsAuthenticated, IsRestaurantOwnerOrManager]
    
    def get_queryset(self):
        user = self.request.user
        if not hasattr(user, 'restaurant') or not user.restaurant:
            return WeeklySchedule.objects.none()
        
        return WeeklySchedule.objects.filter(restaurant=user.restaurant)
    
    @action(detail=False, methods=['post'])
    def generate_ai(self, request):
        """
        Generate AI-powered optimal schedule
        
        Request body:
        {
            "week_start": "2024-01-15",
            "labor_budget": 5000.00,
            "demand_override": {
                "Monday": "HIGH",
                "Tuesday": "MEDIUM"
            }
        }
        """
        serializer = AIScheduleRequestSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        week_start = serializer.validated_data['week_start']
        labor_budget = serializer.validated_data.get('labor_budget')
        demand_override = serializer.validated_data.get('demand_override')
        
        # Initialize AI scheduler
        ai_scheduler = AIScheduler(restaurant=request.user.restaurant)
        
        # Generate schedule
        try:
            result = ai_scheduler.generate_optimal_schedule(
                week_start=week_start,
                demand_forecast=demand_override,
                labor_budget=labor_budget
            )
            
            # Create weekly schedule
            week_end = week_start + timedelta(days=6)
            schedule = WeeklySchedule.objects.create(
                restaurant=request.user.restaurant,
                week_start=week_start,
                week_end=week_end,
                is_published=False
            )
            
            # Create assigned shifts
            created_shifts = []
            for shift_data in result['shifts']:
                shift = AssignedShift.objects.create(
                    schedule=schedule,
                    staff_id=shift_data['staff_id'],
                    shift_date=shift_data['shift_date'],
                    start_time=shift_data['start_time'],
                    end_time=shift_data['end_time'],
                    role=shift_data['role'],
                    status='SCHEDULED'
                )
                created_shifts.append(shift)
            
            return Response({
                'detail': 'AI schedule generated successfully',
                'schedule': WeeklyScheduleSerializer(schedule).data,
                'shifts': AssignedShiftSerializer(created_shifts, many=True).data,
                'analytics': {
                    'total_hours': result['total_hours'],
                    'estimated_cost': result['estimated_cost'],
                    'coverage_score': result['coverage_score'],
                    'warnings': result['warnings']
                },
                'demand_forecast': result['demand_forecast']
            }, status=status.HTTP_201_CREATED)
        
        except Exception as e:
            logger.error(f"Error generating AI schedule: {str(e)}")
            return Response(
                {'detail': f'Error generating schedule: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=True, methods=['get'])
    def calendar_view(self, request, pk=None):
        """
        Get schedule data formatted for calendar display
        
        Returns:
        {
            "events": [
                {
                    "id": "shift-uuid",
                    "title": "John Doe - Chef",
                    "start": "2024-01-15T10:00:00",
                    "end": "2024-01-15T18:00:00",
                    "color": "#3B82F6",
                    "staff_id": "uuid",
                    "role": "CHEF",
                    "status": "SCHEDULED"
                }
            ]
        }
        """
        schedule = self.get_object()
        shifts = AssignedShift.objects.filter(schedule=schedule).select_related('staff')
        
        # Color mapping for roles
        role_colors = {
            'SUPER_ADMIN': '#8B5CF6',
            'ADMIN': '#6366F1',
            'CHEF': '#EF4444',
            'WAITER': '#10B981',
            'CLEANER': '#F59E0B',
            'CASHIER': '#3B82F6',
        }
        
        events = []
        for shift in shifts:
            # Combine date and time
            start_datetime = datetime.combine(shift.shift_date, shift.start_time)
            end_datetime = datetime.combine(shift.shift_date, shift.end_time)
            
            events.append({
                'id': str(shift.id),
                'title': f"{shift.staff.first_name} {shift.staff.last_name} - {shift.get_role_display()}",
                'start': start_datetime.isoformat(),
                'end': end_datetime.isoformat(),
                'color': role_colors.get(shift.role, '#6B7280'),
                'staff_id': str(shift.staff.id),
                'staff_name': f"{shift.staff.first_name} {shift.staff.last_name}",
                'role': shift.role,
                'status': shift.status,
                'is_confirmed': shift.is_confirmed,
                'notes': shift.notes
            })
        
        return Response({'events': events})
    
    @action(detail=True, methods=['post'])
    def auto_assign_tasks(self, request, pk=None):
        """
        Automatically assign tasks to all shifts in schedule based on AI suggestions
        """
        schedule = self.get_object()
        shifts = AssignedShift.objects.filter(schedule=schedule)
        
        ai_scheduler = AIScheduler(restaurant=request.user.restaurant)
        
        created_tasks = []
        for shift in shifts:
            # Get task suggestions
            suggestions = ai_scheduler.suggest_task_assignments(shift)
            
            # Create tasks
            for suggestion in suggestions:
                task = ShiftTask.objects.create(
                    shift=shift,
                    title=suggestion['title'],
                    priority=suggestion['priority'],
                    estimated_duration=suggestion['estimated_duration'],
                    assigned_to=shift.staff,
                    created_by=request.user,
                    status='TODO'
                )
                created_tasks.append(task)
        
        return Response({
            'detail': f'Created {len(created_tasks)} tasks',
            'tasks': ShiftTaskSerializer(created_tasks, many=True).data
        })
    
    @action(detail=False, methods=['get'])
    def demand_forecast(self, request):
        """
        Get demand forecast for upcoming weeks
        
        Query params:
        - weeks: Number of weeks to forecast (default: 2)
        """
        weeks = int(request.query_params.get('weeks', 2))
        
        ai_scheduler = AIScheduler(restaurant=request.user.restaurant)
        
        forecasts = []
        today = timezone.now().date()
        
        for week_num in range(weeks):
            week_start = today + timedelta(weeks=week_num)
            # Adjust to Monday
            week_start = week_start - timedelta(days=week_start.weekday())
            
            forecast = ai_scheduler._get_demand_forecast(week_start)
            
            forecasts.append({
                'week_start': week_start.isoformat(),
                'week_end': (week_start + timedelta(days=6)).isoformat(),
                'forecast': forecast
            })
        
        return Response({'forecasts': forecasts})
    
    @action(detail=True, methods=['get'])
    def conflict_report(self, request, pk=None):
        """
        Get detailed conflict report for schedule
        """
        schedule = self.get_object()
        shifts = AssignedShift.objects.filter(schedule=schedule).select_related('staff')
        
        conflicts = []
        warnings = []
        
        # Check for overlapping shifts
        for shift in shifts:
            overlaps = SchedulingService.detect_scheduling_conflicts(
                str(shift.staff.id),
                shift.shift_date,
                shift.start_time,
                shift.end_time
            )
            
            if overlaps:
                conflicts.append({
                    'shift_id': str(shift.id),
                    'staff_name': f"{shift.staff.first_name} {shift.staff.last_name}",
                    'date': shift.shift_date.isoformat(),
                    'conflicts': overlaps
                })
        
        # Check weekly hours
        staff_hours = {}
        for shift in shifts:
            staff_id = str(shift.staff.id)
            if staff_id not in staff_hours:
                staff_hours[staff_id] = {
                    'name': f"{shift.staff.first_name} {shift.staff.last_name}",
                    'hours': 0
                }
            staff_hours[staff_id]['hours'] += shift.actual_hours
        
        for staff_id, data in staff_hours.items():
            if data['hours'] > 48:
                warnings.append({
                    'type': 'EXCESSIVE_HOURS',
                    'staff_name': data['name'],
                    'hours': round(data['hours'], 2),
                    'message': f"{data['name']} scheduled for {data['hours']:.1f} hours (max 48)"
                })
        
        return Response({
            'conflicts': conflicts,
            'warnings': warnings,
            'has_issues': len(conflicts) > 0 or len(warnings) > 0
        })


class CalendarAPIViewSet(viewsets.ViewSet):
    """
    Calendar-specific API endpoints
    """
    permission_classes = [IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def my_shifts(self, request):
        """
        Get current user's shifts in calendar format
        
        Query params:
        - start_date: Start date (YYYY-MM-DD)
        - end_date: End date (YYYY-MM-DD)
        """
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        if not start_date or not end_date:
            return Response(
                {'detail': 'start_date and end_date are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Invalid date format. Use YYYY-MM-DD'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        shifts = AssignedShift.objects.filter(
            staff=request.user,
            shift_date__gte=start_date,
            shift_date__lte=end_date
        ).select_related('schedule')
        
        events = []
        for shift in shifts:
            try:
                if isinstance(shift.start_time, datetime):
                    start_datetime = shift.start_time
                else:
                    start_datetime = timezone.datetime.combine(shift.shift_date, shift.start_time)
                if isinstance(shift.end_time, datetime):
                    end_datetime = shift.end_time
                else:
                    end_datetime = timezone.datetime.combine(shift.shift_date, shift.end_time)
            except Exception:
                start_datetime = datetime.combine(shift.shift_date, getattr(shift.start_time, 'time', shift.start_time))
                end_datetime = datetime.combine(shift.shift_date, getattr(shift.end_time, 'time', shift.end_time))

            if end_datetime < start_datetime:
                end_datetime += timezone.timedelta(days=1)
            
            # Get tasks for this shift
            tasks = ShiftTask.objects.filter(shift=shift)
            
            events.append({
                'id': str(shift.id),
                'title': f"{shift.get_role_display()} Shift",
                'start': start_datetime.isoformat(),
                'end': end_datetime.isoformat(),
                'status': shift.status,
                'is_confirmed': shift.is_confirmed,
                'notes': shift.notes,
                'tasks_count': tasks.count(),
                'tasks_completed': tasks.filter(status='COMPLETED').count()
            })
        
        return Response({'events': events})
    
    @action(detail=False, methods=['get'])
    def team_availability(self, request):
        """
        Get team availability for a specific date range
        """
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        if not start_date or not end_date:
            return Response(
                {'detail': 'start_date and end_date are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Invalid date format. Use YYYY-MM-DD'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Get all staff
        from accounts.models import CustomUser
        staff = CustomUser.objects.filter(
            restaurant=request.user.restaurant,
            is_active=True
        )
        
        availability = []
        for member in staff:
            # Get shifts for this staff member
            shifts = AssignedShift.objects.filter(
                staff=member,
                shift_date__gte=start_date,
                shift_date__lte=end_date
            )
            
            total_hours = sum(shift.actual_hours for shift in shifts)
            
            availability.append({
                'staff_id': str(member.id),
                'name': f"{member.first_name} {member.last_name}",
                'role': member.role,
                'shifts_count': shifts.count(),
                'total_hours': round(total_hours, 2),
                'availability_score': min(100, (48 - total_hours) / 48 * 100)  # Based on 48-hour week
            })
        
        return Response({'availability': availability})