from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q, Count, Sum, Avg
from scheduling.models import ShiftTask, TaskCategory
from scheduling.serializers import ShiftTaskSerializer, TaskCategorySerializer
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