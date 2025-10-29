"""
Scheduling service layer - contains business logic for scheduling operations
"""
from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple
from django.db.models import Q, Count, Avg
from django.utils import timezone
from .models import AssignedShift, WeeklySchedule, ScheduleTemplate, TemplateShift
from accounts.models import CustomUser


class SchedulingService:
    """Service for managing scheduling operations"""
    
    @staticmethod
    def get_staff_coverage(schedule_id: str, role: str = None) -> Dict:
        """
        Calculate staff coverage for a schedule
        
        Returns:
            {
                'total_required': int,
                'total_assigned': int,
                'coverage_percentage': float,
                'uncovered_shifts': int,
                'by_day': {date: {'required': int, 'assigned': int}}
            }
        """
        try:
            schedule = WeeklySchedule.objects.get(id=schedule_id)
        except WeeklySchedule.DoesNotExist:
            return {'error': 'Schedule not found'}
        
        # Get all shifts for this schedule
        shifts = AssignedShift.objects.filter(
            schedule=schedule,
            status__in=['SCHEDULED', 'CONFIRMED']
        )
        
        if role:
            shifts = shifts.filter(role=role)
        
        # Group by day
        coverage_by_day = {}
        total_assigned = 0
        
        for shift in shifts:
            day = shift.shift_date
            if day not in coverage_by_day:
                coverage_by_day[day] = {'shifts': [], 'assigned': 0}
            
            coverage_by_day[day]['shifts'].append(shift)
            coverage_by_day[day]['assigned'] += 1
            total_assigned += 1
        
        # Calculate coverage from template if available
        total_required = 0
        for day_shift in shifts.values('shift_date').distinct():
            total_required += 1
        
        coverage_percentage = (total_assigned / total_required * 100) if total_required > 0 else 0
        
        return {
            'total_required': total_required,
            'total_assigned': total_assigned,
            'coverage_percentage': round(coverage_percentage, 2),
            'uncovered_shifts': max(0, total_required - total_assigned),
            'by_day': coverage_by_day
        }
    
    @staticmethod
    def detect_scheduling_conflicts(staff_id: str, shift_date, start_time, end_time) -> List[Dict]:
        """
        Detect scheduling conflicts for a staff member
        
        Returns:
            List of conflicting shifts
        """
        conflicts = []
        
        try:
            staff = CustomUser.objects.get(id=staff_id)
        except CustomUser.DoesNotExist:
            return conflicts
        
        # Find overlapping shifts
        existing_shifts = AssignedShift.objects.filter(
            staff=staff,
            shift_date=shift_date,
            status__in=['SCHEDULED', 'CONFIRMED']
        )
        
        shift_start = timezone.datetime.combine(shift_date, start_time)
        shift_end = timezone.datetime.combine(shift_date, end_time)
        
        for existing in existing_shifts:
            existing_start = timezone.datetime.combine(existing.shift_date, existing.start_time)
            existing_end = timezone.datetime.combine(existing.shift_date, existing.end_time)
            
            if shift_start < existing_end and shift_end > existing_start:
                conflicts.append({
                    'shift_id': str(existing.id),
                    'start_time': str(existing.start_time),
                    'end_time': str(existing.end_time),
                    'role': existing.role,
                    'status': existing.status
                })
        
        return conflicts
    
    @staticmethod
    def calculate_staff_hours(staff_id: str, start_date, end_date) -> Dict:
        """
        Calculate total working hours for a staff member in date range
        
        Returns:
            {
                'total_hours': float,
                'by_role': {role: float},
                'by_week': {week: float},
                'shifts_count': int
            }
        """
        try:
            staff = CustomUser.objects.get(id=staff_id)
        except CustomUser.DoesNotExist:
            return {'error': 'Staff not found'}
        
        shifts = AssignedShift.objects.filter(
            staff=staff,
            shift_date__gte=start_date,
            shift_date__lte=end_date,
            status__in=['SCHEDULED', 'CONFIRMED', 'COMPLETED']
        )
        
        total_hours = 0
        by_role = {}
        by_week = {}
        
        for shift in shifts:
            hours = shift.actual_hours
            total_hours += hours
            
            # By role
            if shift.role not in by_role:
                by_role[shift.role] = 0
            by_role[shift.role] += hours
            
            # By week
            week_key = shift.shift_date.isocalendar()[1]
            if week_key not in by_week:
                by_week[week_key] = 0
            by_week[week_key] += hours
        
        return {
            'total_hours': round(total_hours, 2),
            'by_role': {role: round(hours, 2) for role, hours in by_role.items()},
            'by_week': {str(week): round(hours, 2) for week, hours in by_week.items()},
            'shifts_count': shifts.count()
        }
    
    @staticmethod
    def generate_schedule_from_template(
        schedule_id: str,
        template_id: str,
        week_start_date
    ) -> Tuple[bool, str]:
        """
        Generate shifts for a week using a template
        
        Returns:
            (success: bool, message: str)
        """
        try:
            schedule = WeeklySchedule.objects.get(id=schedule_id)
            template = ScheduleTemplate.objects.get(id=template_id)
        except (WeeklySchedule.DoesNotExist, ScheduleTemplate.DoesNotExist):
            return False, "Schedule or template not found"
        
        try:
            # Get template shifts
            template_shifts = TemplateShift.objects.filter(template=template)
            
            created_count = 0
            for ts in template_shifts:
                # Calculate the actual date for this day of week
                days_ahead = ts.day_of_week - week_start_date.weekday()
                if days_ahead < 0:
                    days_ahead += 7
                
                shift_date = week_start_date + timedelta(days=days_ahead)
                
                # Find available staff for this role
                available_staff = CustomUser.objects.filter(
                    restaurant=schedule.restaurant,
                    role=ts.role,
                    is_active=True
                )
                
                if available_staff.exists():
                    staff = available_staff.first()
                    
                    # Check for conflicts
                    conflicts = SchedulingService.detect_scheduling_conflicts(
                        str(staff.id),
                        shift_date,
                        ts.start_time,
                        ts.end_time
                    )
                    
                    if not conflicts:
                        AssignedShift.objects.create(
                            schedule=schedule,
                            staff=staff,
                            shift_date=shift_date,
                            start_time=ts.start_time,
                            end_time=ts.end_time,
                            role=ts.role
                        )
                        created_count += 1
            
            return True, f"Generated {created_count} shifts from template"
        
        except Exception as e:
            return False, str(e)
    
    @staticmethod
    def get_schedule_analytics(schedule_id: str) -> Dict:
        """
        Get comprehensive analytics for a schedule
        """
        try:
            schedule = WeeklySchedule.objects.get(id=schedule_id)
        except WeeklySchedule.DoesNotExist:
            return {'error': 'Schedule not found'}
        
        shifts = AssignedShift.objects.filter(schedule=schedule)
        
        total_hours = sum(shift.actual_hours for shift in shifts)
        avg_shift_hours = total_hours / shifts.count() if shifts.count() > 0 else 0
        
        # By role
        by_role = {}
        for role in set(shifts.values_list('role', flat=True)):
            role_shifts = shifts.filter(role=role)
            by_role[role] = {
                'count': role_shifts.count(),
                'total_hours': sum(s.actual_hours for s in role_shifts)
            }
        
        return {
            'total_shifts': shifts.count(),
            'total_hours': round(total_hours, 2),
            'average_shift_hours': round(avg_shift_hours, 2),
            'unique_staff': shifts.values('staff').distinct().count(),
            'by_role': by_role,
            'by_status': dict(shifts.values('status').annotate(count=Count('id')).values_list('status', 'count')),
            'confirmation_rate': round(
                (shifts.filter(is_confirmed=True).count() / shifts.count() * 100) if shifts.count() > 0 else 0,
                2
            )
        }
    
    @staticmethod
    def notify_shift_assignment(shift: 'AssignedShift') -> None:
        """
        Send notification to staff about shift assignment
        """
        from notifications.models import Notification
        from django.template.loader import render_to_string
        from django.core.mail import send_mail
        from django.conf import settings
        
        try:
            # Create in-app notification
            message = f"You have been assigned a shift on {shift.shift_date} from {shift.start_time} to {shift.end_time}"
            Notification.objects.create(
                recipient=shift.staff,
                message=message,
                notification_type='SHIFT_UPDATE'
            )
            
            # Send email notification
            subject = f"New Shift Assignment - {shift.shift_date}"
            html_message = render_to_string('emails/shift_assigned.html', {
                'staff_name': shift.staff.get_full_name(),
                'shift_date': shift.shift_date,
                'start_time': shift.start_time,
                'end_time': shift.end_time,
                'role': shift.role,
                'restaurant_name': shift.schedule.restaurant.name,
            })
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [shift.staff.email],
                html_message=html_message,
                fail_silently=True,
            )
        except Exception as e:
            print(f"Error notifying shift assignment: {e}")
    
    @staticmethod
    def notify_shift_cancellation(shift: 'AssignedShift') -> None:
        """
        Send notification to staff about shift cancellation
        """
        from notifications.models import Notification
        from django.template.loader import render_to_string
        from django.core.mail import send_mail
        from django.conf import settings
        
        try:
            # Create in-app notification
            message = f"Your shift on {shift.shift_date} from {shift.start_time} to {shift.end_time} has been cancelled"
            Notification.objects.create(
                recipient=shift.staff,
                message=message,
                notification_type='SHIFT_UPDATE'
            )
            
            # Send email notification
            subject = f"Shift Cancelled - {shift.shift_date}"
            html_message = render_to_string('emails/shift_cancelled.html', {
                'staff_name': shift.staff.get_full_name(),
                'shift_date': shift.shift_date,
                'start_time': shift.start_time,
                'end_time': shift.end_time,
            })
            
            send_mail(
                subject,
                message,
                settings.DEFAULT_FROM_EMAIL,
                [shift.staff.email],
                html_message=html_message,
                fail_silently=True,
            )
        except Exception as e:
            print(f"Error notifying shift cancellation: {e}")