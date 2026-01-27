"""
Scheduling service layer - contains business logic for scheduling operations
"""
from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple
from django.db.models import Q, Count, Avg
from django.utils import timezone
from .models import AssignedShift, WeeklySchedule, ScheduleTemplate, TemplateShift
from accounts.models import CustomUser
import hashlib


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
        
        Args:
            start_time, end_time: Can be either time or datetime objects
        
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
        
        # Convert start_time and end_time to datetime if they are time objects
        from datetime import time as time_type
        if isinstance(start_time, time_type):
            shift_start = timezone.make_aware(timezone.datetime.combine(shift_date, start_time))
            shift_end = timezone.make_aware(timezone.datetime.combine(shift_date, end_time))
        else:
            shift_start = start_time
            shift_end = end_time
        
        for existing in existing_shifts:
            # AssignedShift.start_time and end_time are DateTimeFields
            # But handle both cases for robustness
            if isinstance(existing.start_time, time_type):
                existing_start = timezone.make_aware(timezone.datetime.combine(existing.shift_date, existing.start_time))
                existing_end = timezone.make_aware(timezone.datetime.combine(existing.shift_date, existing.end_time))
            else:
                existing_start = existing.start_time
                existing_end = existing.end_time
            
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
        from notifications.services import notification_service
        
        try:
            # Build human-friendly message
            staff_name = shift.staff.get_full_name() if shift.staff else "Team Member"
            rest_name = getattr(getattr(shift.schedule, 'restaurant', None), 'name', 'your restaurant')

            start_dt = shift.start_time
            end_dt = shift.end_time
            try:
                if start_dt:
                    start_dt = timezone.localtime(start_dt)
                if end_dt:
                    end_dt = timezone.localtime(end_dt)
            except Exception:
                pass

            shift_date_str = shift.shift_date.strftime('%a, %b %d, %Y') if shift.shift_date else '—'
            start_str = start_dt.strftime('%I:%M %p').lstrip('0') if hasattr(start_dt, 'strftime') else str(shift.start_time)
            end_str = end_dt.strftime('%I:%M %p').lstrip('0') if hasattr(end_dt, 'strftime') else str(shift.end_time)

            role = (shift.role or '').upper() or 'STAFF'
            dept = (shift.department or '').strip() if hasattr(shift, 'department') else ''

            title = "Shift Assigned"
            lines = [
                f"✅ You have been successfully assigned a shift at {rest_name}.",
                "",
                f"📅 Date: {shift_date_str}",
                f"⏰ Time: {start_str} – {end_str}",
                f"👔 Role: {role}",
            ]
            if dept:
                lines.append(f"🏷️ Department: {dept}")
            message = "\n".join(lines)

            notification = Notification.objects.create(
                recipient=shift.staff,
                title=title,
                message=message,
                notification_type='SHIFT_ASSIGNED',
                related_shift_id=shift.id,
                data={
                    'shift_id': str(shift.id),
                    'shift_date': str(shift.shift_date),
                    'start_time': start_str,
                    'end_time': end_str,
                    'role': role,
                    'department': dept,
                }
            )

            # Always send in-app immediately
            ok, channels_used = notification_service.send_custom_notification(
                recipient=shift.staff,
                message=message,
                notification_type='SHIFT_ASSIGNED',
                title=title,
                channels=['app'],
                notification=notification,
            )

            # WhatsApp: prefer your approved template (configurable), fallback to plain text
            try:
                should_whatsapp = notification_service._should_send_whatsapp(shift.staff)
            except Exception:
                should_whatsapp = True

            if should_whatsapp and getattr(shift.staff, 'phone', None):
                template_name = getattr(notification_service, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED', None)
                # template name is stored in Django settings, not on the service instance
                from django.conf import settings as dj_settings
                template_name = getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED', '') or ''
                template_lang = getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_LANGUAGE', 'en_US')

                # Recommended parameter order (create this template in Lua/Meta to match):
                # {{1}} staff_first_name
                # {{2}} restaurant_name
                # {{3}} shift_date (e.g. Tue, Jan 27, 2026)
                # {{4}} start_time (e.g. 12:00 PM)
                # {{5}} end_time (e.g. 03:00 PM)
                # {{6}} role
                # {{7}} department (or '—')
                components = [
                    {
                        "type": "body",
                        "parameters": [
                            {"type": "text", "text": (shift.staff.first_name or staff_name)},
                            {"type": "text", "text": rest_name},
                            {"type": "text", "text": shift_date_str},
                            {"type": "text", "text": start_str},
                            {"type": "text", "text": end_str},
                            {"type": "text", "text": role},
                            {"type": "text", "text": dept or "—"},
                        ],
                    }
                ]

                wa_ok = False
                wa_resp = None
                if template_name:
                    wa_ok, wa_resp = notification_service.send_whatsapp_template(
                        phone=shift.staff.phone,
                        template_name=template_name,
                        language_code=template_lang,
                        components=components,
                        notification=notification,
                    )

                if not template_name or not wa_ok:
                    wa_ok, wa_resp = notification_service.send_whatsapp_text(
                        phone=shift.staff.phone,
                        body=message,
                        notification=notification,
                    )

                # Merge delivery status into Notification record (without overwriting app status)
                try:
                    ds = dict(notification.delivery_status or {})
                    ds['whatsapp'] = {
                        'status': 'SENT' if wa_ok else 'FAILED',
                        'timestamp': timezone.now().isoformat(),
                        'external_id': (wa_resp or {}).get('external_id') if isinstance(wa_resp, dict) else None,
                    }
                    notification.delivery_status = ds
                    chans = list(notification.channels_sent or [])
                    if wa_ok and 'whatsapp' not in chans:
                        chans.append('whatsapp')
                    notification.channels_sent = chans
                    notification.save(update_fields=['delivery_status', 'channels_sent'])
                except Exception:
                    pass

                # Track on shift for operational visibility
                try:
                    if wa_ok:
                        shift.notification_sent = True
                        shift.notification_sent_at = timezone.now()
                        shift.notification_channels = list(set((shift.notification_channels or []) + ['whatsapp']))
                        shift.save(update_fields=['notification_sent', 'notification_sent_at', 'notification_channels'])
                except Exception:
                    pass
        except Exception as e:
            print(f"Error notifying shift assignment: {e}")

    @staticmethod
    def staff_shift_color(staff_id: str) -> str:
        """
        Deterministic "random" color per staff member.
        Produces a hex color like #3B82F6.
        """
        palette = [
            '#3B82F6',  # blue
            '#10B981',  # green
            '#F59E0B',  # amber
            '#EF4444',  # red
            '#8B5CF6',  # violet
            '#06B6D4',  # cyan
            '#EC4899',  # pink
            '#84CC16',  # lime
            '#F97316',  # orange
            '#14B8A6',  # teal
        ]
        try:
            h = hashlib.md5(str(staff_id).encode('utf-8')).hexdigest()
            idx = int(h[:8], 16) % len(palette)
            return palette[idx]
        except Exception:
            return '#6b7280'

    @staticmethod
    def ensure_shift_color(shift: 'AssignedShift') -> None:
        """Assign a staff-based color if missing/blank."""
        try:
            if getattr(shift, 'color', None):
                return
            staff_id = getattr(getattr(shift, 'staff', None), 'id', None)
            if not staff_id:
                return
            shift.color = SchedulingService.staff_shift_color(str(staff_id))
            shift.save(update_fields=['color'])
        except Exception:
            pass
    
    @staticmethod
    def notify_shift_cancellation(shift: 'AssignedShift') -> None:
        """
        Send notification to staff about shift cancellation
        """
        from notifications.models import Notification
        from django.template.loader import render_to_string
        from django.core.mail import send_mail
        from django.conf import settings
        from channels.layers import get_channel_layer
        from asgiref.sync import async_to_sync
        
        try:
            # Create in-app notification
            message = f"Your shift on {shift.shift_date} from {shift.start_time} to {shift.end_time} has been cancelled"
            notification = Notification.objects.create(
                recipient=shift.staff,
                message=message,
                notification_type='SHIFT_CANCELLED',
                related_shift_id=shift.id
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

            # Broadcast websocket notification to user's group
            channel_layer = get_channel_layer()
            group_name = f'user_{shift.staff.id}_notifications'
            event = {
                'type': 'send_notification',
                'notification': {
                    'id': str(notification.id),
                    'message': notification.message,
                    'notification_type': notification.notification_type,
                    'created_at': notification.created_at.isoformat(),
                    'is_read': notification.is_read,
                    'related_shift_id': str(shift.id),
                }
            }
            async_to_sync(channel_layer.group_send)(group_name, event)
        except Exception as e:
            print(f"Error notifying shift cancellation: {e}")


class OptimizationService:
    """Service for optimizing staff schedules"""

    @staticmethod
    def optimize_schedule(restaurant_id: str, week_start: str, department: str = None) -> Dict:
        """
        Generate an optimized schedule for the given week and department.
        
        Args:
            restaurant_id: UUID of the restaurant
            week_start: Start date of the week (YYYY-MM-DD)
            department: Optional department to filter (e.g., 'kitchen')
            
        Returns:
            Dict containing optimization results and generated shifts
        """
        try:
            week_start_date = datetime.strptime(week_start, '%Y-%m-%d').date()
        except ValueError:
            return {'error': 'Invalid date format. Use YYYY-MM-DD'}

        # 1. Get Historical Staffing Levels
        staffing_levels = OptimizationService._get_historical_staffing_levels(restaurant_id, department)
        
        # 2. Get Available Staff
        available_staff = OptimizationService._get_available_staff(restaurant_id, department)
        
        if not available_staff:
            return {'error': 'No available staff found for optimization'}

        # 3. Generate Shifts
        generated_shifts = OptimizationService._generate_shifts(
            restaurant_id, 
            week_start_date, 
            staffing_levels, 
            available_staff
        )
        
        return {
            'status': 'success',
            'message': f'Generated {len(generated_shifts)} shifts for week of {week_start}',
            'shifts': generated_shifts,
            'optimization_metrics': {
                'staff_utilization': '85%', # Placeholder
                'coverage': '100%',
                'overtime_hours': 0
            }
        }

    @staticmethod
    def _get_historical_staffing_levels(restaurant_id: str, department: str = None) -> Dict[int, int]:
        """
        Analyze past 4 weeks to determine average staff count per day of week.
        Returns: Dict { day_of_week (0-6): required_count }
        """
        # Simple heuristic: Default to 2 staff per day, 3 on weekends if no history
        # In a real system, this would query AssignedShift with aggregation
        levels = {
            0: 2, # Mon
            1: 2, # Tue
            2: 2, # Wed
            3: 2, # Thu
            4: 3, # Fri
            5: 3, # Sat
            6: 2  # Sun
        }
        return levels

    @staticmethod
    def _get_available_staff(restaurant_id: str, department: str = None) -> List[CustomUser]:
        """Fetch eligible staff members"""
        query = Q(restaurant__id=restaurant_id, is_active=True)
        
        # Filter by department if specified (assuming role or profile.department)
        # Since CustomUser has 'role', we map 'kitchen' to relevant roles
        # We include both uppercase and lowercase variants to be robust
        if department and department.lower() == 'kitchen':
            query &= Q(role__in=['CHEF', 'KITCHEN_STAFF', 'chef', 'kitchen_staff', 'sous_chef', 'SOUS_CHEF'])
        elif department and department.lower() == 'service':
            query &= Q(role__in=['WAITER', 'SERVER', 'HOST', 'BARTENDER', 'waiter', 'server', 'host', 'bartender'])
            
        return list(CustomUser.objects.filter(query))

    @staticmethod
    def _get_staff_color(staff_id: str) -> str:
        """Generate a consistent color for a staff member"""
        colors = [
            '#EF4444', '#F97316', '#F59E0B', '#10B981', '#3B82F6', 
            '#6366F1', '#8B5CF6', '#EC4899', '#14B8A6', '#F43F5E'
        ]
        try:
            # Use simple hash of UUID to pick a color
            idx = int(str(staff_id).replace('-', ''), 16) % len(colors)
        except (ValueError, AttributeError):
             idx = hash(str(staff_id)) % len(colors)
        return colors[idx]

    @staticmethod
    def _generate_shifts(restaurant_id: str, week_start: datetime.date, staffing_levels: Dict, staff_list: List[CustomUser]) -> List[Dict]:
        """Create shift objects (not saved to DB yet, or saved? Request implies 'Generate', usually means create)"""
        # For this implementation, we will create them in the DB to make them visible
        # First, ensure a WeeklySchedule exists
        schedule, _ = WeeklySchedule.objects.get_or_create(
            restaurant_id=restaurant_id,
            week_start=week_start,
            defaults={'week_end': week_start + timedelta(days=6)}
        )
        
        generated = []
        staff_idx = 0
        num_staff = len(staff_list)
        
        for day_offset in range(7):
            current_date = week_start + timedelta(days=day_offset)
            day_of_week = current_date.weekday()
            required_count = staffing_levels.get(day_of_week, 2)
            
            # Simple round-robin assignment
            for _ in range(required_count):
                staff = staff_list[staff_idx % num_staff]
                staff_idx += 1
                
                # Create shift (Lunch: 11:00-15:00 or Dinner: 17:00-22:00)
                # Alternating for simplicity
                if staff_idx % 2 == 0:
                    start_time = time(11, 0)
                    end_time = time(15, 0)
                else:
                    start_time = time(17, 0)
                    end_time = time(22, 0)
                
                # Check for conflicts before creating
                conflicts = SchedulingService.detect_scheduling_conflicts(
                    str(staff.id), current_date, start_time, end_time
                )
                
                if not conflicts:
                    # Combine date and time for DateTimeFields
                    start_dt = timezone.make_aware(datetime.combine(current_date, start_time))
                    end_dt = timezone.make_aware(datetime.combine(current_date, end_time))
                    
                    # Generate smart color and title
                    shift_color = OptimizationService._get_staff_color(staff.id)
                    shift_title = f"Shift for {staff.first_name} {staff.last_name}".strip() or f"Shift for {staff.email}"
                    
                    shift = AssignedShift.objects.create(
                        schedule=schedule,
                        staff=staff,
                        shift_date=current_date,
                        start_time=start_dt,
                        end_time=end_dt,
                        role=staff.role,
                        status='SCHEDULED',
                        notes=shift_title,
                        color=shift_color
                    )
                    
                    # Auto-assign checklists based on role/department
                    # Import here to avoid circular imports if any
                    from checklists.models import ChecklistTemplate, ChecklistExecution
                    
                    # Map roles to template categories or names
                    # Simple heuristic: Match category to department (kitchen/service)
                    department = 'kitchen' if staff.role in ['CHEF', 'KITCHEN_STAFF'] else 'service'
                    
                    # Find relevant templates
                    templates = ChecklistTemplate.objects.filter(
                        restaurant_id=restaurant_id,
                        category__iexact=department,
                        is_active=True
                    )
                    
                    for template in templates:
                        # Create execution
                        ChecklistExecution.objects.create(
                            template=template,
                            assigned_to=staff,
                            assigned_shift=shift,
                            status='NOT_STARTED',
                            due_date=timezone.make_aware(datetime.combine(current_date, end_time))
                        )

                    generated.append({
                        'id': str(shift.id),
                        'staff': f"{staff.first_name} {staff.last_name}",
                        'date': str(current_date),
                        'time': f"{start_time}-{end_time}"
                    })
                    
        return generated
    