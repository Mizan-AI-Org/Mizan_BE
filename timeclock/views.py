from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from notifications.utils import send_realtime_notification

from accounts.utils import calculate_distance
from .models import ClockEvent
from .serializers import ClockEventSerializer, ClockInSerializer, ShiftSerializer
from accounts.models import CustomUser
from scheduling.models import AssignedShift

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

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def web_clock_in(request):
    """Clock-in for React frontend with geolocation"""
    user = request.user
    
    # Get geolocation data from request
    latitude = request.data.get('latitude')
    longitude = request.data.get('longitude')
    accuracy = request.data.get('accuracy')
    
    # Validate required geolocation data
    if not latitude or not longitude:
        return Response({
            'error': 'Geolocation data required',
            'message': 'Please enable location services to clock in'
        }, status=status.HTTP_400_BAD_REQUEST)
    
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
        
        # Check if within allowed distance (e.g., 100 meters)
        if distance > 100:
            return Response({
                'error': 'Location verification failed',
                'message': f'You are {distance:.0f}m away from the restaurant. Please be within 100m to clock in.',
                'distance': distance,
                'within_range': False
            }, status=status.HTTP_400_BAD_REQUEST)
    else:
        # Restaurant location not set, allow clock-in with warning
        distance = None
    
    # Create clock in event with geolocation
    clock_event = ClockEvent.objects.create(
        staff=user,
        event_type='in',
        latitude=latitude,
        longitude=longitude,
        device_id=request.META.get('HTTP_USER_AGENT', ''),
        notes=f"Web clock-in | GPS Accuracy: {accuracy}m" if accuracy else "Web clock-in"
    )
    
    response_data = {
        'session_id': str(clock_event.id),
        'clock_in_time': clock_event.timestamp.isoformat(),
        'location_verified': True,
        'distance_from_restaurant': f"{distance:.0f}m" if distance else "Unknown",
        'message': 'Clocked in successfully with location verification'
    }
    
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
            'geofence_radius': 100  # meters
        }
    })

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def verify_location(request):
    """Verify if current location is within restaurant premises"""
    user = request.user
    latitude = request.data.get('latitude')
    longitude = request.data.get('longitude')
    
    if not latitude or not longitude:
        return Response({
            'error': 'Location data required',
            'within_range': False
        }, status=status.HTTP_400_BAD_REQUEST)
    
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
    
    within_range = distance <= 100  # 100 meter radius
    
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
    todays_shift = AssignedShift.objects.filter(
        staff=user,
        start_time__date=today
    ).first()
    
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
        'geofence_radius': 100,  # meters
        'is_on_break': bool(last_event and last_event.event_type == 'break_start' and not current_break_start) # Simplified check for active break
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

    # Example of sending a notification (can be triggered by various events)
    # send_realtime_notification(user, 'You viewed your dashboard!', description='Just a friendly reminder.', level='info')