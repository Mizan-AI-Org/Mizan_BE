from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes, authentication_classes
from django.conf import settings
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from django.http import Http404
from notifications.utils import send_realtime_notification
from notifications.services import notification_service

from accounts.utils import calculate_distance
from .models import ClockEvent
from .serializers import ClockEventSerializer, ClockInSerializer, ShiftSerializer
from accounts.models import CustomUser, AuditLog
from accounts.views import get_client_ip
from accounts.permissions import IsAdminOrManager
from django.db.models import Q
from scheduling.models import AssignedShift
import base64  # <--- ADD THIS IMPORT
import logging

logger = logging.getLogger(__name__)
from django.core.files.base import ContentFile  # <--- ADD THIS IMPORT
# Your existing geolocation endpoints (keep these)
@api_view(['POST'])
def clock_in(request):
    serializer = ClockInSerializer(data=request.data)
    
    if serializer.is_valid():
        user = serializer.validated_data['user']
        latitude = serializer.validated_data['latitude']
        longitude = serializer.validated_data['longitude']
        photo = serializer.validated_data.get('photo')
        accuracy = serializer.validated_data.get('accuracy')
        
        # Check if user is already clocked in
        last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
        if last_event and last_event.event_type == 'in':
            return Response({
                'error': 'Already clocked in',
                'last_clock_in': last_event.timestamp
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create clock in event with location data
        clock_event = ClockEvent.objects.create(
            staff=user,
            event_type='in',
            latitude=latitude,
            longitude=longitude,
            photo=photo,
            device_id=request.META.get('HTTP_USER_AGENT', ''),
            notes=f"GPS Accuracy: {accuracy}m" if accuracy else ""
        )
        
        # Calculate distance for response
        restaurant = user.restaurant
        distance = calculate_distance(
            float(restaurant.latitude) if restaurant.latitude else 0,
            float(restaurant.longitude) if restaurant.longitude else 0,
            latitude,
            longitude
        )
        
        response_data = ClockEventSerializer(clock_event).data
        response_data['distance_from_restaurant'] = f"{distance:.0f}m"
        response_data['location_verified'] = distance <= 100  # Within 100m
        
        return Response(response_data)
    
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
def clock_out(request):
    pin_code = request.data.get('pin_code')
    
    try:
        user = CustomUser.objects.get(pin_code=pin_code, is_active=True)
    except CustomUser.DoesNotExist:
        return Response({'error': 'Invalid PIN code'}, status=status.HTTP_400_BAD_REQUEST)
    
    # Check if user is clocked in
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    if not last_event or last_event.event_type != 'in':
        return Response({'error': 'Not clocked in'}, status=status.HTTP_400_BAD_REQUEST)
    
    # Create clock out event
    clock_event = ClockEvent.objects.create(
        staff=user,
        event_type='out',
        device_id=request.META.get('HTTP_USER_AGENT', '')
    )
    
    return Response(ClockEventSerializer(clock_event).data)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def today_attendance(request):
    today = timezone.now().date()
    events = ClockEvent.objects.filter(
        staff__restaurant=request.user.restaurant,
        timestamp__date=today
    ).order_by('-timestamp')
    
    serializer = ClockEventSerializer(events, many=True)
    return Response(serializer.data)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def staff_attendance(request, user_id):
    staff = get_object_or_404(CustomUser, id=user_id, restaurant=request.user.restaurant)
    events = ClockEvent.objects.filter(staff=staff).order_by('-timestamp')[:50]
    
    serializer = ClockEventSerializer(events, many=True)
    return Response(serializer.data)

# NEW ENDPOINTS FOR REACT FRONTEND WITH GEOLOCATION
import base64, sys  # <--- ADD THIS IMPORT
from django.core.files.base import ContentFile  # <--- ADD THIS IMPORT

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def web_clock_in(request):
    """Clock-in for React frontend with geolocation"""
    # Removed for production

    user = request.user
    
    # Get geolocation data from request
    latitude = request.data.get('latitude')
    longitude = request.data.get('longitude')
    accuracy = request.data.get('accuracy')
    photo = request.data.get('photo_url') 
    # --- Get new photo and device_id data ---
    photo_data = request.data.get('photo')
    client_device_id = request.data.get('device_id') 

    # Validate required geolocation data
    if not latitude or not longitude:
        return Response({
            'error': 'Geolocation data required',
            'message': 'Please enable location services to clock in'
        }, status=status.HTTP_400_BAD_REQUEST)
    # Enforce GPS accuracy <= 10m when provided
    try:
        if accuracy is not None and float(accuracy) > 100:
            ip_address = get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='OTHER',
                entity_type='CLOCK_EVENT',
                entity_id=None,
                description=f'Web clock-in rejected due to weak GPS accuracy: {accuracy}m',
                old_values={},
                new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return Response({
                'error': 'GPS accuracy too weak',
                'message': 'Please move to open area or enable precise location (<=100m).',
            }, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        pass
    # Photo is optional for web clock-in; if provided, it will be saved.
    # Geofence enforcement remains mandatory.
    # Check if user is already clocked in
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    if last_event and last_event.event_type == 'in':
        return Response({
            'error': 'Already clocked in',
            'last_clock_in': last_event.timestamp.isoformat()
        }, status=status.HTTP_400_BAD_REQUEST)
    
    # Verify location is within restaurant premises
    restaurant = user.restaurant
    if restaurant.latitude and restaurant.longitude:
        distance = calculate_distance(
            float(restaurant.latitude),
            float(restaurant.longitude),
            float(latitude),
            float(longitude)
        )
        
        # Check if within allowed distance using restaurant geofence radius
        radius = float(restaurant.radius) if restaurant.radius else 100
        # Clamp radius to safe range (5m - 100m)
        radius = max(5.0, min(100.0, radius))
        if distance > radius:
            try:
                ip_address = get_client_ip(request)
                user_agent = request.META.get('HTTP_USER_AGENT', '')
                AuditLog.create_log(
                    restaurant=request.user.restaurant,
                    user=request.user,
                    action_type='OTHER',
                    entity_type='CLOCK_EVENT',
                    entity_id=None,
                    description=f'Web clock-in rejected: outside geofence (distance {distance:.2f}m, radius {radius:.2f}m)',
                    old_values={},
                    new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy, 'distance': distance, 'radius': radius},
                    ip_address=ip_address,
                    user_agent=user_agent,
                )
            except Exception:
                pass
            return Response({
                'error': 'Location verification failed',
                'message': f'You are {distance:.0f}m away from the restaurant. Please be within {radius:.0f}m to clock in.',
                'distance': distance,
                'within_range': False
            }, status=status.HTTP_400_BAD_REQUEST)
    else:
        # Restaurant location not set, allow clock-in with warning
        distance = None
    
    # Use client_device_id if provided, otherwise fall back to User-Agent
    device_id = client_device_id or request.META.get('HTTP_USER_AGENT', '')
    
    # Create clock in event (without photo first)
    clock_event = ClockEvent.objects.create(
        staff=user,
        event_type='in',
        latitude=latitude,
        longitude=longitude,
        device_id=device_id,  # <--- UPDATED THIS
        notes=f"Web clock-in | GPS Accuracy: {accuracy}m" if accuracy else "Web clock-in",
        photo=photo  
    )

    # --- Handle and save the photo ---
    if photo_data:
        try:
            # Split the base64 string (e.g., "data:image/png;base64,iVBOR...")
            format, imgstr = photo_data.split(';base64,') 
            ext = format.split('/')[-1] # e.g., "png"
            
            # Create a unique name for the file
            file_name = f"{user.id}_{clock_event.id}.{ext}"
            
            # Decode the string and create a Django ContentFile
            data = ContentFile(base64.b64decode(imgstr), name=file_name)
            
            # Save the file to the 'photo' field
            clock_event.photo.save(file_name, data, save=True)
            
        except Exception as e:
            # Handle error if base64 is malformed
            clock_event.notes += f" | Photo failed to save: {str(e)}"
            clock_event.save()

    
    response_data = {
        'session_id': str(clock_event.id),
        'clock_in_time': clock_event.timestamp.isoformat(),
        'location_verified': True,
        'distance_from_restaurant': f"{distance:.0f}m" if distance else "Unknown",
        'message': 'Clocked in successfully with location verification',
        'photo_url': clock_event.photo.url if clock_event.photo else None # <--- ADDED
    }
    # Audit success
    try:
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        AuditLog.create_log(
            restaurant=request.user.restaurant,
            user=request.user,
            action_type='CREATE',
            entity_type='CLOCK_EVENT',
            entity_id=str(clock_event.id),
            description='Web clock-in successful',
            old_values={},
            new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy, 'distance': distance},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception:
        pass

    # Start conversational checklist (WhatsApp step-by-step) so staff receive it immediately after clock-in
    try:
        now_today = timezone.now()
        active_qs = AssignedShift.objects.filter(
            staff=user,
            shift_date=now_today.date(),
            status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'],
        )
        active_shift = (
            active_qs.filter(start_time__lte=now_today, end_time__gte=now_today).first()
            or active_qs.order_by('start_time').first()
        )
        if active_shift and getattr(user, 'phone', None):
            notification_service.start_conversational_checklist_after_clock_in(user, active_shift)
    except Exception:
        pass

    return Response(response_data)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def web_clock_out(request):
    """Clock-out for React frontend with optional geolocation"""
    user = request.user
    
    # Get optional geolocation data
    latitude = request.data.get('latitude')
    longitude = request.data.get('longitude')
    accuracy = request.data.get('accuracy')
    # Enforce GPS accuracy <= 10m when provided
    try:
        if accuracy is not None and float(accuracy) > 100:
            ip_address = get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='OTHER',
                entity_type='CLOCK_EVENT',
                entity_id=None,
                description=f'Web clock-out rejected due to weak GPS accuracy: {accuracy}m',
                old_values={},
                new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return Response({
                'error': 'GPS accuracy too weak',
                'message': 'Please move to open area or enable precise location (<=100m).',
            }, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        pass
    
    # Check if user is clocked in
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    if not last_event or last_event.event_type != 'in':
        return Response({'error': 'Not clocked in'}, status=status.HTTP_400_BAD_REQUEST)
    
    # Create clock out event with optional geolocation
    clock_event = ClockEvent.objects.create(
        staff=user,
        event_type='out',
        latitude=latitude,
        longitude=longitude,
        device_id=request.META.get('HTTP_USER_AGENT', ''),
        notes=f"Web clock-out | GPS Accuracy: {accuracy}m" if accuracy else "Web clock-out"
    )
    
    # Calculate session duration
    duration = clock_event.timestamp - last_event.timestamp
    total_hours = duration.total_seconds() / 3600
    
    response_data = {
        'session_id': str(clock_event.id),
        'clock_out_time': clock_event.timestamp.isoformat(),
        'total_hours': round(total_hours, 2),
        'location_verified': bool(latitude and longitude),
        'message': 'Clocked out successfully'
    }
    # Audit success
    try:
        ip_address = get_client_ip(request)
        user_agent = request.META.get('HTTP_USER_AGENT', '')
        AuditLog.create_log(
            restaurant=request.user.restaurant,
            user=request.user,
            action_type='CREATE',
            entity_type='CLOCK_EVENT',
            entity_id=str(clock_event.id),
            description='Web clock-out successful',
            old_values={},
            new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy},
            ip_address=ip_address,
            user_agent=user_agent,
        )
    except Exception:
        pass

    # Send shift review template so staff can rate their shift (Miya: Hi {{1}}, how was your shift today?)
    if getattr(user, 'phone', None):
        try:
            notification_service.send_shift_review_request(user.phone, user.first_name)
        except Exception:
            pass

    return Response(response_data)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def current_session(request):
    """Get current active clock session with location info"""
    user = request.user
    
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    
    if last_event and last_event.event_type == 'in':
        # Calculate current session duration
        duration = timezone.now() - last_event.timestamp
        current_hours = duration.total_seconds() / 3600
        
        session_data = {
            'id': str(last_event.id),
            'clock_in': last_event.timestamp.isoformat(),
            'duration_hours': round(current_hours, 2),
            'location': {
                'latitude': last_event.latitude,
                'longitude': last_event.longitude
            } if last_event.latitude and last_event.longitude else None
        }
        
        return Response({
            'currentSession': session_data,
            'is_clocked_in': True
        })
    else:
        return Response({
            'currentSession': None,
            'is_clocked_in': False
        })

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def restaurant_location(request):
    """Get restaurant location for geolocation verification"""
    user = request.user
    restaurant = user.restaurant
    
    if not restaurant.latitude or not restaurant.longitude:
        return Response({
            'error': 'Restaurant location not set',
            'message': 'Please contact administrator to set restaurant location'
        }, status=status.HTTP_400_BAD_REQUEST)
    
    return Response({
        'restaurant': {
            'name': restaurant.name,
            'address': restaurant.address,
            'latitude': float(restaurant.latitude),
            'longitude': float(restaurant.longitude),
            'geofence_radius': float(restaurant.radius) if restaurant.radius else 100,  # meters (5-100m range)
            'language': getattr(restaurant, 'language', 'en') or 'en',
            'timezone': getattr(restaurant, 'timezone', None),
        }
    })

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def verify_location(request):
    """Verify if current location is within restaurant premises"""
    user = request.user
    latitude = request.data.get('latitude')
    longitude = request.data.get('longitude')
    accuracy = request.data.get('accuracy')
    
    if not latitude or not longitude:
        return Response({
            'error': 'Location data required',
            'within_range': False
        }, status=status.HTTP_400_BAD_REQUEST)
    # Enforce GPS accuracy <= 10m when provided
    try:
        if accuracy is not None and float(accuracy) > 100:
            ip_address = get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='OTHER',
                entity_type='GEOLOCATION_VERIFY',
                entity_id=None,
                description=f'Location verification rejected due to weak GPS accuracy: {accuracy}m',
                old_values={},
                new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy},
                ip_address=ip_address,
                user_agent=user_agent,
            )
            return Response({
                'error': 'GPS accuracy too weak',
                'within_range': False,
                'message': 'Please move to open area or enable precise location (<=100m).'
            }, status=status.HTTP_400_BAD_REQUEST)
    except Exception:
        pass
    
    restaurant = user.restaurant
    if not restaurant.latitude or not restaurant.longitude:
        return Response({
            'error': 'Restaurant location not configured',
            'within_range': False
        }, status=status.HTTP_400_BAD_REQUEST)
    
    distance = calculate_distance(
        float(restaurant.latitude),
        float(restaurant.longitude),
        float(latitude),
        float(longitude)
    )
    
    # Use restaurant geofence radius with safe clamp
    radius = float(restaurant.radius) if restaurant.radius else 100
    radius = max(5.0, min(100.0, radius))
    within_range = distance <= radius

    # Log failures only to avoid excessive logs from frequent checks
    try:
        if not within_range:
            ip_address = get_client_ip(request)
            user_agent = request.META.get('HTTP_USER_AGENT', '')
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='OTHER',
                entity_type='GEOLOCATION_VERIFY',
                entity_id=None,
                description=f'Location verification failed: outside geofence (distance {distance:.2f}m, radius {radius:.2f}m)',
                old_values={},
                new_values={'latitude': latitude, 'longitude': longitude, 'accuracy': accuracy, 'distance': distance, 'radius': radius},
                ip_address=ip_address,
                user_agent=user_agent,
            )
    except Exception:
        pass

    return Response({
        'within_range': within_range,
        'distance': round(distance, 2),
        'restaurant_location': {
            'latitude': float(restaurant.latitude),
            'longitude': float(restaurant.longitude)
        },
        'current_location': {
            'latitude': float(latitude),
            'longitude': float(longitude)
        }
    })

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def start_break(request):
    user = request.user
    
    # Check if clocked in and not already on break
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    if not last_event or last_event.event_type != 'in':
        return Response({'error': 'Not clocked in'}, status=status.HTTP_400_BAD_REQUEST)
    
    if ClockEvent.objects.filter(staff=user, event_type='break_start', timestamp__gt=last_event.timestamp).exists():
        # Check for an unmatched break_start after the last clock_in
        last_break_start = ClockEvent.objects.filter(staff=user, event_type='break_start', timestamp__gt=last_event.timestamp).order_by('-timestamp').first()
        last_break_end = ClockEvent.objects.filter(staff=user, event_type='break_end', timestamp__gt=last_event.timestamp).order_by('-timestamp').first()
        
        if last_break_start and (not last_break_end or last_break_start.timestamp > last_break_end.timestamp):
            return Response({'error': 'Already on break'}, status=status.HTTP_400_BAD_REQUEST)
            
    clock_event = ClockEvent.objects.create(
        staff=user,
        event_type='break_start',
        device_id=request.META.get('HTTP_USER_AGENT', '')
    )
    
    return Response(ClockEventSerializer(clock_event).data, status=status.HTTP_201_CREATED)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def end_break(request):
    user = request.user
    
    # Check if on break
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    if not last_event or last_event.event_type != 'in':
        return Response({'error': 'Not clocked in'}, status=status.HTTP_400_BAD_REQUEST)
        
    last_break_start = ClockEvent.objects.filter(staff=user, event_type='break_start', timestamp__gt=last_event.timestamp).order_by('-timestamp').first()
    last_break_end = ClockEvent.objects.filter(staff=user, event_type='break_end', timestamp__gt=last_event.timestamp).order_by('-timestamp').first()
    
    if not last_break_start or (last_break_end and last_break_end.timestamp > last_break_start.timestamp):
        return Response({'error': 'Not currently on break'}, status=status.HTTP_400_BAD_REQUEST)
        
    clock_event = ClockEvent.objects.create(
        staff=user,
        event_type='break_end',
        device_id=request.META.get('HTTP_USER_AGENT', '')
    )
    
    return Response(ClockEventSerializer(clock_event).data, status=status.HTTP_201_CREATED)

# Keep your existing timecards and staff_dashboard_data functions
@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def timecards(request):
    """Get timecards for date range"""
    user = request.user
    start_date = request.GET.get('start')
    end_date = request.GET.get('end')
    
    if not start_date or not end_date:
        today = timezone.now().date()
        start_date = today.replace(day=1)
        end_date = today
    
    events = ClockEvent.objects.filter(
        staff=user,
        timestamp__date__range=[start_date, end_date]
    ).order_by('timestamp')
    
    sessions = []
    current_session = None
    
    for event in events:
        if event.event_type == 'in':
            current_session = {
                'date': event.timestamp.date().isoformat(),
                'clock_in': event.timestamp,
                'clock_out': None,
                'total_hours': 0,
                'status': 'incomplete',
                'location_in': {
                    'latitude': event.latitude,
                    'longitude': event.longitude
                } if event.latitude and event.longitude else None
            }
        elif event.event_type == 'out' and current_session:
            current_session['clock_out'] = event.timestamp
            current_session['location_out'] = {
                'latitude': event.latitude,
                'longitude': event.longitude
            } if event.latitude and event.longitude else None
            
            duration = event.timestamp - current_session['clock_in']
            current_session['total_hours'] = round(duration.total_seconds() / 3600, 2)
            current_session['status'] = 'completed'
            
            sessions.append(current_session)
            current_session = None
    
    if current_session:
        duration = timezone.now() - current_session['clock_in']
        current_session['total_hours'] = round(duration.total_seconds() / 3600, 2)
        sessions.append(current_session)
    
    return Response(sessions)

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def staff_dashboard_data(request):
    """Get staff dashboard data"""
    user = request.user
    
    # Get current session with location
    current_session_data = None
    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
    if last_event and last_event.event_type == 'in':
        current_session_data = {
            'id': str(last_event.id),
            'clock_in': last_event.timestamp.isoformat(),
            'location': {
                'latitude': last_event.latitude,
                'longitude': last_event.longitude
            } if last_event.latitude and last_event.longitude else None
        }
    
    # Calculate total break duration for the current session
    total_break_seconds = 0
    if last_event and last_event.event_type == 'in':
        break_events = ClockEvent.objects.filter(
            staff=user,
            timestamp__gt=last_event.timestamp, # Only consider breaks after clock-in
            event_type__in=['break_start', 'break_end']
        ).order_by('timestamp')
        
        current_break_start = None
        for event in break_events:
            if event.event_type == 'break_start':
                current_break_start = event.timestamp
            elif event.event_type == 'break_end' and current_break_start:
                total_break_seconds += (event.timestamp - current_break_start).total_seconds()
                current_break_start = None
        
        # If a break is currently active
        if current_break_start:
            total_break_seconds += (timezone.now() - current_break_start).total_seconds()
            
    # Calculate weekly stats
    week_ago = timezone.now() - timezone.timedelta(days=7)
    week_events = ClockEvent.objects.filter(
        staff=user,
        timestamp__gte=week_ago
    ).order_by('timestamp')
    
    weekly_hours = 0
    session_count = 0
    current_session = None
    
    for event in week_events:
        if event.event_type == 'in':
            current_session = {'in': event.timestamp}
        elif event.event_type == 'out' and current_session:
            duration = event.timestamp - current_session['in']
            weekly_hours += duration.total_seconds() / 3600
            session_count += 1
            current_session = None
    
    today = timezone.now().date()
    # Use wall-date field to avoid timezone edge cases for late-night shifts
    todays_shift = AssignedShift.objects.filter(
        staff=user,
        shift_date=today
    ).order_by('start_time').first()
    
    todays_shift_data = ShiftSerializer(todays_shift).data if todays_shift else None
    
    hourly_rate = 15.0
    earnings_this_week = round(weekly_hours * hourly_rate, 2)
    
    return Response({
        'currentSession': current_session_data,
        'is_clocked_in': bool(last_event and last_event.event_type == 'in'),
        'current_break_duration_minutes': round(total_break_seconds / 60, 2),
        'todaysShift': todays_shift_data,
        'restaurant_location': {
            'latitude': float(user.restaurant.latitude) if user.restaurant.latitude else None,
            'longitude': float(user.restaurant.longitude) if user.restaurant.longitude else None
        },
        'stats': {
            'hoursThisWeek': round(weekly_hours, 2),
            'shiftsThisWeek': session_count,
            'earningsThisWeek': earnings_this_week
        },
        'geofence_radius': float(user.restaurant.radius) if user.restaurant and user.restaurant.radius else 100,  # meters (5-100m range)
        'is_on_break': bool(last_event and last_event.event_type == 'break_start' and not current_break_start), # Simplified check for active break
        'account_status': 'active' if getattr(user, 'is_active', True) else 'inactive',
        'is_active': bool(getattr(user, 'is_active', True))
    })

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def attendance_history(request, user_id=None):
    """Get attendance history for a staff member or the current user"""
    start_date_str = request.GET.get('start_date')
    end_date_str = request.GET.get('end_date')

    if not start_date_str or not end_date_str:
        return Response({'error': 'start_date and end_date parameters are required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)

    try:
        start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date()
        end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

    if user_id:
        # For managers to view specific staff's history
        if not (request.user.role == 'SUPER_ADMIN' or request.user.role == 'ADMIN'):
            return Response({'error': 'Permission denied'}, status=status.HTTP_403_FORBIDDEN)
        staff_member = get_object_or_404(CustomUser, id=user_id, restaurant=request.user.restaurant)
    else:
        # For staff to view their own history
        staff_member = request.user

    events = ClockEvent.objects.filter(
        staff=staff_member,
        timestamp__date__range=[start_date, end_date]
    ).order_by('timestamp')

    attendance_records = []
    current_session = None
    current_break_start = None

    for event in events:
        if event.event_type == 'in':
            if current_session:
                # Previous session was not properly closed, close it now
                duration = event.timestamp - current_session['clock_in']
                current_session['total_hours'] = round(duration.total_seconds() / 3600, 2)
                current_session['status'] = 'incomplete'
                attendance_records.append(current_session)
            
            current_session = {
                'date': event.timestamp.date().isoformat(),
                'clock_in': event.timestamp,
                'clock_out': None,
                'total_hours': 0,
                'breaks': [],
                'status': 'active',
            }
        elif event.event_type == 'out' and current_session:
            current_session['clock_out'] = event.timestamp
            if current_session['clock_in']:
                duration = event.timestamp - current_session['clock_in']
                current_session['total_hours'] = round(duration.total_seconds() / 3600, 2)
                current_session['status'] = 'completed'
            attendance_records.append(current_session)
            current_session = None
            current_break_start = None # Reset break state
        elif event.event_type == 'break_start' and current_session:
            current_break_start = event.timestamp
        elif event.event_type == 'break_end' and current_break_start and current_session:
            break_duration = event.timestamp - current_break_start
            current_session['breaks'].append({
                'start': current_break_start,
                'end': event.timestamp,
                'duration_minutes': round(break_duration.total_seconds() / 60, 2),
            })
            current_break_start = None
    
    # Handle any open session at the end of the time range
    if current_session:
        if current_session['status'] == 'active' and not current_session['clock_out']:
            duration = timezone.now() - current_session['clock_in']
            current_session['total_hours'] = round(duration.total_seconds() / 3600, 2)
        
        # If there's an open break, add its duration to the current session
        if current_break_start:
            break_duration = timezone.now() - current_break_start
            current_session['breaks'].append({
                'start': current_break_start,
                'end': None,
                'duration_minutes': round(break_duration.total_seconds() / 60, 2),
            })
        attendance_records.append(current_session)
        
    return Response(attendance_records)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, IsAdminOrManager])
def manager_clock_in(request, staff_id):
    """
    Manager override: clock in a staff member without location (e.g. device failure, GPS issues).
    Requires mandatory reason. Records staff_id, shift_id, manager_id, override_reason.
    clock_in_method = manager_override. Idempotent (no duplicate clock-in).
    """
    import traceback
    try:
        return _manager_clock_in_impl(request, staff_id)
    except Http404:
        raise
    except Exception as e:
        logger.exception("Manager clock-in failed")
        detail = str(e)
        if settings.DEBUG:
            detail = f"{detail}\n{traceback.format_exc()}"
        return Response(
            {'error': 'Manager clock-in failed.', 'detail': detail},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR
        )


def _manager_clock_in_impl(request, staff_id):
    reason = (request.data.get('reason') or '').strip()
    if not reason:
        return Response(
            {'error': 'Reason is required for manager clock-in override.'},
            status=status.HTTP_400_BAD_REQUEST
        )
    shift_id = request.data.get('shift_id')
    staff = get_object_or_404(CustomUser, id=staff_id, is_active=True)
    manager_restaurant_id = getattr(request.user, 'restaurant_id', None)
    if not staff.restaurant_id or staff.restaurant_id != manager_restaurant_id:
        return Response({'error': 'Staff not in your restaurant'}, status=status.HTTP_403_FORBIDDEN)
    last_event = ClockEvent.objects.filter(staff=staff).order_by('-timestamp').first()
    if last_event and last_event.event_type == 'in':
        return Response({
            'error': 'Staff is already clocked in',
            'last_clock_in': last_event.timestamp.isoformat()
        }, status=status.HTTP_400_BAD_REQUEST)

    from django.db import transaction
    from django.db.utils import OperationalError, IntegrityError

    manager_name = (request.user.get_full_name() or getattr(request.user, 'email', None) or 'Manager') if request.user else 'Manager'
    staff_name = (staff.get_full_name() or getattr(staff, 'email', None) or 'Staff') if staff else 'Staff'
    notes_text = f"Manager override by {manager_name}: {reason[:500]}"

    try:
        with transaction.atomic():
            clock_event = ClockEvent.objects.create(
                staff=staff,
                event_type='in',
                device_id=ClockEvent.CLOCK_IN_METHOD_OVERRIDE,
                notes=notes_text,
                performed_by=request.user,
                override_reason=reason,
            )
            if shift_id:
                shift = AssignedShift.objects.filter(
                    pk=shift_id,
                    staff=staff,
                    schedule__restaurant_id=staff.restaurant_id,
                    status__in=['SCHEDULED', 'CONFIRMED'],
                ).first()
                if shift:
                    shift.status = 'IN_PROGRESS'
                    shift.save(update_fields=['status'])
    except (OperationalError, IntegrityError) as e:
        logger.warning("Manager clock-in: create with performed_by/override_reason failed (migration not applied?): %s", e)
        with transaction.atomic():
            clock_event = ClockEvent.objects.create(
                staff=staff,
                event_type='in',
                device_id=ClockEvent.CLOCK_IN_METHOD_OVERRIDE,
                notes=notes_text,
            )
            if shift_id:
                shift = AssignedShift.objects.filter(
                    pk=shift_id,
                    staff=staff,
                    schedule__restaurant_id=staff.restaurant_id,
                    status__in=['SCHEDULED', 'CONFIRMED'],
                ).first()
                if shift:
                    shift.status = 'IN_PROGRESS'
                    shift.save(update_fields=['status'])

    try:
        AuditLog.create_log(
            restaurant=getattr(request.user, 'restaurant', None),
            user=request.user,
            action_type='CREATE',
            entity_type='CLOCK_EVENT',
            entity_id=str(clock_event.id),
            description='Manager clock-in override',
            old_values={},
            new_values={
                'clock_in_method': ClockEvent.CLOCK_IN_METHOD_OVERRIDE,
                'staff_id': str(staff.id),
                'staff_name': staff_name,
                'shift_id': str(shift_id) if shift_id else None,
                'manager_id': str(request.user.id),
                'override_reason': reason,
            },
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
    except Exception as e:
        logger.warning("Manager clock-in: audit log failed: %s", e)

    if getattr(staff, 'phone', None):
        try:
            notification_service.send_whatsapp_text(
                staff.phone,
                "You have been clocked in by your manager."
            )
        except Exception:
            pass

    try:
        payload = ClockEventSerializer(clock_event).data
    except Exception as e:
        logger.warning("Manager clock-in: serialize failed: %s", e)
        payload = {'id': str(clock_event.id), 'event_type': 'in', 'timestamp': clock_event.timestamp.isoformat() if clock_event.timestamp else None}

    return Response(payload, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated, IsAdminOrManager])
def manager_clock_out(request, staff_id):
    """
    Manager/admin/super_admin clocks out a staff member (e.g. lost phone).
    No geolocation required.
    """
    staff = get_object_or_404(CustomUser, id=staff_id, is_active=True)
    if not staff.restaurant_id or staff.restaurant_id != request.user.restaurant_id:
        return Response({'error': 'Staff not in your restaurant'}, status=status.HTTP_403_FORBIDDEN)
    last_event = ClockEvent.objects.filter(staff=staff).order_by('-timestamp').first()
    if not last_event or last_event.event_type != 'in':
        return Response({'error': 'Staff is not clocked in'}, status=status.HTTP_400_BAD_REQUEST)
    duration = timezone.now() - last_event.timestamp
    clock_event = ClockEvent.objects.create(
        staff=staff,
        event_type='out',
        device_id=request.META.get('HTTP_USER_AGENT', ''),
        notes=f"Manager override (clock-out by {request.user.get_full_name()}) - lost phone",
    )
    try:
        AuditLog.create_log(
            restaurant=request.user.restaurant,
            user=request.user,
            action_type='CREATE',
            entity_type='CLOCK_EVENT',
            entity_id=str(clock_event.id),
            description=f'Manager clock-out for staff {staff.get_full_name()} (lost phone)',
            old_values={},
            new_values={'staff_id': str(staff.id), 'staff_name': staff.get_full_name(), 'duration_hours': round(duration.total_seconds() / 3600, 2)},
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', ''),
        )
    except Exception:
        pass
    return Response({
        **ClockEventSerializer(clock_event).data,
        'total_hours': round(duration.total_seconds() / 3600, 2),
    }, status=status.HTTP_201_CREATED)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def agent_clock_in(request):
    """
    Clock-in for Lua Agent on behalf of staff.
    Bypasses PIN requirement but requires Agent API Key.
    """
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        staff_id = request.data.get('staff_id')
        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        timestamp = request.data.get('timestamp')

        if not staff_id:
            return Response({'error': 'staff_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        user = get_object_or_404(CustomUser, id=staff_id, is_active=True)

        # Location required: no bypass for staff clock-in
        if latitude is None or longitude is None:
            return Response({
                'error': 'latitude and longitude required',
                'message': 'Please share live location to clock in.',
            }, status=status.HTTP_400_BAD_REQUEST)
        rest = getattr(user, 'restaurant', None)
        if rest and getattr(rest, 'latitude', None) is not None and getattr(rest, 'longitude', None) is not None and getattr(rest, 'radius', None) is not None:
            try:
                dist = calculate_distance(
                    float(rest.latitude), float(rest.longitude),
                    float(latitude), float(longitude)
                )
                radius = float(rest.radius or 100)
                if dist > radius:
                    return Response({
                        'error': 'Outside geofence',
                        'message': "You are not within the restaurant's approved location zone. Please move closer and try again.",
                    }, status=status.HTTP_400_BAD_REQUEST)
            except (TypeError, ValueError):
                return Response({'error': 'Invalid coordinates'}, status=status.HTTP_400_BAD_REQUEST)
        latitude, longitude = float(latitude), float(longitude)

        # Check if user is already clocked in
        last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
        if last_event and last_event.event_type == 'in':
            return Response({
                'error': 'Already clocked in',
                'last_clock_in': last_event.timestamp
            }, status=status.HTTP_400_BAD_REQUEST)

        clock_event = ClockEvent.objects.create(
            staff=user,
            event_type='in',
            latitude=latitude,
            longitude=longitude,
            device_id="Lua Agent",
            notes="Clock-in via WhatsApp Agent (location verified)"
        )
        if timestamp:
            try:
                # If timestamp provided, we could override it or just log it
                pass
            except Exception:
                pass

        # Start conversational checklist (WhatsApp) so staff receive step-by-step tasks after clock-in
        try:
            now_today = timezone.now()
            active_qs = AssignedShift.objects.filter(
                staff=user,
                shift_date=now_today.date(),
                status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'],
            )
            active_shift = (
                active_qs.filter(start_time__lte=now_today, end_time__gte=now_today).first()
                or active_qs.order_by('start_time').first()
            )
            if active_shift and getattr(user, 'phone', None):
                notification_service.start_conversational_checklist_after_clock_in(user, active_shift)
        except Exception:
            pass

        return Response(ClockEventSerializer(clock_event).data)

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@authentication_classes([])
def agent_clock_in_by_phone(request):
    """
    Clock-in by phone (for Miya fallback when staff say "clock in").
    Resolves staff from phone; optionally validates location against restaurant geofence.
    Auth: Bearer LUA_WEBHOOK_API_KEY.
    Body: phone (required), latitude (optional), longitude (optional).
    Returns: success, message_for_user (for Miya to send), staff_id, error.
    """
    try:
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        if not expected_key:
            return Response({
                'success': False,
                'error': 'Agent key not configured',
                'message_for_user': "We couldn't complete your request. Please try again later.",
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        phone = (request.data.get('phone') or request.data.get('phoneNumber') or '').strip()
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if not clean_phone or len(clean_phone) < 6:
            return Response({
                'success': False,
                'error': 'Invalid or missing phone',
                'message_for_user': "We couldn't find your account. Please contact your manager to be added.",
            }, status=status.HTTP_400_BAD_REQUEST)

        from accounts.services import _find_active_user_by_phone
        user = _find_active_user_by_phone(clean_phone)
        if not user:
            return Response({
                'success': False,
                'error': 'Staff not found for this phone',
                'message_for_user': "We couldn't find your account. Please contact your manager to be added.",
            }, status=status.HTTP_404_NOT_FOUND)

        latitude = request.data.get('latitude')
        longitude = request.data.get('longitude')
        rest = getattr(user, 'restaurant', None)
        # Mandatory location: staff cannot clock in without live location (no bypass)
        if latitude is None or longitude is None:
            # Send the official template or interactive message with Share Location button ourselves.
            # Miya must NOT send plain text—the user must get the button from this request.
            ok, _ = notification_service.send_whatsapp_location_request(
                clean_phone,
                "Please share your live location to clock in.",
            )
            return Response({
                'success': False,
                'error': 'Location required',
                'location_request_sent': ok,
                'message_for_user': "Tap Share Location above to clock in." if ok else "Share your location to clock in.",
            }, status=status.HTTP_400_BAD_REQUEST)
        if not rest or not getattr(rest, 'latitude', None) or not getattr(rest, 'longitude', None) or not getattr(rest, 'radius', None):
            return Response({
                'success': False,
                'error': 'Geofence not configured',
                'message_for_user': "Location check is not set up for your restaurant. Please contact your manager to clock in.",
            }, status=status.HTTP_400_BAD_REQUEST)
        try:
            lat_f, lon_f = float(latitude), float(longitude)
            radius = float(getattr(rest, 'radius', None) or 100)
            dist = calculate_distance(
                float(rest.latitude), float(rest.longitude),
                lat_f, lon_f
            )
            if dist > radius:
                return Response({
                    'success': False,
                    'error': 'Outside geofence',
                    'message_for_user': (
                        "You are not within the restaurant's approved location zone. Please move closer and try again."
                    ),
                }, status=status.HTTP_400_BAD_REQUEST)
            latitude, longitude = lat_f, lon_f
        except (TypeError, ValueError):
            return Response({
                'success': False,
                'error': 'Invalid coordinates',
                'message_for_user': "Please share your live location to clock in.",
            }, status=status.HTTP_400_BAD_REQUEST)

        last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
        if last_event and last_event.event_type == 'in':
            return Response({
                'success': False,
                'error': 'Already clocked in',
                'message_for_user': "You are already clocked in.",
            }, status=status.HTTP_400_BAD_REQUEST)

        clock_event = ClockEvent.objects.create(
            staff=user,
            event_type='in',
            latitude=latitude,
            longitude=longitude,
            device_id="Lua Agent",
            notes="Clock-in via Miya (by phone, location verified)",
        )

        try:
            now_today = timezone.now()
            active_qs = AssignedShift.objects.filter(
                Q(staff=user) | Q(staff_members=user),
                shift_date=now_today.date(),
                status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'],
            ).distinct()
            active_shift = (
                active_qs.filter(start_time__lte=now_today, end_time__gte=now_today).first()
                or active_qs.order_by('start_time').first()
            )
            if active_shift:
                started = notification_service.start_conversational_checklist_after_clock_in(
                    user, active_shift, phone_digits=clean_phone
                )
                if not started and getattr(user, 'phone', None):
                    notification_service.start_conversational_checklist_after_clock_in(user, active_shift)
        except Exception as e:
            logger.warning(
                "start_conversational_checklist_after_clock_in failed after agent_clock_in_by_phone: %s", e
            )

        first_name = getattr(user, 'first_name', None) or 'Team Member'
        return Response({
            'success': True,
            'staff_id': str(user.id),
            'message_for_user': f"You're clocked in! Have a great shift, {first_name}.",
            'clock_event_id': str(clock_event.id),
        }, status=status.HTTP_200_OK)

    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message_for_user': "Something went wrong. Please try again or contact your manager.",
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
@authentication_classes([])
def agent_clock_out_by_phone(request):
    """
    Clock-out by phone (for Miya when staff say "clock out" in chat).
    Auth: Bearer LUA_WEBHOOK_API_KEY. Body: phone (required).
    Returns: success, message_for_user.
    """
    try:
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        if not expected_key:
            return Response({
                'success': False,
                'error': 'Agent key not configured',
                'message_for_user': "We couldn't complete your request. Please try again later.",
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'success': False, 'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        phone = (request.data.get('phone') or request.data.get('phoneNumber') or '').strip()
        clean_phone = ''.join(filter(str.isdigit, str(phone)))
        if not clean_phone or len(clean_phone) < 6:
            return Response({
                'success': False,
                'error': 'Invalid or missing phone',
                'message_for_user': "We couldn't find your account. Please contact your manager.",
            }, status=status.HTTP_400_BAD_REQUEST)

        from accounts.services import _find_active_user_by_phone
        user = _find_active_user_by_phone(clean_phone)
        if not user:
            return Response({
                'success': False,
                'error': 'Staff not found for this phone',
                'message_for_user': "We couldn't find your account. Please contact your manager.",
            }, status=status.HTTP_404_NOT_FOUND)

        last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
        if not last_event or last_event.event_type != 'in':
            return Response({
                'success': False,
                'error': 'Not clocked in',
                'message_for_user': "You are not clocked in. No need to clock out.",
            }, status=status.HTTP_400_BAD_REQUEST)

        duration = timezone.now() - last_event.timestamp
        hours = round(duration.total_seconds() / 3600, 2)
        ClockEvent.objects.create(
            staff=user,
            event_type='out',
            device_id="Lua Agent",
            notes="Clock-out via Miya (by phone)",
        )
        first_name = getattr(user, 'first_name', None) or 'Team Member'
        return Response({
            'success': True,
            'message_for_user': f"You're clocked out. You worked {hours} hours today. Have a great rest of your day, {first_name}!",
        }, status=status.HTTP_200_OK)
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e),
            'message_for_user': "Something went wrong. Please try again or contact your manager.",
        }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['POST'])
@permission_classes([permissions.AllowAny])
def agent_clock_out(request):
    """
    Clock-out for Lua Agent on behalf of staff.
    """
    try:
        # Validate Agent Key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        staff_id = request.data.get('staff_id')

        if not staff_id:
            return Response({'error': 'staff_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        user = get_object_or_404(CustomUser, id=staff_id, is_active=True)
        
        # Check if user is clocked in
        last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
        if not last_event or last_event.event_type != 'in':
            return Response({'error': 'Not clocked in'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Create clock out event
        clock_event = ClockEvent.objects.create(
            staff=user,
            event_type='out',
            device_id="Lua Agent",
            notes="Clock-out via WhatsApp Agent"
        )
        
        return Response(ClockEventSerializer(clock_event).data)
    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


@api_view(['GET'])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_attendance_report(request):
    """
    Get attendance and punctuality report for the agent.
    Shows who is on duty, who has clocked in, and who is late.
    """
    try:
        # Validate agent key
        auth_header = request.headers.get('Authorization')
        expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
        
        if not expected_key:
            return Response({'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
             
        if not auth_header or auth_header != f"Bearer {expected_key}":
            return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)

        restaurant_id = request.query_params.get('restaurant_id')
        date_str = request.query_params.get('date') or timezone.now().date().isoformat()
        
        if not restaurant_id:
            return Response({'error': 'restaurant_id is required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            report_date = datetime.strptime(date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Get all scheduled shifts for today
        shifts = AssignedShift.objects.filter(
            weekly_schedule__restaurant_id=restaurant_id,
            shift_date=report_date
        ).select_related('staff')

        # 2. Get all clock-in events for today
        clock_ins = ClockEvent.objects.filter(
            staff__restaurant_id=restaurant_id,
            timestamp__date=report_date,
            event_type='in'
        )

        # Map clock-ins by staff_id
        staff_clock_ins = {str(c.staff_id): c.timestamp for c in clock_ins}

        report = []
        for shift in shifts:
            staff = shift.staff
            clock_in_time = staff_clock_ins.get(str(staff.id))
            
            status_text = "Scheduled"
            lateness_minutes = 0
            
            if clock_in_time:
                status_text = "Present"
                # Combine date and time for comparison
                shift_start = timezone.make_aware(datetime.combine(shift.shift_date, shift.start_time))
                if clock_in_time > shift_start:
                    diff = clock_in_time - shift_start
                    lateness_minutes = int(diff.total_seconds() / 60)
                    if lateness_minutes > 5: # 5 min grace period
                        status_text = "Late"
            else:
                # If shift has already started, mark as absent/late to clock in
                now = timezone.now()
                shift_start = timezone.make_aware(datetime.combine(shift.shift_date, shift.start_time))
                if now > shift_start:
                    status_text = "Missing"

            report.append({
                'staff_id': str(staff.id),
                'staff_name': f"{staff.first_name} {staff.last_name}",
                'role': shift.role or staff.role,
                'shift_start': shift.start_time.strftime('%H:%M'),
                'shift_end': shift.end_time.strftime('%H:%M'),
                'clock_in': clock_in_time.strftime('%H:%M') if clock_in_time else None,
                'status': status_text,
                'lateness_minutes': lateness_minutes
            })

        return Response({
            'date': date_str,
            'restaurant_id': restaurant_id,
            'summary': report
        })

    except Exception as e:
        return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


    # Example of sending a notification (can be triggered by various events)
    # send_realtime_notification(user, 'You viewed your dashboard!', description='Just a friendly reminder.', level='info')
