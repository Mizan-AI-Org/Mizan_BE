from rest_framework import status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Sum, Count, Avg
from datetime import timedelta
from accounts.models import CustomUser
from timeclock.models import ClockEvent, Shift
from reporting.models import Report

@api_view(['GET'])
def attendance_report(request):
    date_from = request.GET.get('date_from', (timezone.now() - timedelta(days=30)).date())
    date_to = request.GET.get('date_to', timezone.now().date())
    
    # Calculate attendance metrics
    staff_attendance = CustomUser.objects.filter(
        restaurant=request.user.restaurant,
        is_active=True
    ).annotate(
        total_shifts=Count('shifts'),
        completed_shifts=Count('shifts', filter=models.Q(shifts__status='completed')),
        late_arrivals=Count('clock_events', filter=models.Q(
            clock_events__event_type='in',
            clock_events__timestamp__date__gte=date_from,
            clock_events__timestamp__date__lte=date_to
        )),  # Add proper late calculation logic
        total_hours=Sum('shifts__actual_hours')
    )
    
    report_data = {
        'period': {'from': date_from, 'to': date_to},
        'summary': {
            'total_staff': staff_attendance.count(),
            'total_hours': staff_attendance.aggregate(Sum('shifts__actual_hours'))['shifts__actual_hours__sum'] or 0,
            'attendance_rate': 95,  # Calculate properly
        },
        'staff_performance': [
            {
                'name': staff.username,
                'role': staff.role,
                'total_shifts': staff.total_shifts,
                'completed_shifts': staff.completed_shifts,
                'attendance_rate': round((staff.completed_shifts / staff.total_shifts * 100) if staff.total_shifts > 0 else 0, 1),
                'total_hours': staff.total_hours or 0
            }
            for staff in staff_attendance
        ]
    }
    
    # Save report
    Report.objects.create(
        restaurant=request.user.restaurant,
        report_type='attendance',
        date_from=date_from,
        date_to=date_to,
        data=report_data
    )
    
    return Response(report_data)

@api_view(['GET'])
def payroll_report(request):
    date_from = request.GET.get('date_from', (timezone.now() - timedelta(days=30)).date())
    date_to = request.GET.get('date_to', timezone.now().date())
    
    payroll_data = CustomUser.objects.filter(
        restaurant=request.user.restaurant,
        is_active=True
    ).annotate(
        total_hours=Sum('shifts__actual_hours', filter=models.Q(
            shifts__start_time__date__gte=date_from,
            shifts__start_time__date__lte=date_to
        )),
        total_pay=models.F('total_hours') * models.F('profile__hourly_rate')
    )
    
    report_data = {
        'period': {'from': date_from, 'to': date_to},
        'total_payroll': sum(staff.total_pay or 0 for staff in payroll_data),
        'staff_payroll': [
            {
                'name': staff.username,
                'role': staff.role,
                'hourly_rate': staff.profile.hourly_rate if hasattr(staff, 'profile') else 0,
                'total_hours': staff.total_hours or 0,
                'total_pay': staff.total_pay or 0
            }
            for staff in payroll_data
        ]
    }
    
    return Response(report_data)

@api_view(['GET'])
def dashboard_metrics(request):
    today = timezone.now().date()
    
    # Today's metrics
    clocked_in_count = ClockEvent.objects.filter(
        staff__restaurant=request.user.restaurant,
        event_type='in',
        timestamp__date=today
    ).count()
    
    total_staff = CustomUser.objects.filter(
        restaurant=request.user.restaurant,
        is_active=True
    ).count()
    
    # Weekly hours
    week_start = today - timedelta(days=today.weekday())
    weekly_hours = Shift.objects.filter(
        staff__restaurant=request.user.restaurant,
        start_time__date__gte=week_start
    ).aggregate(total_hours=Sum('actual_hours'))['total_hours'] or 0
    
    metrics = {
        'today': {
            'clocked_in': clocked_in_count,
            'total_staff': total_staff,
            'attendance_rate': round((clocked_in_count / total_staff * 100) if total_staff > 0 else 0, 1)
        },
        'weekly_hours': weekly_hours,
        'active_shifts': Shift.objects.filter(
            staff__restaurant=request.user.restaurant,
            start_time__lte=timezone.now(),
            end_time__gte=timezone.now(),
            status='scheduled'
        ).count()
    }
    
    return Response(metrics)