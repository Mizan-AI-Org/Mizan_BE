"""
API Views for Checklist Management System
"""
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.parsers import MultiPartParser, JSONParser
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework.filters import SearchFilter, OrderingFilter
from django.utils import timezone
from django.db.models import Q, Count, Avg
from django.db import transaction
from django.core.files.base import ContentFile
import base64
import uuid
import json

from .models import (
    ChecklistTemplate, ChecklistStep, ChecklistExecution,
    ChecklistStepResponse, ChecklistEvidence, ChecklistAction
)
from .serializers import (
    ChecklistTemplateSerializer, ChecklistTemplateCreateSerializer,
    ChecklistStepSerializer, ChecklistExecutionSerializer,
    ChecklistExecutionCreateSerializer, ChecklistExecutionUpdateSerializer,
    ChecklistStepResponseSerializer, ChecklistStepResponseUpdateSerializer,
    ChecklistEvidenceSerializer, ChecklistActionSerializer,
    ChecklistSyncSerializer
)
from .services import ChecklistSyncService, ChecklistValidationService
from core.permissions import IsRestaurantOwnerOrManager
from scheduling.models import ShiftTask
from scheduling.task_templates import TaskTemplate


class ChecklistTemplateViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing checklist templates
    
    Endpoints:
    - GET /api/checklists/templates/ - List templates
    - POST /api/checklists/templates/ - Create template
    - GET /api/checklists/templates/{id}/ - Get template details
    - PUT /api/checklists/templates/{id}/ - Update template
    - DELETE /api/checklists/templates/{id}/ - Delete template
    - POST /api/checklists/templates/{id}/duplicate/ - Duplicate template
    """
    serializer_class = ChecklistTemplateSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['template_type', 'is_active']
    search_fields = ['name', 'description']
    ordering_fields = ['name', 'created_at', 'updated_at']
    ordering = ['-created_at']
    
    def get_queryset(self):
        return ChecklistTemplate.objects.filter(
            restaurant=self.request.user.restaurant
        ).prefetch_related('steps')
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ChecklistTemplateCreateSerializer
        return ChecklistTemplateSerializer
    
    def perform_create(self, serializer):
        serializer.save(
            restaurant=self.request.user.restaurant,
            created_by=self.request.user
        )
    
    @action(detail=True, methods=['post'])
    def duplicate(self, request, pk=None):
        """Duplicate a checklist template"""
        template = self.get_object()
        
        # Create new template
        new_template = ChecklistTemplate.objects.create(
            restaurant=template.restaurant,
            name=f"{template.name} (Copy)",
            description=template.description,
            template_type=template.template_type,
            estimated_duration=template.estimated_duration,
            requires_supervisor_approval=template.requires_supervisor_approval,
            created_by=request.user
        )
        
        # Duplicate steps
        for step in template.steps.all():
            ChecklistStep.objects.create(
                template=new_template,
                title=step.title,
                description=step.description,
                step_type=step.step_type,
                order=step.order,
                is_required=step.is_required,
                requires_photo=step.requires_photo,
                requires_note=step.requires_note,
                requires_signature=step.requires_signature,
                measurement_type=step.measurement_type,
                measurement_unit=step.measurement_unit,
                min_value=step.min_value,
                max_value=step.max_value,
                target_value=step.target_value,
                conditional_logic=step.conditional_logic,
                validation_rules=step.validation_rules
            )
        
        serializer = self.get_serializer(new_template)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['get'])
    def usage_stats(self, request, pk=None):
        """Get usage statistics for a template"""
        template = self.get_object()
        
        stats = {
            'total_executions': template.executions.count(),
            'completed_executions': template.executions.filter(status='COMPLETED').count(),
            'in_progress_executions': template.executions.filter(status='IN_PROGRESS').count(),
            'average_completion_time': template.executions.filter(
                status='COMPLETED',
                started_at__isnull=False,
                completed_at__isnull=False
            ).aggregate(
                avg_time=Avg('completed_at') - Avg('started_at')
            )['avg_time'],
            'success_rate': 0
        }
        
        if stats['total_executions'] > 0:
            stats['success_rate'] = (stats['completed_executions'] / stats['total_executions']) * 100
        
        return Response(stats)


class ChecklistExecutionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing checklist executions
    
    Endpoints:
    - GET /api/checklists/executions/ - List executions
    - POST /api/checklists/executions/ - Create execution
    - GET /api/checklists/executions/{id}/ - Get execution details
    - PUT /api/checklists/executions/{id}/ - Update execution
    - DELETE /api/checklists/executions/{id}/ - Delete execution
    - POST /api/checklists/executions/{id}/start/ - Start execution
    - POST /api/checklists/executions/{id}/complete/ - Complete execution
    - POST /api/checklists/executions/{id}/sync/ - Sync offline data
    """
    serializer_class = ChecklistExecutionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'template__template_type', 'assigned_to']
    search_fields = ['template__name', 'completion_notes']
    ordering_fields = ['created_at', 'due_date', 'progress_percentage']
    ordering = ['-created_at']
    
    def get_queryset(self):
        user = self.request.user
        queryset = ChecklistExecution.objects.filter(
            template__restaurant=user.restaurant
        ).select_related(
            'template', 'assigned_to', 'current_step', 'approved_by'
        ).prefetch_related(
            'step_responses__step', 'step_responses__evidence', 'actions'
        )
        
        # Filter by assigned user if not manager
        if not user.is_manager and not user.is_admin:
            queryset = queryset.filter(assigned_to=user)
        
        return queryset
    
    def get_serializer_class(self):
        if self.action == 'create':
            return ChecklistExecutionCreateSerializer
        elif self.action in ['update', 'partial_update']:
            return ChecklistExecutionUpdateSerializer
        return ChecklistExecutionSerializer

    @action(detail=False, methods=['post'])
    def ensure_for_task(self, request):
        """Ensure a checklist execution exists for a given ShiftTask.

        Expects JSON body: { "task_id": "<uuid>" }

        - Validates the task belongs to the current user's restaurant.
        - Ensures the current user is the assignee for secure access.
        - If an execution already exists, returns it.
        - Otherwise, selects an active checklist template linked to the task's template
          (or falls back to category) and creates a new execution with step responses.
        """
        task_id = request.data.get('task_id')
        if not task_id:
            return Response({
                'error': 'task_id is required'
            }, status=status.HTTP_400_BAD_REQUEST)

        try:
            task = ShiftTask.objects.select_related(
                'assigned_to', 'shift__schedule__restaurant', 'task_template'
            ).get(id=task_id)
        except ShiftTask.DoesNotExist:
            return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

        # Secure authentication and authorization checks
        user = request.user
        if task.assigned_to_id != user.id:
            return Response(
                {'error': 'You can only open checklists for your assigned tasks'},
                status=status.HTTP_403_FORBIDDEN
            )

        restaurant = getattr(task.shift.schedule, 'restaurant', None)
        user_restaurant = getattr(user, 'restaurant', None)
        # Validate restaurant ownership without assignment expressions (for broader Python compatibility)
        if not restaurant or not user_restaurant:
            return Response(
                {'error': 'Task does not belong to your restaurant'},
                status=status.HTTP_403_FORBIDDEN
            )
        if restaurant.id != user_restaurant.id:
            return Response(
                {'error': 'Task does not belong to your restaurant'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check if there is already an execution linked to this task
        existing = ChecklistExecution.objects.filter(
            task=task
        ).select_related('template').first()
        if existing:
            serializer = self.get_serializer(existing)
            return Response(serializer.data)

        # Find an active checklist template: prefer direct link via task_template
        template = None
        if task.task_template_id:
            template = ChecklistTemplate.objects.filter(
                restaurant=user_restaurant,
                task_template_id=task.task_template_id,
                is_active=True
            ).prefetch_related('steps').first()

        # Fallback: match by category if available on template/task
        if not template:
            task_category = getattr(getattr(task, 'task_template', None), 'template_type', None)
            if task_category:
                template = ChecklistTemplate.objects.filter(
                    restaurant=user_restaurant,
                    category=task_category,
                    is_active=True
                ).prefetch_related('steps').first()

        if not template:
            return Response({
                'error': 'No active checklist template linked to this task for your restaurant'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Create new execution and pre-create step responses
        with transaction.atomic():
            execution = ChecklistExecution.objects.create(
                template=template,
                assigned_to=user,
                assigned_shift=getattr(task, 'assigned_shift', None) or getattr(task, 'shift', None),
                task=task,
                status='NOT_STARTED'
            )

            # Pre-create step responses
            steps = list(template.steps.all())
            for step in steps:
                ChecklistStepResponse.objects.create(
                    execution=execution,
                    step=step
                )

        serializer = self.get_serializer(execution)
        return Response(serializer.data, status=status.HTTP_201_CREATED)
    
    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Start a checklist execution"""
        execution = self.get_object()
        
        if execution.assigned_to != request.user:
            return Response(
                {'error': 'You can only start your own checklist executions'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if execution.status != 'NOT_STARTED':
            return Response(
                {'error': 'Checklist execution has already been started'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        execution.start_execution()
        serializer = self.get_serializer(execution)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete a checklist execution"""
        execution = self.get_object()
        
        if execution.assigned_to != request.user:
            return Response(
                {'error': 'You can only complete your own checklist executions'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        if execution.status != 'IN_PROGRESS':
            return Response(
                {'error': 'Checklist execution must be in progress to complete'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Validate all required steps are completed
        validation_service = ChecklistValidationService()
        validation_result = validation_service.validate_execution_completion(execution)
        
        if not validation_result['is_valid']:
            return Response(
                {'error': 'Cannot complete checklist', 'validation_errors': validation_result['errors']},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        completion_notes = request.data.get('completion_notes', '')
        execution.complete_execution(completion_notes)
        
        serializer = self.get_serializer(execution)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'])
    def sync(self, request, pk=None):
        """Sync offline data for a checklist execution"""
        execution = self.get_object()
        
        if execution.assigned_to != request.user:
            return Response(
                {'error': 'You can only sync your own checklist executions'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        serializer = ChecklistSyncSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            sync_service = ChecklistSyncService()
            result = sync_service.sync_execution_data(execution, serializer.validated_data)
            
            return Response({
                'success': True,
                'synced_items': result['synced_items'],
                'conflicts': result['conflicts'],
                'sync_version': execution.sync_version
            })
        
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    @action(detail=False, methods=['get'])
    def my_checklists(self, request):
        """Get current user's assigned checklists"""
        queryset = self.get_queryset().filter(assigned_to=request.user)
        
        # Filter by status if provided
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        page = self.paginate_queryset(queryset)
        if page is not None:
            serializer = self.get_serializer(page, many=True)
            return self.get_paginated_response(serializer.data)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def overdue(self, request):
        """Get overdue checklist executions"""
        now = timezone.now()
        queryset = self.get_queryset().filter(
            due_date__lt=now,
            status__in=['NOT_STARTED', 'IN_PROGRESS']
        )
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class ChecklistStepResponseViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing checklist step responses
    
    Endpoints:
    - GET /api/checklists/step-responses/ - List step responses
    - PUT /api/checklists/step-responses/{id}/ - Update step response
    - POST /api/checklists/step-responses/{id}/add-evidence/ - Add evidence
    """
    serializer_class = ChecklistStepResponseSerializer
    permission_classes = [permissions.IsAuthenticated]
    parser_classes = [JSONParser, MultiPartParser]
    
    def get_queryset(self):
        user = self.request.user
        return ChecklistStepResponse.objects.filter(
            execution__template__restaurant=user.restaurant,
            execution__assigned_to=user
        ).select_related('step', 'execution').prefetch_related('evidence')
    
    def get_serializer_class(self):
        if self.action in ['update', 'partial_update']:
            return ChecklistStepResponseUpdateSerializer
        return ChecklistStepResponseSerializer
    
    @action(detail=True, methods=['post'], parser_classes=[MultiPartParser])
    def add_evidence(self, request, pk=None):
        """Add evidence (photo, video, document) to a step response"""
        step_response = self.get_object()
        
        if 'file' not in request.FILES:
            return Response(
                {'error': 'No file provided'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        file_obj = request.FILES['file']
        evidence_type = request.data.get('evidence_type', 'PHOTO')
        visibility = request.data.get('visibility', 'TEAM')
        
        # Create evidence record
        evidence = ChecklistEvidence.objects.create(
            step_response=step_response,
            evidence_type=evidence_type,
            filename=file_obj.name,
            file_size=file_obj.size,
            mime_type=file_obj.content_type,
            visibility=visibility,
            file_path=f"checklist_evidence/{uuid.uuid4()}/{file_obj.name}"
        )
        
        # In a real implementation, you would save the file to storage
        # For now, we'll just create the record
        
        serializer = ChecklistEvidenceSerializer(evidence)
        return Response(serializer.data, status=status.HTTP_201_CREATED)


class ChecklistActionViewSet(viewsets.ModelViewSet):
    """
    ViewSet for managing checklist actions
    
    Endpoints:
    - GET /api/checklists/actions/ - List actions
    - POST /api/checklists/actions/ - Create action
    - PUT /api/checklists/actions/{id}/ - Update action
    - POST /api/checklists/actions/{id}/resolve/ - Resolve action
    """
    serializer_class = ChecklistActionSerializer
    permission_classes = [permissions.IsAuthenticated]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ['status', 'priority', 'assigned_to']
    search_fields = ['title', 'description']
    ordering_fields = ['created_at', 'due_date', 'priority']
    ordering = ['-created_at']
    
    def get_queryset(self):
        user = self.request.user
        queryset = ChecklistAction.objects.filter(
            execution__template__restaurant=user.restaurant
        ).select_related('assigned_to', 'created_by', 'resolved_by', 'execution')
        
        # Filter by assigned user if not manager
        if not user.is_manager and not user.is_admin:
            queryset = queryset.filter(
                Q(assigned_to=user) | Q(created_by=user)
            )
        
        return queryset
    
    def perform_create(self, serializer):
        serializer.save(created_by=self.request.user)
    
    @action(detail=True, methods=['post'])
    def resolve(self, request, pk=None):
        """Resolve a checklist action"""
        action = self.get_object()
        
        if action.status == 'RESOLVED':
            return Response(
                {'error': 'Action is already resolved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        resolution_notes = request.data.get('resolution_notes', '')
        
        action.status = 'RESOLVED'
        action.resolved_at = timezone.now()
        action.resolved_by = request.user
        action.resolution_notes = resolution_notes
        action.save()
        
        serializer = self.get_serializer(action)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def my_actions(self, request):
        """Get current user's assigned actions"""
        queryset = self.get_queryset().filter(assigned_to=request.user)
        
        # Filter by status if provided
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter)
        
        serializer = self.get_serializer(queryset, many=True)
        return Response(serializer.data)


class ChecklistAnalyticsViewSet(viewsets.ViewSet):
    """
    ViewSet for checklist analytics and reporting
    """
    permission_classes = [permissions.IsAuthenticated]
    
    @action(detail=False, methods=['get'])
    def dashboard_stats(self, request):
        """Get dashboard statistics for checklists"""
        user = request.user
        restaurant = user.restaurant
        
        # Get date range from query params
        from datetime import datetime, timedelta
        end_date = timezone.now()
        start_date = end_date - timedelta(days=30)  # Default to last 30 days
        
        if request.query_params.get('start_date'):
            start_date = datetime.fromisoformat(request.query_params['start_date'])
        if request.query_params.get('end_date'):
            end_date = datetime.fromisoformat(request.query_params['end_date'])
        
        executions = ChecklistExecution.objects.filter(
            template__restaurant=restaurant,
            created_at__range=[start_date, end_date]
        )
        
        stats = {
            'total_executions': executions.count(),
            'completed_executions': executions.filter(status='COMPLETED').count(),
            'in_progress_executions': executions.filter(status='IN_PROGRESS').count(),
            'overdue_executions': executions.filter(
                due_date__lt=timezone.now(),
                status__in=['NOT_STARTED', 'IN_PROGRESS']
            ).count(),
            'average_completion_rate': 0,
            'total_actions_created': ChecklistAction.objects.filter(
                execution__template__restaurant=restaurant,
                created_at__range=[start_date, end_date]
            ).count(),
            'open_actions': ChecklistAction.objects.filter(
                execution__template__restaurant=restaurant,
                status='OPEN'
            ).count(),
            'template_usage': {}
        }
        
        if stats['total_executions'] > 0:
            stats['average_completion_rate'] = (
                stats['completed_executions'] / stats['total_executions']
            ) * 100
        
        # Template usage breakdown
        template_usage = executions.values(
            'template__name', 'template__template_type'
        ).annotate(
            usage_count=Count('id'),
            completion_rate=Count('id', filter=Q(status='COMPLETED')) * 100.0 / Count('id')
        )
        
        stats['template_usage'] = list(template_usage)
        
        return Response(stats)