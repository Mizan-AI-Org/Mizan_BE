from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from datetime import timedelta
from .models import ScheduleTemplate, TemplateShift, WeeklySchedule
from .serializers import (
    ScheduleTemplateSerializer, TemplateShiftSerializer, 
    WeeklyScheduleSerializer, ShiftAssignmentSerializer
)
from timeclock.models import Shift
from accounts.models import CustomUser

@api_view(['GET', 'POST'])
def schedule_templates(request):
    if request.method == 'GET':
        templates = ScheduleTemplate.objects.filter(restaurant=request.user.restaurant)
        serializer = ScheduleTemplateSerializer(templates, many=True)
        return Response(serializer.data)
    
    elif request.method == 'POST':
        serializer = ScheduleTemplateSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(restaurant=request.user.restaurant)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
def current_schedule(request):
    today = timezone.now().date()
    week_start = today - timedelta(days=today.weekday())
    
    schedule, created = WeeklySchedule.objects.get_or_create(
        restaurant=request.user.restaurant,
        week_start=week_start,
        defaults={'week_end': week_start + timedelta(days=6)}
    )
    
    # Get shifts for this week
    shifts = Shift.objects.filter(
        staff__restaurant=request.user.restaurant,
        start_time__date__gte=week_start,
        start_time__date__lte=week_start + timedelta(days=6)
    )
    
    serializer = ShiftAssignmentSerializer(shifts, many=True)
    return Response({
        'schedule': WeeklyScheduleSerializer(schedule).data,
        'shifts': serializer.data
    })

@api_view(['POST'])
def assign_shift(request):
    staff_id = request.data.get('staff_id')
    start_time = request.data.get('start_time')
    end_time = request.data.get('end_time')
    section = request.data.get('section', '')
    
    staff = get_object_or_404(CustomUser, id=staff_id, restaurant=request.user.restaurant)
    
    shift = Shift.objects.create(
        staff=staff,
        start_time=start_time,
        end_time=end_time,
        section=section
    )
    
    serializer = ShiftAssignmentSerializer(shift)
    return Response(serializer.data, status=status.HTTP_201_CREATED)

@api_view(['GET'])
def my_schedule(request):
    # Get current user's schedule for the next 7 days
    today = timezone.now().date()
    next_week = today + timedelta(days=7)
    
    shifts = Shift.objects.filter(
        staff=request.user,
        start_time__date__gte=today,
        start_time__date__lte=next_week
    ).order_by('start_time')
    
    serializer = ShiftAssignmentSerializer(shifts, many=True)
    return Response(serializer.data)