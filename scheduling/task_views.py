from rest_framework import viewsets, status
from rest_framework import serializers as drf_serializers
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone
from django.db.models import Q
from django.core.files.base import ContentFile
import base64
import uuid

from .task_templates import TaskTemplate, Task
from .models import TaskCategory, ShiftTask
from .serializers import TaskTemplateSerializer, TaskCategorySerializer, TaskSerializer, CombinedTaskItemSerializer
from .recurrence_service import RecurrenceService

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

    def create(self, request, *args, **kwargs):
        """Override create to prevent 500s and surface clear validation errors"""
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except drf_serializers.ValidationError as e:
            # Return DRF validation errors directly
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)

        try:
            self.perform_create(serializer)
        except drf_serializers.ValidationError as e:
            # Catch validation errors raised during save/create
            return Response(e.detail, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # Log unexpected exceptions and return a safe 400 with message
            import logging
            logging.getLogger(__name__).exception("TaskTemplate creation failed: %s", str(e))
            return Response({
                'detail': 'Failed to create task template.',
                'message': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
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

    @action(detail=False, methods=['post'])
    def run_recurring(self, request):
        """Trigger recurrence generation for active templates.

        Optional body:
        - frequency: Restrict to a specific frequency (e.g., DAILY)
        - date: YYYY-MM-DD override for testing
        - restaurant_id: limit to restaurant
        """
        frequency = request.data.get('frequency')
        date = request.data.get('date')
        restaurant_id = request.data.get('restaurant_id')

        date_obj = None
        if date:
            try:
                from django.utils import timezone
                date_obj = timezone.datetime.strptime(date, '%Y-%m-%d').date()
            except Exception:
                return Response({'detail': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        restaurant = getattr(request.user, 'restaurant', None)
        # Allow superusers/admins to override restaurant
        if restaurant_id and getattr(request.user, 'role', '') in ['ADMIN']:
            from accounts.models import Restaurant
            try:
                restaurant = Restaurant.objects.get(id=restaurant_id)
            except Restaurant.DoesNotExist:
                return Response({'detail': 'Restaurant not found.'}, status=status.HTTP_404_NOT_FOUND)

        results = RecurrenceService.generate(date=date_obj, frequency=frequency, restaurant=restaurant, request=request)
        return Response(results, status=status.HTTP_200_OK)


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

    @action(detail=True, methods=['post'])
    def complete_with_verification(self, request, pk=None):
        """Complete a task with photo verification and notes"""
        task = self.get_object()
        
        # Check if user is assigned to this task
        if task.assigned_to.filter(id=request.user.id).exists() or request.user.role in ['MANAGER', 'ADMIN']:
            completion_data = {
                'notes': request.data.get('notes', ''),
                'location': request.data.get('location', ''),
                'completion_photo': None
            }
            
            # Handle photo upload
            photo_data = request.data.get('completion_photo')
            if photo_data:
                try:
                    # Decode base64 image
                    format, imgstr = photo_data.split(';base64,')
                    ext = format.split('/')[-1]
                    photo_file = ContentFile(
                        base64.b64decode(imgstr),
                        name=f'task_completion_{task.id}_{uuid.uuid4()}.{ext}'
                    )
                    completion_data['completion_photo'] = photo_file
                except Exception as e:
                    return Response(
                        {'detail': f'Invalid photo format: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Mark task as completed with verification data
            task.mark_completed(
                user=request.user,
                completion_notes=completion_data['notes'],
                completion_photo=completion_data['completion_photo'],
                completion_location=completion_data['location']
            )
            
            serializer = self.get_serializer(task)
            return Response({
                'task': serializer.data,
                'message': 'Task completed successfully with verification'
            })
        else:
            return Response(
                {'detail': 'You are not assigned to this task'},
                status=status.HTTP_403_FORBIDDEN
            )

    @action(detail=True, methods=['post'])
    def update_progress(self, request, pk=None):
        """Update task progress with real-time tracking"""
        task = self.get_object()
        
        # Check if user is assigned to this task
        if task.assigned_to.filter(id=request.user.id).exists() or request.user.role in ['MANAGER', 'ADMIN']:
            progress_percentage = request.data.get('progress_percentage', 0)
            progress_notes = request.data.get('progress_notes', '')
            
            # Validate progress percentage
            if not (0 <= progress_percentage <= 100):
                return Response(
                    {'detail': 'Progress percentage must be between 0 and 100'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            
            # Update task progress
            task.progress_percentage = progress_percentage
            task.progress_notes = progress_notes
            task.last_updated = timezone.now()
            
            # Auto-update status based on progress
            if progress_percentage == 0:
                task.status = 'TODO'
            elif progress_percentage == 100:
                task.status = 'COMPLETED'
                task.completed_at = timezone.now()
                task.completed_by = request.user
            else:
                task.status = 'IN_PROGRESS'
                if not task.started_at:
                    task.started_at = timezone.now()
            
            task.save()
            
            serializer = self.get_serializer(task)
            return Response({
                'task': serializer.data,
                'message': f'Progress updated to {progress_percentage}%'
            })
        else:
            return Response(
                {'detail': 'You are not assigned to this task'},
                status=status.HTTP_403_FORBIDDEN
            )

    @action(detail=True, methods=['post'])
    def add_checkpoint(self, request, pk=None):
        """Add a checkpoint/milestone to task progress"""
        task = self.get_object()
        
        # Check if user is assigned to this task
        if task.assigned_to.filter(id=request.user.id).exists() or request.user.role in ['MANAGER', 'ADMIN']:
            checkpoint_data = {
                'description': request.data.get('description', ''),
                'timestamp': timezone.now(),
                'user': request.user.id,
                'photo': None
            }
            
            # Handle checkpoint photo
            photo_data = request.data.get('checkpoint_photo')
            if photo_data:
                try:
                    format, imgstr = photo_data.split(';base64,')
                    ext = format.split('/')[-1]
                    photo_file = ContentFile(
                        base64.b64decode(imgstr),
                        name=f'checkpoint_{task.id}_{uuid.uuid4()}.{ext}'
                    )
                    checkpoint_data['photo'] = photo_file.name
                except Exception as e:
                    return Response(
                        {'detail': f'Invalid photo format: {str(e)}'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            
            # Add checkpoint to task
            if not hasattr(task, 'checkpoints') or task.checkpoints is None:
                task.checkpoints = []
            
            task.checkpoints.append(checkpoint_data)
            task.save()
            
            serializer = self.get_serializer(task)
            return Response({
                'task': serializer.data,
                'message': 'Checkpoint added successfully'
            })
        else:
            return Response(
                {'detail': 'You are not assigned to this task'},
                status=status.HTTP_403_FORBIDDEN
            )

    @action(detail=False, methods=['get'])
    def my_active_tasks(self, request):
        """Get current user's active tasks with real-time status"""
        user = request.user
        
        tasks = Task.objects.filter(
            restaurant=user.restaurant,
            assigned_to=user,
            status__in=['TODO', 'IN_PROGRESS']
        ).order_by('-priority', 'due_date')
        
        serializer = self.get_serializer(tasks, many=True)
        return Response({
            'tasks': serializer.data,
            'total_active': tasks.count(),
            'in_progress': tasks.filter(status='IN_PROGRESS').count(),
            'todo': tasks.filter(status='TODO').count()
        })

    @action(detail=False, methods=['get'], url_path='my_combined')
    def my_combined(self, request):
        """Return a unified list of tasks assigned to the current user.

        Combines direct template tasks (`Task`) and shift-linked tasks (`ShiftTask`).
        Supports filters: status, priority, due_from, due_to, ordering.
        """
        user = request.user
        restaurant = getattr(user, 'restaurant', None)

        # Query params
        status_filter = request.query_params.get('status')
        priority_filter = request.query_params.get('priority')
        due_from = request.query_params.get('due_from')
        due_to = request.query_params.get('due_to')
        ordering = request.query_params.get('ordering', 'due_date')
        page_size = int(request.query_params.get('page_size', 200))

        # Shift tasks assigned to user
        shift_tasks_qs = ShiftTask.objects.filter(
            assigned_to=user,
            shift__schedule__restaurant=restaurant
        ).select_related('shift', 'category', 'created_by')

        # Apply filters to ShiftTask
        if status_filter:
            shift_tasks_qs = shift_tasks_qs.filter(status=status_filter)
        if priority_filter:
            shift_tasks_qs = shift_tasks_qs.filter(priority=priority_filter)
        # Due date for a shift task is approximated by the shift date
        if due_from:
            try:
                shift_tasks_qs = shift_tasks_qs.filter(shift__shift_date__gte=due_from)
            except Exception:
                pass
        if due_to:
            try:
                shift_tasks_qs = shift_tasks_qs.filter(shift__shift_date__lte=due_to)
            except Exception:
                pass

        # Direct/template tasks assigned to user
        template_tasks_qs = Task.objects.filter(
            restaurant=restaurant,
            assigned_to=user
        ).select_related('template', 'assigned_shift', 'category', 'created_by')

        # Apply filters to Task
        if status_filter:
            template_tasks_qs = template_tasks_qs.filter(status=status_filter)
        if priority_filter:
            template_tasks_qs = template_tasks_qs.filter(priority=priority_filter)
        if due_from:
            try:
                template_tasks_qs = template_tasks_qs.filter(due_date__gte=due_from)
            except Exception:
                pass
        if due_to:
            try:
                template_tasks_qs = template_tasks_qs.filter(due_date__lte=due_to)
            except Exception:
                pass

        items = []
        # Normalize ShiftTask → Combined view
        for st in shift_tasks_qs:
            items.append({
                'id': st.id,
                'title': st.title,
                'description': st.description,
                'priority': st.priority,
                'status': st.status,
                'due_date': getattr(getattr(st, 'shift', None), 'shift_date', None),
                'due_time': None,
                'source': 'SHIFT_TASK',
                'associated_shift': {
                    'id': str(getattr(st.shift, 'id', '')),
                    'shift_date': str(getattr(st.shift, 'shift_date', '')),
                    'role': str(getattr(st.shift, 'role', '')),
                },
                'associated_template': None,
                'category': {
                    'id': str(getattr(st.category, 'id', '')),
                    'name': str(getattr(st.category, 'name', '')),
                } if getattr(st, 'category', None) else None,
                'created_at': st.created_at,
                'updated_at': st.updated_at,
                'assigned_to': [str(getattr(st.assigned_to, 'id', ''))] if getattr(st, 'assigned_to', None) else [],
            })

        # Normalize Template Task → Combined view
        for tt in template_tasks_qs:
            # ManyToMany assigned_to → list of ids
            assigned_ids = [str(u.id) for u in tt.assigned_to.all()]
            items.append({
                'id': tt.id,
                'title': tt.title,
                'description': tt.description,
                'priority': tt.priority,
                'status': tt.status,
                'due_date': tt.due_date,
                'due_time': tt.due_time,
                'source': 'TEMPLATE_TASK',
                'associated_shift': ({
                    'id': str(getattr(tt.assigned_shift, 'id', '')),
                    'shift_date': str(getattr(tt.assigned_shift, 'shift_date', '')),
                    'role': str(getattr(tt.assigned_shift, 'role', '')),
                } if getattr(tt, 'assigned_shift', None) else None),
                'associated_template': ({
                    'id': str(getattr(tt.template, 'id', '')),
                    'name': str(getattr(tt.template, 'name', '')),
                    'type': str(getattr(tt.template, 'template_type', '')),
                } if getattr(tt, 'template', None) else None),
                'category': {
                    'id': str(getattr(tt.category, 'id', '')),
                    'name': str(getattr(tt.category, 'name', '')),
                } if getattr(tt, 'category', None) else None,
                'created_at': tt.created_at,
                'updated_at': tt.updated_at,
                'assigned_to': assigned_ids,
            })

        # Ordering
        if ordering == 'priority':
            prio_order = {'URGENT': 4, 'HIGH': 3, 'MEDIUM': 2, 'LOW': 1}
            items.sort(key=lambda x: prio_order.get(str(x.get('priority') or 'MEDIUM').upper(), 0), reverse=True)
        elif ordering == 'status':
            items.sort(key=lambda x: str(x.get('status') or ''))
        else:
            # default by due_date asc with None at end
            def _due_ts(x):
                d = x.get('due_date')
                return timezone.datetime.max if d in (None, '') else timezone.datetime.fromisoformat(str(d))
            items.sort(key=_due_ts)

        # Pagination (simple slice)
        items = items[:page_size]

        serializer = CombinedTaskItemSerializer(items, many=True)
        return Response({'results': serializer.data, 'count': len(items)})

    @action(detail=False, methods=['get'])
    def task_analytics(self, request):
        """Get task completion analytics for the user"""
        user = request.user
        restaurant = user.restaurant
        
        # Get date range from query params
        start_date = request.query_params.get('start_date', timezone.now().date() - timezone.timedelta(days=30))
        end_date = request.query_params.get('end_date', timezone.now().date())
        
        # Calculate analytics
        total_tasks = Task.objects.filter(
            restaurant=restaurant,
            assigned_to=user,
            created_at__date__gte=start_date,
            created_at__date__lte=end_date
        ).count()
        
        completed_tasks = Task.objects.filter(
            restaurant=restaurant,
            assigned_to=user,
            status='COMPLETED',
            completed_at__date__gte=start_date,
            completed_at__date__lte=end_date
        ).count()
        
        overdue_tasks = Task.objects.filter(
            restaurant=restaurant,
            assigned_to=user,
            due_date__lt=timezone.now().date(),
            status__in=['TODO', 'IN_PROGRESS']
        ).count()
        
        completion_rate = (completed_tasks / total_tasks * 100) if total_tasks > 0 else 0
        
        return Response({
            'total_tasks': total_tasks,
            'completed_tasks': completed_tasks,
            'overdue_tasks': overdue_tasks,
            'completion_rate': round(completion_rate, 2),
            'period': {
                'start_date': start_date,
                'end_date': end_date
            }
        })

    @action(detail=True, methods=['get'])
    def task_timeline(self, request, pk=None):
        """Get detailed timeline of task progress and activities"""
        task = self.get_object()
        
        timeline = []
        
        # Task creation
        timeline.append({
            'event': 'created',
            'timestamp': task.created_at,
            'user': f"{task.created_by.first_name} {task.created_by.last_name}",
            'description': 'Task created'
        })
        
        # Task started
        if task.started_at:
            timeline.append({
                'event': 'started',
                'timestamp': task.started_at,
                'user': f"{task.created_by.first_name} {task.created_by.last_name}",
                'description': 'Task started'
            })
        
        # Add checkpoints
        if hasattr(task, 'checkpoints') and task.checkpoints:
            for checkpoint in task.checkpoints:
                timeline.append({
                    'event': 'checkpoint',
                    'timestamp': checkpoint.get('timestamp'),
                    'user': checkpoint.get('user'),
                    'description': checkpoint.get('description', 'Checkpoint added'),
                    'photo': checkpoint.get('photo')
                })
        
        # Task completion
        if task.completed_at:
            timeline.append({
                'event': 'completed',
                'timestamp': task.completed_at,
                'user': f"{task.completed_by.first_name} {task.completed_by.last_name}" if task.completed_by else 'Unknown',
                'description': 'Task completed',
                'notes': getattr(task, 'completion_notes', ''),
                'photo': getattr(task, 'completion_photo', None)
            })
        
        # Sort timeline by timestamp
        timeline.sort(key=lambda x: x['timestamp'])
        
        return Response({
            'task_id': task.id,
            'timeline': timeline
        })