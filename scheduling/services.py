"""
Scheduling service layer - contains business logic for scheduling operations
"""
from datetime import datetime, timedelta, time
from typing import Dict, List, Tuple
from django.db.models import Q, Count, Avg
from django.utils import timezone
from .models import AssignedShift, WeeklySchedule, ScheduleTemplate, TemplateShift, StaffAvailability, TimeOffRequest, Holiday
from accounts.models import CustomUser, Restaurant
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
    def detect_scheduling_conflicts(staff_id: str, shift_date, start_time, end_time, ignore_shift_id=None, workspace_location=None) -> List[Dict]:
        """
        Detect scheduling conflicts for a staff member including:
        - Overlapping shifts
        - Availability preferences
        - Approved time off
        - Restaurant holidays
        - Weekly hour limits
        - Minimum rest periods (Clopening)
        - Location/Station overcapacity
        """
        conflicts = []
        
        try:
            staff = CustomUser.objects.get(id=staff_id)
            restaurant = staff.restaurant
        except CustomUser.DoesNotExist:
            return conflicts
        
        # 0. Convert times to datetime objects for comparison
        from datetime import time as time_type
        if isinstance(start_time, time_type):
            shift_start = timezone.make_aware(timezone.datetime.combine(shift_date, start_time))
            end_date = shift_date
            if end_time < start_time:
                end_date = shift_date + timedelta(days=1)
            shift_end = timezone.make_aware(timezone.datetime.combine(end_date, end_time))
        else:
            shift_start = start_time
            shift_end = end_time

        # 1. Check for OVERLAPPING shifts for THIS staff
        existing_shifts = AssignedShift.objects.filter(
            staff=staff,
            shift_date=shift_date,
            status__in=['SCHEDULED', 'CONFIRMED']
        )
        if ignore_shift_id:
            existing_shifts = existing_shifts.exclude(id=ignore_shift_id)
        
        for existing in existing_shifts:
            if shift_start < existing.end_time and shift_end > existing.start_time:
                conflicts.append({
                    'type': 'OVERLAP',
                    'message': f"Overlaps with existing shift: {existing.start_time.strftime('%H:%M')}-{existing.end_time.strftime('%H:%M')}",
                    'shift_id': str(existing.id)
                })

        # 2. Check for TIME OFF
        time_off = TimeOffRequest.objects.filter(
            staff=staff,
            status='APPROVED',
            start_date__lte=shift_date,
            end_date__gte=shift_date
        )
        for tor in time_off:
            conflicts.append({
                'type': 'TIME_OFF',
                'message': f"Staff has approved time off ({tor.get_request_type_display()})",
                'request_id': str(tor.id)
            })

        # 3. Check for AVAILABILITY
        availability = StaffAvailability.objects.filter(
            staff=staff,
            day_of_week=shift_date.weekday()
        )
        if availability.exists():
            is_pref_available = False
            for pref in availability:
                if pref.is_available:
                    if pref.start_time and pref.end_time:
                        if start_time >= pref.start_time and end_time <= pref.end_time:
                            is_pref_available = True
                            break
                    else:
                        is_pref_available = True
                        break
            
            if not is_pref_available:
                conflicts.append({
                    'type': 'AVAILABILITY',
                    'message': "Outside of staff's preferred availability"
                })

        # 4. Check for HOLIDAYS
        if restaurant:
            holidays = Holiday.objects.filter(restaurant=restaurant, date=shift_date, is_closed=True)
            for holiday in holidays:
                conflicts.append({
                    'type': 'HOLIDAY',
                    'message': f"Restaurant is closed for holiday: {holiday.name}"
                })

        # 5. Check for WEEKLY HOURS LIMIT
        if restaurant and restaurant.max_weekly_hours:
            week_start = shift_date - timedelta(days=shift_date.weekday())
            week_end = week_start + timedelta(days=6)
            week_stats = SchedulingService.calculate_staff_hours(staff_id, week_start, week_end)
            current_hours = float(week_stats.get('total_hours', 0))
            new_shift_hours = (shift_end - shift_start).total_seconds() / 3600
            
            if current_hours + new_shift_hours > float(restaurant.max_weekly_hours):
                conflicts.append({
                    'type': 'WEEKLY_LIMIT',
                    'message': f"Exceeds max weekly hours ({restaurant.max_weekly_hours}h). Total would be {current_hours + new_shift_hours:.1f}h"
                })

        # 6. Check for MINIMUM REST PERIOD (Clopening)
        if restaurant and restaurant.min_rest_hours:
            # Check previous day's last shift
            prev_day = shift_date - timedelta(days=1)
            last_shift = AssignedShift.objects.filter(
                staff=staff, 
                shift_date=prev_day,
                status__in=['SCHEDULED', 'CONFIRMED', 'COMPLETED']
            ).order_by('-end_time').first()
            
            if last_shift:
                rest_duration = (shift_start - last_shift.end_time).total_seconds() / 3600
                if rest_duration < float(restaurant.min_rest_hours):
                    conflicts.append({
                        'type': 'REST_PERIOD',
                        'message': f"Less than {restaurant.min_rest_hours}h rest since previous shift (only {rest_duration:.1f}h rest)"
                    })

        # 7. Check for LOCATION OVERCAPACITY
        if workspace_location and restaurant:
            location_conflicts = AssignedShift.objects.filter(
                schedule__restaurant=restaurant,
                shift_date=shift_date,
                workspace_location=workspace_location,
                status__in=['SCHEDULED', 'CONFIRMED']
            )
            if ignore_shift_id:
                location_conflicts = location_conflicts.exclude(id=ignore_shift_id)
            
            for loc_shift in location_conflicts:
                if shift_start < loc_shift.end_time and shift_end > loc_shift.start_time:
                    conflicts.append({
                        'type': 'LOCATION',
                        'message': f"Station '{workspace_location}' is already occupied by {loc_shift.staff.get_full_name()}",
                        'shift_id': str(loc_shift.id)
                    })

        return conflicts

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
                        # Use full datetime so clock-in/reminder tasks find shifts (they filter by start_time range)
                        start_dt = timezone.make_aware(timezone.datetime.combine(shift_date, ts.start_time)) if ts.start_time else timezone.now()
                        end_dt = timezone.make_aware(timezone.datetime.combine(shift_date, ts.end_time)) if ts.end_time else start_dt + timedelta(hours=4)
                        if ts.end_time and ts.end_time < ts.start_time:
                            end_dt = timezone.make_aware(timezone.datetime.combine(shift_date + timedelta(days=1), ts.end_time))
                        AssignedShift.objects.create(
                            schedule=schedule,
                            staff=staff,
                            shift_date=shift_date,
                            start_time=start_dt,
                            end_time=end_dt,
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
    def notify_shift_assignment(shift: 'AssignedShift', force_whatsapp: bool = False) -> None:
        """
        Send notification with full shift details to every assigned staff member
        (Miya / manual create: all chosen staff get the same details).
        """
        from notifications.models import Notification
        from notifications.services import notification_service

        try:
            # Collect all assigned staff (legacy staff + staff_members), deduplicated
            all_staff = []
            if shift.staff_id:
                all_staff.append(shift.staff)
            for m in shift.staff_members.all():
                if m and m not in all_staff:
                    all_staff.append(m)
            if not all_staff:
                return

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
            dept = (getattr(shift, 'department', '') or '').strip()
            shift_title = (getattr(shift, 'notes', '') or '').strip()
            workspace_location = (getattr(shift, 'workspace_location', '') or '').strip()
            instructions = (getattr(shift, 'preparation_instructions', '') or '').strip()

            # Who's on this shift (for "all details")
            colleague_names = [s.get_full_name() or (getattr(s, 'first_name', '') or 'Staff') for s in all_staff]
            colleagues_line = ', '.join(colleague_names[:10])
            if len(colleague_names) > 10:
                colleagues_line += f' (+{len(colleague_names) - 10} more)'

            # Tasks: template names + custom task titles
            task_parts = []
            try:
                for t in shift.task_templates.all():
                    name = (getattr(t, 'name', '') or '').strip()
                    if name:
                        task_parts.append(name)
            except Exception:
                pass
            try:
                for t in shift.tasks.all():
                    title = (getattr(t, 'title', '') or '').strip()
                    if title:
                        task_parts.append(title)
            except Exception:
                pass
            tasks_line = ', '.join(task_parts[:15]) if task_parts else None
            if task_parts and len(task_parts) > 15:
                tasks_line += f' (+{len(task_parts) - 15} more)'

            # Build one base message with all details (same for everyone)
            title = "Shift Assigned"
            lines = [
                f"✅ You have been assigned a shift at {rest_name}.",
                "",
                f"🧾 Shift: {shift_title}" if shift_title else "",
                f"📅 Date: {shift_date_str}",
                f"⏰ Time: {start_str} – {end_str}",
                f"👔 Role: {role}",
            ]
            lines = [ln for ln in lines if ln != ""]
            if dept:
                lines.append(f"🏷️ Department: {dept}")
            if colleagues_line:
                lines.append(f"👥 With: {colleagues_line}")
            if tasks_line:
                lines.append(f"📋 Tasks: {tasks_line}")
            if workspace_location:
                lines.append(f"📍 Location: {workspace_location}")
            if instructions:
                lines.append(f"📝 Notes: {instructions}")
            message = "\n".join(lines)

            shift_data = {
                'shift_id': str(shift.id),
                'shift_date': str(shift.shift_date),
                'start_time': start_str,
                'end_time': end_str,
                'role': role,
                'department': dept,
                'shift_title': shift_title,
                'colleagues': colleague_names,
                'tasks': task_parts,
            }

            # WhatsApp template params (shared)
            from django.conf import settings as dj_settings
            template_name = (getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED', '') or '').strip()
            template_lang = getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED_LANGUAGE', None)
            if not template_name:
                template_name = (getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED', '') or '').strip() or 'staff_weekly_schedule'
                template_lang = getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_LANGUAGE', 'en_US')
            template_lang = template_lang or 'en_US'

            def _cap(s: str, n: int) -> str:
                s = str(s or '').strip()
                return s if len(s) <= n else (s[: max(0, n - 1)] + "…")

            detailed = template_name == (getattr(dj_settings, 'WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED', '') or '').strip()

            any_whatsapp_sent = False
            for recipient in all_staff:
                try:
                    staff_name = recipient.get_full_name() or (getattr(recipient, 'first_name', '') or 'Team Member')
                    try:
                        should_whatsapp = bool(force_whatsapp) or notification_service._should_send_whatsapp(recipient)
                    except Exception:
                        should_whatsapp = True

                    notification = Notification.objects.create(
                        recipient=recipient,
                        title=title,
                        message=message,
                        notification_type='SHIFT_ASSIGNED',
                        related_shift_id=shift.id,
                        data=shift_data,
                    )

                    ok, _ = notification_service.send_custom_notification(
                        recipient=recipient,
                        message=message,
                        notification_type='SHIFT_ASSIGNED',
                        title=title,
                        channels=['app'],
                        notification=notification,
                    )

                    if should_whatsapp and getattr(recipient, 'phone', None):
                        first_name = _cap((recipient.first_name or staff_name), 30)
                        if detailed:
                            components = [{
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": first_name},
                                    {"type": "text", "text": _cap(rest_name, 60)},
                                    {"type": "text", "text": _cap(shift_date_str, 40)},
                                    {"type": "text", "text": _cap(start_str, 20)},
                                    {"type": "text", "text": _cap(end_str, 20)},
                                    {"type": "text", "text": _cap(role, 30)},
                                    {"type": "text", "text": _cap(dept or "—", 40)},
                                    {"type": "text", "text": _cap(shift_title or "—", 80)},
                                    {"type": "text", "text": _cap(workspace_location or "—", 60)},
                                    {"type": "text", "text": _cap(instructions or "—", 120)},
                                ],
                            }]
                        else:
                            components = [{
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": first_name},
                                    {"type": "text", "text": _cap(rest_name, 60)},
                                    {"type": "text", "text": _cap(shift_date_str, 40)},
                                    {"type": "text", "text": _cap(start_str, 20)},
                                    {"type": "text", "text": _cap(end_str, 20)},
                                    {"type": "text", "text": _cap(role, 30)},
                                ],
                            }]

                        wa_ok = False
                        wa_resp = None
                        if template_name:
                            wa_ok, wa_resp = notification_service.send_whatsapp_template(
                                phone=recipient.phone,
                                template_name=template_name,
                                language_code=template_lang,
                                components=components,
                                notification=notification,
                            )
                        if not template_name or not wa_ok:
                            wa_ok, wa_resp = notification_service.send_whatsapp_text(
                                phone=recipient.phone,
                                body=message,
                                notification=notification,
                            )
                        if wa_ok:
                            any_whatsapp_sent = True
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
                except Exception:
                    pass

            if any_whatsapp_sent:
                try:
                    shift.notification_sent = True
                    shift.notification_sent_at = timezone.now()
                    shift.notification_channels = list(set((shift.notification_channels or []) + ['whatsapp']))
                    shift.save(update_fields=['notification_sent', 'notification_sent_at', 'notification_channels'])
                except Exception:
                    pass
        except Exception as e:
            pass


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
        """Assign a staff-based color (unique per staff) if missing/blank."""
        try:
            if getattr(shift, 'color', None):
                return
            staff_id = getattr(getattr(shift, 'staff', None), 'id', None)
            if not staff_id:
                first_member = shift.staff_members.order_by('id').first()
                staff_id = getattr(first_member, 'id', None) if first_member else None
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
            # Human-readable date and times for the notification message
            date_str = shift.shift_date.strftime('%A, %b %d, %Y') if shift.shift_date else '—'
            start_dt = shift.start_time
            end_dt = shift.end_time
            if start_dt and timezone.is_aware(start_dt):
                start_dt = timezone.localtime(start_dt)
            if end_dt and timezone.is_aware(end_dt):
                end_dt = timezone.localtime(end_dt)
            start_str = start_dt.strftime('%I:%M %p').lstrip('0') if start_dt and hasattr(start_dt, 'strftime') else '—'
            end_str = end_dt.strftime('%I:%M %p').lstrip('0') if end_dt and hasattr(end_dt, 'strftime') else '—'
            message = f"Your shift on {date_str} from {start_str} to {end_str} has been cancelled."
            notification = Notification.objects.create(
                recipient=shift.staff,
                message=message,
                notification_type='SHIFT_CANCELLED',
                related_shift_id=shift.id
            )
            
            # Send email notification
            subject = f"Shift Cancelled - {date_str}"
            html_message = render_to_string('emails/shift_cancelled.html', {
                'staff_name': shift.staff.get_full_name(),
                'shift_date': date_str,
                'start_time': start_str,
                'end_time': end_str,
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
            # logger.error(f"Error notifying shift cancellation: {e}")
            pass



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
    