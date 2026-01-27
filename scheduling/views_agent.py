"""
Agent-specific views for scheduling operations.
These endpoints use LUA_WEBHOOK_API_KEY authentication instead of JWT.
"""
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from rest_framework.response import Response
from rest_framework import status, permissions
from django.conf import settings
from django.utils import timezone
from datetime import datetime, time, timedelta

from accounts.models import CustomUser, Restaurant
from .models import AssignedShift, WeeklySchedule
from .serializers import AssignedShiftSerializer
from .services import SchedulingService
import logging

logger = logging.getLogger(__name__)


def validate_agent_key(request):
    """Validate the agent API key from Authorization header."""
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    
    if not expected_key:
        return False, "Agent key not configured"
    
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return False, "Unauthorized"
    
    return True, None


@api_view(['GET'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_list_staff(request):
    """
    List all staff members for a restaurant.
    Used by the Lua agent to look up staff for scheduling.
    
    Query params:
    - restaurant_id: UUID of the restaurant (required)
    - name: Optional name filter (fuzzy match)
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        restaurant_id = request.query_params.get('restaurant_id')
        if not restaurant_id:
            return Response(
                {'error': 'restaurant_id query parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate restaurant exists
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return Response(
                {'error': 'Restaurant not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get staff for this restaurant
        queryset = CustomUser.objects.filter(
            restaurant=restaurant,
            is_active=True
        ).exclude(role='SUPER_ADMIN')
        
        # Optional name filter
        name_filter = request.query_params.get('name')
        if name_filter:
            name_filter = name_filter.lower()
            queryset = queryset.filter(
                first_name__icontains=name_filter
            ) | queryset.filter(
                last_name__icontains=name_filter
            )
        
        staff_list = []
        for staff in queryset:
            staff_list.append({
                'id': str(staff.id),
                'first_name': staff.first_name,
                'last_name': staff.last_name,
                'email': staff.email,
                'role': staff.role,
                'phone': staff.phone or '',
            })
        
        return Response(staff_list)
        
    except Exception as e:
        logger.error(f"Agent staff list error: {e}")
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_create_shift(request):
    """
    Create a shift for a staff member.
    Used by the Lua agent to schedule staff.
    
    Expected payload:
    {
        "restaurant_id": "uuid",
        "staff_id": "uuid",
        "shift_date": "YYYY-MM-DD",
        "start_time": "HH:MM",
        "end_time": "HH:MM",
        "role": "SERVER",  # optional, defaults to staff's role
        "notes": "optional notes"
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        
        # Required fields
        restaurant_id = data.get('restaurant_id')
        staff_id = data.get('staff_id')
        shift_date_str = data.get('shift_date')
        start_time_str = data.get('start_time')
        end_time_str = data.get('end_time')
        
        if not all([restaurant_id, staff_id, shift_date_str, start_time_str, end_time_str]):
            return Response({
                'success': False,
                'error': 'Missing required fields: restaurant_id, staff_id, shift_date, start_time, end_time'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate restaurant
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Restaurant not found'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Validate staff
        try:
            staff = CustomUser.objects.get(id=staff_id, restaurant=restaurant)
        except CustomUser.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Staff member not found in this restaurant'
            }, status=status.HTTP_404_NOT_FOUND)
        
        # Parse date and times
        try:
            shift_date = datetime.strptime(shift_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({
                'success': False,
                'error': 'Invalid shift_date format. Use YYYY-MM-DD'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            # Handle HH:MM or HH:MM:SS format
            if len(start_time_str) == 5:
                start_time = datetime.strptime(start_time_str, '%H:%M').time()
            else:
                start_time = datetime.strptime(start_time_str, '%H:%M:%S').time()
                
            if len(end_time_str) == 5:
                end_time = datetime.strptime(end_time_str, '%H:%M').time()
            else:
                end_time = datetime.strptime(end_time_str, '%H:%M:%S').time()
        except ValueError:
            return Response({
                'success': False,
                'error': 'Invalid time format. Use HH:MM or HH:MM:SS'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Determine role
        role = data.get('role') or staff.role or 'SERVER'
        
        # Get or create weekly schedule for this date
        # Calculate week start (Monday)
        days_since_monday = shift_date.weekday()
        week_start = shift_date - timedelta(days=days_since_monday)
        week_end = week_start + timedelta(days=6)
        
        schedule, created = WeeklySchedule.objects.get_or_create(
            restaurant=restaurant,
            week_start=week_start,
            defaults={'week_end': week_end}
        )
        
        # Create datetime objects for start and end
        start_datetime = timezone.datetime.combine(shift_date, start_time)
        end_datetime = timezone.datetime.combine(shift_date, end_time)
        
        # Make timezone-aware if needed
        if timezone.is_naive(start_datetime):
            start_datetime = timezone.make_aware(start_datetime)
        if timezone.is_naive(end_datetime):
            end_datetime = timezone.make_aware(end_datetime)
        
        # Check for conflicts
        conflicts = SchedulingService.detect_scheduling_conflicts(
            staff_id,
            shift_date,
            start_time,
            end_time
        )
        
        if conflicts:
            return Response({
                'success': False,
                'error': f"Schedule conflict detected: {staff.first_name} already has a shift at this time",
                'conflicts': conflicts
            }, status=status.HTTP_409_CONFLICT)
        
        # Create the shift
        shift = AssignedShift.objects.create(
            schedule=schedule,
            staff=staff,
            shift_date=shift_date,
            start_time=start_datetime,
            end_time=end_datetime,
            role=role.upper(),
            notes=data.get('notes', ''),
            status='SCHEDULED'
        )
        
        # Add staff to staff_members M2M
        shift.staff_members.add(staff)
        
        return Response({
            'success': True,
            'shift': {
                'id': str(shift.id),
                'staff_id': str(staff.id),
                'staff_name': f"{staff.first_name} {staff.last_name}",
                'shift_date': str(shift_date),
                'start_time': start_time_str,
                'end_time': end_time_str,
                'role': role.upper()
            },
            'message': f"Successfully scheduled {staff.first_name} for {shift_date} from {start_time_str} to {end_time_str}"
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        logger.error(f"Agent create shift error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_send_shift_notification(request):
    """
    Send WhatsApp notification about a scheduled shift.
    
    Expected payload:
    {
        "shift_id": "uuid",
        "staff_id": "uuid",  # optional if shift_id provided
        "message": "optional custom message"
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        shift_id = data.get('shift_id')
        staff_id = data.get('staff_id')
        custom_message = data.get('message')
        
        if not shift_id and not staff_id:
            return Response({
                'success': False,
                'error': 'Either shift_id or staff_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Get shift if provided
        shift = None
        staff = None
        
        if shift_id:
            try:
                shift = AssignedShift.objects.select_related('staff', 'schedule__restaurant').get(id=shift_id)
                staff = shift.staff
            except AssignedShift.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Shift not found'
                }, status=status.HTTP_404_NOT_FOUND)
        elif staff_id:
            try:
                staff = CustomUser.objects.get(id=staff_id)
            except CustomUser.DoesNotExist:
                return Response({
                    'success': False,
                    'error': 'Staff not found'
                }, status=status.HTTP_404_NOT_FOUND)
        
        # Get phone number
        phone = staff.phone if staff else None
        if not phone:
            return Response({
                'success': False,
                'error': 'Staff member has no phone number'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Build message
        if shift:
            shift_date = shift.shift_date.strftime('%A, %B %d')
            start = shift.start_time.strftime('%H:%M') if hasattr(shift.start_time, 'strftime') else str(shift.start_time)[:5]
            end = shift.end_time.strftime('%H:%M') if hasattr(shift.end_time, 'strftime') else str(shift.end_time)[:5]
            restaurant_name = shift.schedule.restaurant.name
            
            message = custom_message or (
                f"Hi {staff.first_name}! 📅\n\n"
                f"You've been scheduled for a shift at {restaurant_name}:\n\n"
                f"📆 Date: {shift_date}\n"
                f"⏰ Time: {start} - {end}\n"
                f"👔 Role: {shift.role}\n\n"
                f"Please reply 'CONFIRM' to confirm your availability."
            )
        else:
            message = custom_message or f"Hi {staff.first_name}! You have new shift information."
        
        # Send WhatsApp message
        from notifications.services import notification_service
        result = notification_service.send_whatsapp_text(
            phone=phone,
            body=message
        )
        
        # Mark shift as notified
        if shift:
            shift.notification_sent = True
            shift.notification_sent_at = timezone.now()
            shift.notification_channels = ['whatsapp']
            shift.save(update_fields=['notification_sent', 'notification_sent_at', 'notification_channels'])
        
        return Response({
            'success': True,
            'message': f"Notification sent to {staff.first_name}",
            'whatsapp_result': list(result) if isinstance(result, tuple) else result
        })
        
    except Exception as e:
        logger.error(f"Agent send notification error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
@api_view(['POST'])
@authentication_classes([])  # Bypass JWT auth
@permission_classes([permissions.AllowAny])
def agent_optimize_schedule(request):
    """
    Generate optimized schedule for a week.
    Used by the Lua agent to automatically fill a week's schedule.
    
    Expected payload:
    {
        "restaurant_id": "uuid",
        "week_start": "YYYY-MM-DD",
        "department": "kitchen/service/all"
    }
    """
    try:
        # Validate agent key
        is_valid, error = validate_agent_key(request)
        if not is_valid:
            return Response({'success': False, 'error': error}, status=status.HTTP_401_UNAUTHORIZED)
        
        data = request.data
        restaurant_id = data.get('restaurant_id')
        week_start = data.get('week_start')
        department = data.get('department')
        
        if not restaurant_id or not week_start:
            return Response({
                'success': False,
                'error': 'Missing required fields: restaurant_id, week_start'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Validate restaurant
        try:
            restaurant = Restaurant.objects.get(id=restaurant_id)
        except Restaurant.DoesNotExist:
            return Response({
                'success': False,
                'error': 'Restaurant not found'
            }, status=status.HTTP_404_NOT_FOUND)
            
        # OptimizationService will handle the business logic
        from .services import OptimizationService
        result = OptimizationService.optimize_schedule(
            str(restaurant.id),
            week_start,
            department
        )
        
        if result.get('error'):
            return Response({
                'success': False,
                'error': result.get('error')
            }, status=status.HTTP_400_BAD_REQUEST)
            
        return Response({
            'success': True,
            **result
        })
        
    except Exception as e:
        logger.error(f"Agent optimize schedule error: {e}")
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
