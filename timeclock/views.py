from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404

from accounts.utils import calculate_distance
from .models import ClockEvent, Shift
from .serializers import ClockEventSerializer, ClockInSerializer
from accounts.models import CustomUser

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
        response_data['location_verified'] = True
        
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
def today_attendance(request):
    today = timezone.now().date()
    events = ClockEvent.objects.filter(
        staff__restaurant=request.user.restaurant,
        timestamp__date=today
    ).order_by('-timestamp')
    
    serializer = ClockEventSerializer(events, many=True)
    return Response(serializer.data)

@api_view(['GET'])
def staff_attendance(request, user_id):
    staff = get_object_or_404(CustomUser, id=user_id, restaurant=request.user.restaurant)
    events = ClockEvent.objects.filter(staff=staff).order_by('-timestamp')[:50]  # Last 50 events
    
    serializer = ClockEventSerializer(events, many=True)
    return Response(serializer.data)