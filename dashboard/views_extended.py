from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Count, Sum, Avg
from scheduling.models import ShiftTask, TaskCategory
from scheduling.serializers import ShiftTaskSerializer, TaskCategorySerializer
from scheduling.process_models import Process, ProcessTask
from attendance.models import ShiftReview
from .models import DailyKPI, Alert, Task
from .serializers import TaskSerializer
from .serializers import DailyKPISerializer, AlertSerializer


class TaskManagementViewSet(viewsets.ModelViewSet):
    """
    Comprehensive task management API
    """
    serializer_class = ShiftTaskSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter tasks by restaurant and user"""
        user = self.request.user
        if not user.restaurant:
            return ShiftTask.objects.none()
        
        queryset = ShiftTask.objects.filter(shift__schedule__restaurant=user.restaurant)
        
        # Apply filters
        status_filter = self.request.query_params.get('status')
        priority_filter = self.request.query_params.get('priority')
        assigned_to = self.request.query_params.get('assigned_to')
        shift_id = self.request.query_params.get('shift_id')
        
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        if priority_filter:
            queryset = queryset.filter(priority=priority_filter)
        if assigned_to:
            queryset = queryset.filter(assigned_to__id=assigned_to)
        if shift_id:
            queryset = queryset.filter(shift__id=shift_id)
        
        return queryset.order_by('-priority', 'created_at')
    
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """Create multiple tasks at once"""
        tasks_data = request.data.get('tasks', [])
        created_tasks = []
        
        for task_data in tasks_data:
            serializer = self.get_serializer(data=task_data)
            if serializer.is_valid():
                serializer.save()
                created_tasks.append(serializer.data)
            else:
                return Response(
                    {'errors': serializer.errors},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(created_tasks, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark a task as completed"""
        task = self.get_object()
        task.mark_completed()
        serializer = self.get_serializer(task)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def start_task(self, request, pk=None):
        """Start a task (change status to IN_PROGRESS)"""
        task = self.get_object()
        task.status = 'IN_PROGRESS'
        task.save()
        serializer = self.get_serializer(task)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def reassign_task(self, request, pk=None):
        """Reassign a task to another staff member"""
        task = self.get_object()
        new_assigned_to_id = request.data.get('assigned_to_id')
        
        if new_assigned_to_id:
            from accounts.models import CustomUser
            try:
                new_user = CustomUser.objects.get(id=new_assigned_to_id)
                task.assigned_to = new_user
                task.save()
                serializer = self.get_serializer(task)
                return Response(serializer.data)
            except CustomUser.DoesNotExist:
                return Response(
                    {'error': 'User not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
        
        return Response(
            {'error': 'assigned_to_id is required'},
            status=status.HTTP_400_BAD_REQUEST
        )
    
    @action(detail=True, methods=['post'])
    def add_subtask(self, request, pk=None):
        """Add a subtask to an existing task"""
        parent_task = self.get_object()
        subtask_data = request.data.copy()
        subtask_data['parent_task'] = parent_task.id
        
        serializer = self.get_serializer(data=subtask_data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=True, methods=['get'])
    def subtasks(self, request, pk=None):
        """Get all subtasks for a task"""
        task = self.get_object()
        subtasks = task.subtasks.all()
        serializer = self.get_serializer(subtasks, many=True)
        return Response(serializer.data)
    
    @action(detail=True, methods=['get'])
    def progress(self, request, pk=None):
        """Get task progress including subtasks"""
        task = self.get_object()
        return Response({
            'task_id': task.id,
            'title': task.title,
            'status': task.status,
            'progress_percentage': task.get_progress_percentage(),
            'completed_subtasks': task.subtasks.filter(status='COMPLETED').count(),
            'total_subtasks': task.subtasks.count()
        })
    
    @action(detail=False, methods=['get'])
    def statistics(self, request):
        """Get task statistics for the restaurant"""
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        
        queryset = self.get_queryset()
        
        stats = {
            'total_tasks': queryset.count(),
            'by_status': dict(queryset.values('status').annotate(count=Count('id')).values_list('status', 'count')),
            'by_priority': dict(queryset.values('priority').annotate(count=Count('id')).values_list('priority', 'count')),
            'completed_today': queryset.filter(completed_at__date=timezone.now().date()).count(),
            'overdue': queryset.filter(priority='URGENT', status__in=['TODO', 'IN_PROGRESS']).count()
        }
        
        return Response(stats)


class TaskCategoryViewSet(viewsets.ModelViewSet):
    """Task category management"""
    serializer_class = TaskCategorySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if not user.restaurant:
            return TaskCategory.objects.none()
        return TaskCategory.objects.filter(restaurant=user.restaurant)
    
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)
    
    @action(detail=False, methods=['get'])
    def with_task_counts(self, request):
        """Get categories with number of tasks in each"""
        queryset = self.get_queryset()
        data = []
        
        for category in queryset:
            data.append({
                'id': category.id,
                'name': category.name,
                'color': category.color,
                'task_count': category.tasks.count(),
                'description': category.description
            })
        
        return Response(data)


class DashboardAnalyticsViewSet(viewsets.ReadOnlyModelViewSet):
    """Dashboard analytics and KPI tracking"""
    serializer_class = DailyKPISerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if not user.restaurant:
            return DailyKPI.objects.none()
        return DailyKPI.objects.filter(restaurant=user.restaurant).order_by('-date')
    
    @action(detail=False, methods=['get'])
    def today(self, request):
        """Get today's KPI"""
        from datetime import date
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        
        kpi, created = DailyKPI.objects.get_or_create(
            restaurant=user.restaurant,
            date=date.today()
        )
        
        serializer = self.get_serializer(kpi)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def range(self, request):
        """Get KPI for a date range"""
        from datetime import datetime, timedelta
        
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        if not start_date or not end_date:
            return Response(
                {'error': 'start_date and end_date required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'error': 'Invalid date format (use YYYY-MM-DD)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        queryset = self.get_queryset().filter(date__range=[start_date, end_date])
        
        stats = {
            'date_range': {
                'start': start_date,
                'end': end_date
            },
            'summary': {
                'total_revenue': queryset.aggregate(Sum('total_revenue'))['total_revenue__sum'] or 0,
                'total_orders': queryset.aggregate(Sum('total_orders'))['total_orders__sum'] or 0,
                'avg_order_value': queryset.aggregate(Avg('avg_order_value'))['avg_order_value__avg'] or 0,
                'total_food_waste': queryset.aggregate(Sum('food_waste_cost'))['food_waste_cost__sum'] or 0,
                'avg_labor_cost': queryset.aggregate(Avg('labor_cost_percentage'))['labor_cost_percentage__avg'] or 0,
            },
            'daily_data': DailyKPISerializer(queryset, many=True).data
        }
        
        return Response(stats)
    
    @action(detail=False, methods=['get'])
    def insights(self, request):
        """Get AI-powered insights from KPI data"""
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Get last 30 days of data
        from datetime import date, timedelta
        start_date = date.today() - timedelta(days=30)
        
        kpis = DailyKPI.objects.filter(
            restaurant=user.restaurant,
            date__gte=start_date
        ).order_by('date')
        
        if not kpis.exists():
            return Response({'insights': [], 'message': 'Not enough data'})
        
        insights = []
        
        # Revenue trend
        avg_revenue = kpis.aggregate(Avg('total_revenue'))['total_revenue__avg'] or 0
        latest_revenue = kpis.last().total_revenue if kpis.last() else 0
        if latest_revenue > avg_revenue * 1.2:
            insights.append({
                'type': 'POSITIVE',
                'title': 'Revenue Boost',
                'message': f'Revenue is {((latest_revenue/avg_revenue - 1) * 100):.1f}% above average!',
                'icon': 'TrendingUp'
            })
        
        # Labor cost warning
        avg_labor = kpis.aggregate(Avg('labor_cost_percentage'))['labor_cost_percentage__avg'] or 0
        latest_labor = kpis.last().labor_cost_percentage if kpis.last() else 0
        if latest_labor > 35:  # Typical threshold
            insights.append({
                'type': 'WARNING',
                'title': 'Labor Cost Alert',
                'message': f'Labor costs at {latest_labor:.1f}% - consider optimizing staff',
                'icon': 'AlertTriangle'
            })
        
        # Food waste
        avg_waste = kpis.aggregate(Avg('food_waste_cost'))['food_waste_cost__avg'] or 0
        latest_waste = kpis.last().food_waste_cost if kpis.last() else 0
        if latest_waste > avg_waste * 1.15:
            insights.append({
                'type': 'WARNING',
                'title': 'High Food Waste',
                'message': f'Food waste increased to ${latest_waste:.2f} - review portion sizes',
                'icon': 'AlertTriangle'
            })
        
        return Response({'insights': insights})

    @action(detail=False, methods=['get'])
    def live_board_metrics(self, request):
        """Get real-time metrics for the Live Board"""
        from datetime import timedelta
        from scheduling.task_templates import TaskTemplate
        
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
            
        today = timezone.now().date()
        yesterday = today - timedelta(days=1)
        
        # 1. Active Ongoing Processes
        # Count of active shifts today that have at least one TaskTemplate assigned
        from scheduling.models import AssignedShift
        active_processes_count = AssignedShift.objects.filter(
            schedule__restaurant=user.restaurant, 
            shift_date=today,
            status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS'],
            task_templates__isnull=False
        ).distinct().count()
        
        # 2. Tasks Today (using ShiftTask)
        tasks_today_queryset = ShiftTask.objects.filter(shift__schedule__restaurant=user.restaurant, shift__shift_date=today)
        total_tasks = tasks_today_queryset.count()
        completed_tasks = tasks_today_queryset.filter(status='COMPLETED').count()
        ongoing_tasks = tasks_today_queryset.filter(status='IN_PROGRESS').count()
        
        # 3. On-Time Rate
        on_time_rate = int((completed_tasks / total_tasks * 100)) if total_tasks > 0 else 100
        
        # Calculate comparison with yesterday
        tasks_yesterday = ShiftTask.objects.filter(shift__schedule__restaurant=user.restaurant, shift__shift_date=yesterday)
        total_yesterday = tasks_yesterday.count()
        completed_yesterday = tasks_yesterday.filter(status='COMPLETED').count()
        rate_yesterday = int((completed_yesterday / total_yesterday * 100)) if total_yesterday > 0 else 100
        on_time_change = on_time_rate - rate_yesterday
        
        # 4. Attention Needed (ShiftReviews with rating <= 3)
        attention_needed = ShiftReview.objects.filter(restaurant=user.restaurant, rating__lte=3).count()
        
        # 5. Process details for the selector
        process_details = [
            { 'id': 'all', 'name': 'All Processes', 'completion': 0, 'health': 'green' }
        ]
        
        # To get process details correctly for the selector, we should probably aggregate from active shifts/templates
        # but for now keeping it simple or reusing active_processes_queryset if we had one
            
        return Response({
            'active_processes_count': active_processes_count,
            'tasks_today': {
                'total': total_tasks,
                'completed': completed_tasks,
                'ongoing': ongoing_tasks,
            },
            'on_time_rate': on_time_rate,
            'on_time_change': on_time_change,
            'attention_needed': attention_needed,
            'processes': process_details
        })

    @action(detail=False, methods=['get'])
    def staff_live_metrics(self, request):
        """Get live operational metrics for each staff member on shift"""
        from datetime import timedelta
        from scheduling.models import AssignedShift
        from django.utils import timezone
        
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
            
        today = timezone.now().date()
        now = timezone.now()
        
        # Get active shifts (today's shifts that are confirmed, in_progress, or scheduled)
        active_shifts = AssignedShift.objects.filter(
            schedule__restaurant=user.restaurant, 
            shift_date=today,
            status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS']
        ).select_related('staff').prefetch_related('tasks', 'task_templates')
        
        staff_metrics = []
        
        for shift in active_shifts:
            staff = shift.staff
            tasks = shift.tasks.all()
            
            # 1. Staff Status
            shift_status = 'ON_SHIFT' # Default logic, refine if we have real-time clock-in data
            if shift.status == 'COMPLETED': shift_status = 'OFF_SHIFT'
            
            # 2. Current Process
            # If a task template is assigned to the shift, use it
            current_process_name = "Idle / Waiting for task"
            process_progress = 0
            
            # Try to infer process from assigned templates
            template = shift.task_templates.first()
            if template:
                current_process_name = template.name
                # Calculate progress for this "process" (tasks linked to this shift)
                total_process_tasks = tasks.count() # Assuming all tasks in shift belong to the process for now
                completed_process_tasks = tasks.filter(status='COMPLETED').count()
                process_progress = int((completed_process_tasks / total_process_tasks * 100)) if total_process_tasks > 0 else 0
            
            # 3. Task Stats
            total_tasks = tasks.count()
            completed_tasks = tasks.filter(status='COMPLETED').count()
            overdue_tasks = 0
            for t in tasks:
                if t.priority == 'URGENT' or (t.estimated_duration and t.created_at + t.estimated_duration < now and t.status != 'COMPLETED'):
                     # Simple overdue logic: if urgent or past estimated time (if created + duration < now)
                     # Better logic would use due_date if available
                     overdue_tasks += 1
            
            # 4. Pace Indicator
            # Calculate elapsed time in shift vs expected
            pace_status = 'GREEN'
            elapsed_minutes = 0
            avg_minutes = 0 # This would come from template historical data ideally
            
            if shift.start_time:
                # If start_time is just time, combine with today
                start_dt = shift.start_time
                if not isinstance(start_dt, timezone.datetime):
                    # This case should be handled by model save, but just in case
                    start_dt = timezone.datetime.combine(today, start_dt)
                    if timezone.is_naive(start_dt):
                        start_dt = timezone.make_aware(start_dt)
                
                delta = now - start_dt
                elapsed_minutes = int(delta.total_seconds() / 60)
                
                # Estimate total expected time based on tasks duration
                total_estimated_seconds = sum([(t.estimated_duration.total_seconds() if t.estimated_duration else 15*60) for t in tasks])
                avg_minutes = int(total_estimated_seconds / 60)
                
                # Pace logic
                if elapsed_minutes > avg_minutes * 1.2:
                    pace_status = 'RED'
                elif elapsed_minutes > avg_minutes:
                    pace_status = 'YELLOW'
            
            # 5. Attention Flag
            attention_needed = False
            attention_reason = ""
            if overdue_tasks > 0:
                attention_needed = True
                attention_reason = f"{overdue_tasks} overdue tasks"
            elif pace_status == 'RED':
                attention_needed = True
                attention_reason = "Behind schedule"
                
            staff_metrics.append({
                'staff_id': staff.id,
                'name': f"{staff.first_name} {staff.last_name}",
                'role': staff.role,
                'avatar': None, # Front-end handles avatar generation
                'shift_status': shift_status,
                'current_process': {
                    'name': current_process_name,
                    'progress': process_progress
                },
                'tasks': {
                    'completed': completed_tasks,
                    'total': total_tasks,
                    'overdue': overdue_tasks,
                    'is_completed': completed_tasks == total_tasks and total_tasks > 0
                },
                'pace': {
                    'elapsed_minutes': elapsed_minutes,
                    'avg_minutes': avg_minutes,
                    'status': pace_status
                },
                'attention': {
                    'needed': attention_needed,
                    'reason': attention_reason
                }
            })
            
        return Response(staff_metrics)

    @action(detail=False, methods=['get'])
    def attendance_dashboard(self, request):
        """Get summarized and detailed attendance data for the dashboard"""
        from scheduling.models import AssignedShift
        from timeclock.models import ClockEvent
        from datetime import datetime, timedelta
        import math

        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        
        today = timezone.now().date()
        today_start = timezone.make_aware(datetime.combine(today, datetime.min.time()))
        today_end = timezone.make_aware(datetime.combine(today, datetime.max.time()))
        
        # 1. Fetch Today's Shifts
        shifts = AssignedShift.objects.filter(
            schedule__restaurant=user.restaurant,
            shift_date=today,
            status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED', 'NO_SHOW']
        ).select_related('staff').prefetch_related('staff_members')
        
        # 2. Fetch Today's Clock Events
        events = ClockEvent.objects.filter(
            staff__restaurant=user.restaurant,
            timestamp__range=(today_start, today_end)
        ).select_related('staff').order_by('timestamp')
        
        # Data Structures
        summary = {
            'present': {'count': 0, 'total': shifts.count(), 'percentage': 0},
            'late': {'count': 0, 'avg_minutes': 0},
            'absent': {'count': 0, 'reason': 'No check-in'},
            'on_leave': {'count': 0, 'subtitle': 'Approved'} 
        }
        
        staff_map = {}
        unique_present = set()
        total_late_minutes = 0
        
        # Initialize staff map with shifts
        for shift in shifts:
            # Get all assigned staff members for this shift
            assigned_staff_list = list(shift.staff_members.all())
            if shift.staff and shift.staff not in assigned_staff_list:
                assigned_staff_list.append(shift.staff)
                
            for staff in assigned_staff_list:
                staff_id = staff.id
                if staff_id in staff_map:
                    continue # Avoid double entry if overlap exists
                    
                staff_map[staff_id] = {
                    'staff': {
                        'id': staff_id,
                        'name': f"{staff.first_name} {staff.last_name}",
                        'role': staff.role,
                        'avatar': None
                    },
                'shift': {
                    'start': timezone.localtime(shift.start_time).strftime('%H:%M') if shift.start_time else None,
                    'end': timezone.localtime(shift.end_time).strftime('%H:%M') if shift.end_time else None,
                    'status': shift.status
                },
                'clock_in': None,
                'status': 'scheduled', # scheduled, late, absent, on_time, on_break, clocked_out
                'late_minutes': 0,
                'location': 'Unknown',
                'timeline': [], # For visual bar
                'signals': [] # "Late 3x", "Perfect Attendance"
            }
            
        # Process Clock Events
        for event in events:
            staff_id = event.staff.id
            
            # If staff clocked in but has no shift, add them (unplanned shift)
            if staff_id not in staff_map:
                staff_map[staff_id] = {
                    'staff': {
                        'id': staff_id,
                        'name': f"{event.staff.first_name} {event.staff.last_name}",
                        'role': event.staff.role,
                        'avatar': None
                    },
                    'shift': {'start': None, 'end': None, 'status': 'UNSCHEDULED'},
                    'clock_in': None,
                    'status': 'present', 
                    'late_minutes': 0,
                    'location': 'Unknown',
                    'timeline': [],
                    'signals': ['Unscheduled Shift']
                }

            data = staff_map[staff_id]
            time_str = timezone.localtime(event.timestamp).strftime('%H:%M')
            data['timeline'].append({'time': time_str, 'type': event.event_type})
            
            if event.event_type in ['in', 'CLOCK_IN']:
                if not data['clock_in']: # First clock in
                    data['clock_in'] = time_str
                    unique_present.add(staff_id)
                    # Check Lateness
                    if data['shift']['start']:
                        shift_start_dt = datetime.strptime(data['shift']['start'], '%H:%M')
                        clock_in_dt = datetime.strptime(time_str, '%H:%M')
                        # 5 min grace period
                        diff_mins = (clock_in_dt - shift_start_dt).total_seconds() / 60
                        if diff_mins > 5:
                            data['status'] = 'late'
                            data['late_minutes'] = int(diff_mins)
                            total_late_minutes += int(diff_mins)
                            data['signals'].append(f"Late ({int(diff_mins)} min)")
                        else:
                            data['status'] = 'on_time'
                            data['signals'].append("On Time")
                    else:
                         data['status'] = 'present'

            elif event.event_type in ['out', 'CLOCK_OUT']:
                 if data['status'] != 'late': # Preserve late status
                     data['status'] = 'clocked_out'

        # Analyze Absences & Final Summaries
        # Analyze Absences & Final Summaries
        now_dt = timezone.localtime(timezone.now())
        
        for staff_id, data in staff_map.items():
            # If scheduled but no clock in...
            if data['shift']['start'] and not data['clock_in']:
                try:
                    # Construct shift datetime objects
                    s_start = datetime.strptime(data['shift']['start'], '%H:%M').time()
                    s_end = datetime.strptime(data['shift']['end'], '%H:%M').time()
                    
                    shift_start_dt = timezone.make_aware(datetime.combine(today, s_start))
                    shift_end_dt = timezone.make_aware(datetime.combine(today, s_end))
                    
                    # Handle overnight shifts if needed (end < start)
                    if shift_end_dt < shift_start_dt:
                        shift_end_dt += timedelta(days=1)
                        
                    if now_dt < shift_start_dt:
                        data['status'] = 'scheduled'
                        # Not absent or late yet
                    elif now_dt > shift_end_dt:
                        data['status'] = 'absent'
                        summary['absent']['count'] += 1
                    else:
                        # Shift has started, but not confirmed absent til end?
                        # Or assume Late if currently running and not here?
                        # User request: "Status should only show 'Absent' immediately the time for their shift passes"
                        # This implies "Late" or "Missing" during the shift.
                        # Let's count as Late for currently running.
                        data['status'] = 'late'
                        summary['late']['count'] += 1
                        # Calculate minutes late so far
                        diff_mins = (now_dt - shift_start_dt).total_seconds() / 60
                        data['late_minutes'] = int(diff_mins)
                        data['signals'].append(f"Late ({int(diff_mins)}m)")
                        
                except Exception as e:
                    print(f"Error parsing times for attendance: {e}")
                    data['status'] = 'scheduled'

            elif data['status'] == 'late':
                summary['late']['count'] += 1
        
        summary['present']['count'] = len(unique_present)
        if summary['present']['total'] > 0:
             summary['present']['percentage'] = int((summary['present']['count'] / summary['present']['total']) * 100)
             
        if summary['late']['count'] > 0:
            summary['late']['avg_minutes'] = int(total_late_minutes / summary['late']['count'])

        # Sort: Late -> Absent -> Present -> Others
        def sort_key(item):
            s = item['status']
            if s == 'late': return 1
            if s == 'absent': return 2
            if s == 'on_time': return 3
            if s == 'present': return 3
            if s == 'clocked_out': return 4
            return 5

        sorted_list = sorted(staff_map.values(), key=sort_key)
        
        return Response({
            'summary': summary,
            'attendance_list': sorted_list,
            'recent_activity': [
                {
                    'id': str(e.id),
                    'staff_name': f"{e.staff.first_name} {e.staff.last_name}",
                    'event': e.get_event_type_display(),
                    'time': timezone.localtime(e.timestamp).strftime('%H:%M'),
                    'location': 'Front Desk' # Placeholder or e.location_name
                } for e in events.reverse()[:10]
            ]
        })

    @action(detail=False, methods=['get'])
    def staff_insights(self, request):
        """Get staff analytics for the Insights tab"""
        from scheduling.models import AssignedShift, ShiftTask
        from scheduling.process_models import ProcessTask
        from timeclock.models import ClockEvent
        from datetime import datetime, timedelta
        from django.db.models import Count, Q
        
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)

        now = timezone.now()
        today = now.date()
        last_7_days = today - timedelta(days=7)
        prev_7_days = last_7_days - timedelta(days=7)
        last_30_days = today - timedelta(days=30)
        grace_period = timedelta(minutes=5)

        # 1. Tasks Completed (Last 7 Days)
        completed_shift_tasks = ShiftTask.objects.filter(
            shift__schedule__restaurant=user.restaurant,
            status='COMPLETED',
            completed_at__date__range=(last_7_days, today)
        ).count()
        
        completed_process_tasks = ProcessTask.objects.filter(
            process__restaurant=user.restaurant,
            status='COMPLETED',
            completed_at__date__range=(last_7_days, today)
        ).count()
        
        total_completed = completed_shift_tasks + completed_process_tasks
        
        # Trend
        prev_completed_shift = ShiftTask.objects.filter(
            shift__schedule__restaurant=user.restaurant,
            status='COMPLETED',
            completed_at__date__range=(prev_7_days, last_7_days - timedelta(days=1))
        ).count()
        
        prev_completed_process = ProcessTask.objects.filter(
            process__restaurant=user.restaurant,
            status='COMPLETED',
            completed_at__date__range=(prev_7_days, last_7_days - timedelta(days=1))
        ).count()
        
        prev_total = prev_completed_shift + prev_completed_process
        trend_perc = 0
        if prev_total > 0:
            trend_perc = int(((total_completed - prev_total) / prev_total) * 100)

        # 2. Team Reliability (Last 30 Days)
        reliability_shifts = AssignedShift.objects.filter(
            schedule__restaurant=user.restaurant,
            shift_date__range=(last_30_days, today),
            status__in=['COMPLETED', 'NO_SHOW']
        ).select_related('staff')
        
        on_time_count = 0
        total_shifts_with_clockin = 0
        
        for s in reliability_shifts:
            first_in = ClockEvent.objects.filter(
                staff=s.staff,
                timestamp__date=s.shift_date,
                event_type__in=['in', 'CLOCK_IN']
            ).order_by('timestamp').first()
            
            if first_in:
                total_shifts_with_clockin += 1
                if s.start_time and first_in.timestamp <= s.start_time + grace_period:
                    on_time_count += 1
        
        reliability_score = 100
        if total_shifts_with_clockin > 0:
            reliability_score = int((on_time_count / total_shifts_with_clockin) * 100)

        # 3. Active Workers
        daily_events = ClockEvent.objects.filter(
            staff__restaurant=user.restaurant,
            timestamp__date=today
        ).order_by('timestamp')
        
        staff_status = {}
        for ev in daily_events:
            staff_status[ev.staff_id] = ev.event_type
            
        active_count = sum(1 for status in staff_status.values() if status in ['in', 'CLOCK_IN'])

        # 4. Star Performers
        staff_completions = ShiftTask.objects.filter(
            shift__schedule__restaurant=user.restaurant,
            status='COMPLETED',
            completed_at__date__range=(last_7_days, today)
        ).values('assigned_to__id', 'assigned_to__first_name', 'assigned_to__last_name', 'assigned_to__role').annotate(
            completed_count=Count('id')
        ).order_by('-completed_count')[:3]

        stars = []
        for s in staff_completions:
            if s['assigned_to__id']:
                stars.append({
                    'name': f"{s['assigned_to__first_name']} {s['assigned_to__last_name']}",
                    'role': s['assigned_to__role'],
                    'tasks': s['completed_count'],
                    'score': 95
                })

        # 5. Burnout & Alerts
        alerts = []
        reliability_signals = []
        all_staff = user.restaurant.customuser_set.filter(is_active=True)
        
        for staff in all_staff:
            recent_shifts = reliability_shifts.filter(staff=staff, shift_date__range=(last_7_days, today))
            # Calculate hours manually to avoid property issues in filter/values
            total_hours = sum(s.actual_hours for s in recent_shifts)
            
            if total_hours > 45:
                alerts.append({
                    'type': 'Burnout Risk',
                    'level': 'Critical',
                    'title': f"{staff.first_name} over-exertion",
                    'description': f"{staff.first_name} has worked {int(total_hours)} hours this week."
                })
            
            late_count = 0
            for s in recent_shifts:
                first_in = ClockEvent.objects.filter(staff=staff, timestamp__date=s.shift_date, event_type__in=['in', 'CLOCK_IN']).order_by('timestamp').first()
                if first_in and s.start_time and first_in.timestamp > s.start_time + grace_period:
                    late_count += 1
            
            if late_count >= 3:
                alerts.append({
                    'type': 'Reliability Alert',
                    'level': 'Monitor',
                    'title': f"{staff.first_name} punctuality",
                    'description': f"{staff.first_name} had {late_count} late check-ins this week."
                })
            elif late_count == 0 and recent_shifts.count() >= 3:
                 reliability_signals.append({
                     'color': 'emerald',
                     'text': f"**{staff.first_name}** has 100% on-time attendance this week"
                 })

        # No-show Rate
        no_shows = AssignedShift.objects.filter(
            schedule__restaurant=user.restaurant,
            shift_date__range=(last_30_days, today),
            status='NO_SHOW'
        ).count()
        total_30d = AssignedShift.objects.filter(schedule__restaurant=user.restaurant, shift_date__range=(last_30_days, today)).count()
        no_show_rate = round((no_shows / total_30d * 100), 1) if total_30d > 0 else 0

        return Response({
            'summary': {
                'tasks_completed': total_completed,
                'tasks_trend': trend_perc,
                'team_reliability': reliability_score,
                'active_workers': active_count,
            },
            'star_performers': stars,
            'attendance_health': {
                'on_time_arrival': reliability_score,
                'no_show_rate': no_show_rate
            },
            'signals': reliability_signals[:2] or [{'color': 'emerald', 'text': 'Team punctuality is stable this week'}],
            'alerts': alerts[:2]
        })

    @action(detail=False, methods=['get'], url_path='staff-performance')
    def staff_performance(self, request):
        """Staff performance metrics for SchedulingAnalytics (real data)."""
        from datetime import datetime, timedelta
        from scheduling.models import AssignedShift
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        if not start_date or not end_date:
            return Response({'error': 'start_date and end_date required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
        shifts = AssignedShift.objects.filter(
            schedule__restaurant=user.restaurant,
            shift_date__gte=start_date,
            shift_date__lte=end_date,
            staff__isnull=False
        ).values('staff__id', 'staff__first_name', 'staff__last_name').annotate(
            total_shifts=Count('id'),
            completed=Count('id', filter=Q(status='COMPLETED'))
        )
        tasks = ShiftTask.objects.filter(
            shift__schedule__restaurant=user.restaurant,
            shift__shift_date__gte=start_date,
            shift__shift_date__lte=end_date,
            assigned_to__isnull=False
        ).values('assigned_to__id', 'assigned_to__first_name', 'assigned_to__last_name').annotate(
            total_tasks=Count('id'),
            completed_tasks=Count('id', filter=Q(status='COMPLETED'))
        )
        by_staff = {}
        for s in shifts:
            sid = str(s['staff__id'])
            name = f"{s['staff__first_name'] or ''} {s['staff__last_name'] or ''}".strip() or sid
            by_staff[sid] = {'name': name, 'value': s['total_shifts'], 'completionRate': (s['completed'] / s['total_shifts'] * 100) if s['total_shifts'] else 0}
        for t in tasks:
            sid = str(t['assigned_to__id'])
            name = f"{t['assigned_to__first_name'] or ''} {t['assigned_to__last_name'] or ''}".strip() or sid
            rate = (t['completed_tasks'] / t['total_tasks'] * 100) if t['total_tasks'] else 0
            if sid in by_staff:
                by_staff[sid]['completionRate'] = round((by_staff[sid]['completionRate'] + rate) / 2, 1)
            else:
                by_staff[sid] = {'name': name, 'value': t['total_tasks'], 'completionRate': round(rate, 1)}
        data = [{'name': v['name'], 'value': v['value'], 'completionRate': round(v['completionRate'], 1)} for v in by_staff.values()]
        return Response(data[:20] if len(data) > 20 else data)

    @action(detail=False, methods=['get'], url_path='task-completion')
    def task_completion(self, request):
        """Task completion over time for SchedulingAnalytics (real data)."""
        from datetime import datetime, timedelta
        from django.db.models.functions import TruncDate
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        if not start_date or not end_date:
            return Response({'error': 'start_date and end_date required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
        qs = ShiftTask.objects.filter(
            shift__schedule__restaurant=user.restaurant,
            shift__shift_date__gte=start_date,
            shift__shift_date__lte=end_date
        )
        by_date = qs.values('shift__shift_date').annotate(
            total=Count('id'),
            completed=Count('id', filter=Q(status='COMPLETED'))
        ).order_by('shift__shift_date')
        data = [{'date': b['shift__shift_date'].isoformat(), 'total': b['total'], 'completed': b['completed']} for b in by_date]
        return Response(data)

    @action(detail=False, methods=['get'], url_path='labor-costs')
    def labor_costs(self, request):
        """Labor cost from real timesheet/clock data for SchedulingAnalytics."""
        from datetime import datetime
        from reporting.services_labor import labor_cost_from_real_data, labor_budget_for_period
        user = request.user
        if not user.restaurant:
            return Response({'error': 'No restaurant associated'}, status=status.HTTP_400_BAD_REQUEST)
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        if not start_date or not end_date:
            return Response({'error': 'start_date and end_date required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
        labor = labor_cost_from_real_data(user.restaurant, start_date, end_date)
        budget = labor_budget_for_period(user.restaurant, start_date, end_date)
        by_role = labor.get('by_role', {})
        chart_data = [{'name': k, 'value': round(v['cost'], 2)} for k, v in by_role.items()]
        return Response({
            'chart_data': chart_data,
            'total_hours': labor['total_hours'],
            'total_cost': labor['total_cost'],
            'currency': labor['currency'],
            'source': labor.get('source', 'timesheets'),
            'budget': budget,
        })

class AlertViewSet(viewsets.ModelViewSet):
    """Alert management for restaurants"""
    serializer_class = AlertSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if not user.restaurant:
            return Alert.objects.none()
        return Alert.objects.filter(restaurant=user.restaurant).order_by('-created_at')
    
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)
    
    @action(detail=True, methods=['post'])
    def mark_resolved(self, request, pk=None):
        """Mark an alert as resolved"""
        alert = self.get_object()
        alert.is_resolved = True
        alert.save()
        return Response({'status': 'Alert marked as resolved'})
    
    @action(detail=False, methods=['get'])
    def unresolved(self, request):
        """Get all unresolved alerts"""
        queryset = self.get_queryset().filter(is_resolved=False)
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)