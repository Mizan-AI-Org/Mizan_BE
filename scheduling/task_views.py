from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q

from .task_templates import TaskTemplate, TaskCategory, Task
from .serializers import TaskTemplateSerializer, TaskCategorySerializer, TaskSerializer

class TaskTemplateViewSet(viewsets.ModelViewSet):
    """
    API endpoint for task templates
    """
    serializer_class = TaskTemplateSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        restaurant = user.restaurant
        return TaskTemplate.objects.filter(restaurant=restaurant)
    
    def perform_create(self, serializer):
        serializer.save(
            restaurant=self.request.user.restaurant,
            created_by=self.request.user
        )
    
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """Duplicate a task template"""
        template = self.get_object()
        new_template = template.duplicate()
        serializer = self.get_serializer(new_template)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def generate_tasks(self, request, pk=None):
        """Generate tasks from a template"""
        template = self.get_object()
        assigned_to_ids = request.data.get('assigned_to', [])
        due_date = request.data.get('due_date')
        
        tasks = []
        for task_data in template.tasks:
            task = Task.objects.create(
                restaurant=self.request.user.restaurant,
                title=task_data.get('title'),
                description=task_data.get('description', ''),
                priority=task_data.get('priority', 'MEDIUM'),
                template=template,
                due_date=due_date,
                created_by=self.request.user
            )
            
            # Add assigned users
            if assigned_to_ids:
                task.assigned_to.set(assigned_to_ids)
            
            tasks.append(task)
        
        serializer = TaskSerializer(tasks, many=True)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class TaskCategoryViewSet(viewsets.ModelViewSet):
    """
    API endpoint for task categories
    """
    serializer_class = TaskCategorySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        restaurant = user.restaurant
        return TaskCategory.objects.filter(restaurant=restaurant)
    
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)


class TaskViewSet(viewsets.ModelViewSet):
    """
    API endpoint for tasks
    """
    serializer_class = TaskSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        restaurant = user.restaurant
        
        # Filter by parent task (for subtasks)
        parent_id = self.request.query_params.get('parent_id')
        if parent_id:
            return Task.objects.filter(parent_task_id=parent_id)
        
        # Filter by status
        status = self.request.query_params.get('status')
        if status:
            return Task.objects.filter(restaurant=restaurant, status=status)
        
        # Filter by assigned user
        assigned_to = self.request.query_params.get('assigned_to')
        if assigned_to:
            return Task.objects.filter(restaurant=restaurant, assigned_to=assigned_to)
        
        # Filter by due date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        if start_date and end_date:
            return Task.objects.filter(
                restaurant=restaurant,
                due_date__gte=start_date,
                due_date__lte=end_date
            )
        
        # Default: return all tasks for this restaurant
        return Task.objects.filter(restaurant=restaurant, parent_task=None)
    
    def perform_create(self, serializer):
        serializer.save(
            restaurant=self.request.user.restaurant,
            created_by=self.request.user
        )
    
    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark a task as completed"""
        task = self.get_object()
        task.mark_completed(user=request.user)
        serializer = self.get_serializer(task)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def start_task(self, request, pk=None):
        """Mark a task as in progress"""
        task = self.get_object()
        task.start_task(user=request.user)
        serializer = self.get_serializer(task)
        return Response(serializer.data)
    
    @action(detail=False, methods=['post'])
    def bulk_create(self, request):
        """Create multiple tasks at once"""
        tasks_data = request.data.get('tasks', [])
        created_tasks = []
        
        for task_data in tasks_data:
            serializer = self.get_serializer(data=task_data)
            serializer.is_valid(raise_exception=True)
            self.perform_create(serializer)
            created_tasks.append(serializer.data)
        
        return Response(created_tasks, status=status.HTTP_201_CREATED)
    
    @action(detail=False, methods=['get'])
    def overdue(self, request):
        """Get all overdue tasks"""
        user = request.user
        restaurant = user.restaurant
        today = timezone.now().date()
        
        tasks = Task.objects.filter(
            restaurant=restaurant,
            due_date__lt=today,
            status__in=['TODO', 'IN_PROGRESS']
        )
        
        serializer = self.get_serializer(tasks, many=True)
        return Response(serializer.data)