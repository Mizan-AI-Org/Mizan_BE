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
from datetime import datetime
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
    ChecklistSyncSerializer,
    ChecklistSubmissionListSerializer
)
from .services import (
    ChecklistSyncService, ChecklistValidationService, 
    ChecklistNotificationService
)
from accounts.permissions import IsAdminOrSuperAdmin, IsAdminOrManager
from accounts.models import AuditLog, CustomUser
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
    # Allow managers to read and create templates for operational use
    permission_classes = [IsAdminOrManager]
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    # Use category (not template_type) per model fields
    filterset_fields = ['category', 'is_active']
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
        template = serializer.save(
            restaurant=self.request.user.restaurant,
            created_by=self.request.user
        )
        # Audit logging for template creation
        self._audit(
            action_type='CREATE',
            entity=template,
            description=f"Created checklist template '{template.name}'",
            old_values={},
            new_values=self.get_serializer(template).data
        )

    def perform_update(self, serializer):
        # Capture old values before update
        instance = self.get_object()
        old_values = self.get_serializer(instance).data
        template = serializer.save()
        # Audit logging for template update
        self._audit(
            action_type='UPDATE',
            entity=template,
            description=f"Updated checklist template '{template.name}'",
            old_values=old_values,
            new_values=self.get_serializer(template).data
        )

    def perform_destroy(self, instance):
        # Audit logging for template deletion
        old_values = self.get_serializer(instance).data
        name = instance.name
        template_id = str(instance.id)
        restaurant = instance.restaurant
        instance.delete()
        AuditLog.create_log(
            restaurant=restaurant,
            user=self.request.user,
            action_type='DELETE',
            entity_type='ChecklistTemplate',
            description=f"Deleted checklist template '{name}'",
            entity_id=template_id,
            old_values=old_values,
            new_values={},
            ip_address=self._get_ip(),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
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
            category=template.category,
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
        # Audit log for duplication
        self._audit(
            action_type='CREATE',
            entity=new_template,
            description=f"Duplicated checklist template '{template.name}' -> '{new_template.name}'",
            old_values={},
            new_values=serializer.data
        )
        return Response(serializer.data, status=status.HTTP_201_CREATED)

    @action(detail=True, methods=['post'], permission_classes=[IsAdminOrManager])
    def assign(self, request, pk=None):
        """Assign this template to a staff member (admin-only).

        Expects JSON body: { "user_id": "<uuid>", "due_date": "<iso8601 optional>" }
        Creates a ChecklistExecution for the specified user and sends a notification.
        """
        template = self.get_object()
        user_id = request.data.get('user_id')
        due_date_str = request.data.get('due_date')

        if not user_id:
            return Response({ 'error': 'user_id is required' }, status=status.HTTP_400_BAD_REQUEST)

        try:
            assignee = CustomUser.objects.get(id=user_id, restaurant=request.user.restaurant)
        except CustomUser.DoesNotExist:
            return Response({ 'error': 'User not found in your restaurant' }, status=status.HTTP_404_NOT_FOUND)

        # Create execution
        with transaction.atomic():
            parsed_due = None
            if due_date_str:
                try:
                    parsed_due = datetime.fromisoformat(due_date_str)
                    if timezone.is_naive(parsed_due):
                        parsed_due = timezone.make_aware(parsed_due)
                except Exception:
                    return Response({ 'error': 'Invalid due_date format. Use ISO 8601.' }, status=status.HTTP_400_BAD_REQUEST)
            execution = ChecklistExecution.objects.create(
                template=template,
                assigned_to=assignee,
                due_date=parsed_due,
                status='NOT_STARTED'
            )
            # Pre-create step responses
            for step in template.steps.all():
                ChecklistStepResponse.objects.create(execution=execution, step=step)

        # Send notification
        notifier = ChecklistNotificationService()
        notifier.send_assignment_notification(execution)

        # Audit log for assignment (as template-related operational action)
        AuditLog.create_log(
            restaurant=request.user.restaurant,
            user=request.user,
            action_type='OTHER',
            entity_type='ChecklistTemplate',
            entity_id=str(template.id),
            description=f"Assigned template '{template.name}' to {assignee.email}",
            old_values={},
            new_values={'execution_id': str(execution.id), 'assigned_to': str(assignee.id), 'due_date': due_date_str},
            ip_address=self._get_ip(),
            user_agent=request.META.get('HTTP_USER_AGENT', '')
        )

        exec_serializer = ChecklistExecutionSerializer(execution, context={'request': request})
        return Response(exec_serializer.data, status=status.HTTP_201_CREATED)

    def _audit(self, action_type: str, entity, description: str, old_values=None, new_values=None):
        """Helper to create audit logs consistently"""
        AuditLog.create_log(
            restaurant=self.request.user.restaurant,
            user=self.request.user,
            action_type=action_type,
            entity_type='ChecklistTemplate',
            entity_id=str(entity.id),
            description=description,
            old_values=old_values or {},
            new_values=new_values or {},
            ip_address=self._get_ip(),
            user_agent=self.request.META.get('HTTP_USER_AGENT', '')
        )

    def _get_ip(self):
        forwarded = self.request.META.get('HTTP_X_FORWARDED_FOR')
        if forwarded:
            return forwarded.split(',')[0].strip()
        return self.request.META.get('REMOTE_ADDR')
    
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
    filterset_fields = ['status', 'template__category', 'assigned_to']
    search_fields = ['template__name', 'completion_notes']
    ordering_fields = ['created_at', 'updated_at', 'completed_at', 'due_date', 'progress_percentage']
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
        
        # Filter by assigned user if not manager/admin/owner
        allowed_roles = {'SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER'}
        if str(getattr(user, 'role', '')).upper() not in allowed_roles:
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
                'assigned_to', 'shift__schedule__restaurant', 'category'
            ).get(id=task_id)
        except ShiftTask.DoesNotExist:
            return Response({'error': 'Task not found'}, status=status.HTTP_404_NOT_FOUND)

        # Secure authentication and authorization checks
        user = request.user
        restaurant = getattr(task.shift.schedule, 'restaurant', None)
        user_restaurant = getattr(user, 'restaurant', None)

        # Validate restaurant ownership without assignment expressions (for broader Python compatibility)
        if not restaurant or not user_restaurant or restaurant.id != user_restaurant.id:
            return Response(
                {'error': 'Task does not belong to your restaurant'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Allow the assigned staff to ensure their own checklist.
        # Also allow managers/admins/owners of the same restaurant to ensure checklists
        # on behalf of the assigned staff.
        is_assigned_staff = (task.assigned_to_id == user.id)
        is_manager_or_admin = (getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER'])
        if not (is_assigned_staff or is_manager_or_admin):
            return Response(
                {'error': 'You do not have permission to open checklists for this task'},
                status=status.HTTP_403_FORBIDDEN
            )

        # Check if there is already an execution linked to this task
        existing = ChecklistExecution.objects.filter(
            task=task
        ).select_related('template').first()
        if existing:
            serializer = self.get_serializer(existing)
            return Response(serializer.data)

        # Find an active checklist template by category (ShiftTask has no direct task_template link)
        template = None
        category_name = getattr(getattr(task, 'category', None), 'name', None)
        if category_name:
            # Prefer templates explicitly tagged with the same category
            template = ChecklistTemplate.objects.filter(
                restaurant=user_restaurant,
                is_active=True,
                category=category_name
            ).prefetch_related('steps').first()

            # If not found, try templates linked via TaskTemplate where template_type matches category
            if not template:
                template = ChecklistTemplate.objects.filter(
                    restaurant=user_restaurant,
                    is_active=True,
                    task_template__template_type=category_name
                ).prefetch_related('steps').first()

        if not template:
            return Response({
                'error': 'No active checklist template linked to this task for your restaurant'
            }, status=status.HTTP_400_BAD_REQUEST)

        # Create new execution and pre-create step responses
        with transaction.atomic():
            # Always assign the execution to the task's assignee (staff member)
            execution = ChecklistExecution.objects.create(
                template=template,
                assigned_to=task.assigned_to,
                assigned_shift=getattr(task, 'assigned_shift', None) or getattr(task, 'shift', None),
                task=task,
                status='NOT_STARTED',
                due_date=getattr(task, 'due_date', None)
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
        try:
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='CREATE',
                entity_type='ChecklistExecution',
                entity_id=str(execution.id),
                description='Checklist execution started',
                old_values={},
                new_values={'status': 'IN_PROGRESS', 'started_at': execution.started_at.isoformat()},
                ip_address=request.META.get('REMOTE_ADDR', ''),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except Exception:
            pass
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
        try:
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='UPDATE',
                entity_type='ChecklistExecution',
                entity_id=str(execution.id),
                description='Checklist execution completed',
                old_values={},
                new_values={'status': 'COMPLETED', 'completed_at': execution.completed_at.isoformat()},
                ip_address=request.META.get('REMOTE_ADDR', ''),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except Exception:
            pass
        
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
            
            try:
                AuditLog.create_log(
                    restaurant=request.user.restaurant,
                    user=request.user,
                    action_type='UPDATE',
                    entity_type='ChecklistExecution',
                    entity_id=str(execution.id),
                    description='Checklist execution synced',
                    old_values={},
                    new_values={'synced_items': result['synced_items']},
                    ip_address=request.META.get('REMOTE_ADDR', ''),
                    user_agent=request.META.get('HTTP_USER_AGENT', '')
                )
            except Exception:
                pass
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
        
        ordering = request.query_params.get('ordering')
        if ordering:
            try:
                queryset = queryset.order_by(ordering)
            except Exception:
                pass
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

    @action(detail=False, methods=['get'])
    def submitted(self, request):
        """List completed submissions for managers of the current restaurant"""
        user = request.user
        restaurant = getattr(user, 'restaurant', None)
        if not restaurant:
            return Response({'error': 'No restaurant context'}, status=status.HTTP_400_BAD_REQUEST)

        # Authorization: allow SUPER_ADMIN/ADMIN/OWNER/MANAGER to view submissions for their restaurant
        allowed_roles = {'SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER'}
        if str(getattr(user, 'role', '')).upper() not in allowed_roles:
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        qs = (
            ChecklistExecution.objects
            .filter(template__restaurant=restaurant, status='COMPLETED')
            .select_related('template', 'assigned_to', 'approved_by')
            .prefetch_related('step_responses__step', 'step_responses__evidence', 'actions')
        )

        # Optional date filter
        date_str = request.query_params.get('date')
        if date_str:
            try:
                from datetime import datetime
                target = datetime.fromisoformat(date_str)
                from django.db.models.functions import TruncDate
                qs = qs.annotate(comp_date=TruncDate('completed_at')).filter(comp_date=target.date())
            except Exception:
                pass

        # Order newest completions first
        qs = qs.order_by('-completed_at', '-updated_at')

        page = self.paginate_queryset(qs)
        if page is not None:
            serializer = ChecklistSubmissionListSerializer(page, many=True, context={'request': request})
            return self.get_paginated_response(serializer.data)

        serializer = ChecklistSubmissionListSerializer(qs, many=True, context={'request': request})
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def manager_review(self, request, pk=None):
        """Manager approval or rejection of a completed checklist"""
        user = request.user
        allowed_roles = {'SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER'}
        if str(getattr(user, 'role', '')).upper() not in allowed_roles:
            return Response({'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        try:
            execution = ChecklistExecution.objects.get(id=pk)
        except ChecklistExecution.DoesNotExist:
            return Response({'error': 'Execution not found'}, status=status.HTTP_404_NOT_FOUND)

        decision = str(request.data.get('decision', '')).upper()
        if decision not in {'APPROVED', 'REJECTED'}:
            return Response({'error': 'Invalid decision'}, status=status.HTTP_400_BAD_REQUEST)

        from django.utils import timezone
        if decision == 'APPROVED':
            execution.supervisor_approved = True
            execution.approved_by = user
            execution.approved_at = timezone.now()
        else:
            execution.supervisor_approved = False
            execution.approved_by = None
            execution.approved_at = None
            # Optionally attach an action to follow up on rejection
            note = str(request.data.get('reason', '')).strip() or 'Rejected by manager'
            try:
                ChecklistAction.objects.create(
                    execution=execution,
                    title='Checklist Rejected',
                    description=note,
                    priority='MEDIUM',
                    assigned_to=execution.assigned_to,
                )
            except Exception:
                pass

        execution.save()

        # Audit log
        try:
            AuditLog.create_log(
                restaurant=user.restaurant,
                user=user,
                action_type='OTHER',
                entity_type='ChecklistExecution',
                entity_id=str(execution.id),
                description=f'Manager review decision: {decision}',
                old_values={},
                new_values={'decision': decision},
                ip_address=request.META.get('REMOTE_ADDR', ''),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
        except Exception:
            pass

        data = self.get_serializer(execution).data
        data.update({'review_status': decision})
        return Response(data, status=status.HTTP_200_OK)


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
        
        # Filter by assigned user if not manager/admin/owner
        allowed_roles = {'SUPER_ADMIN', 'ADMIN', 'OWNER', 'MANAGER'}
        if str(getattr(user, 'role', '')).upper() not in allowed_roles:
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


# Import for function-based views
from rest_framework.decorators import api_view, permission_classes as perm_classes, authentication_classes
from django.conf import settings
from .serializers import ChecklistSyncSerializer
from scheduling.models import AssignedShift


@api_view(['GET'])
@perm_classes([permissions.IsAuthenticated])
def get_shift_checklists(request):
    """
    Get checklist templates assigned to the staff's current active shift.
    
    This endpoint is called after a successful clock-in to determine which
    checklists the staff member needs to complete during their shift.
    
    Returns:
        - shift_id: The active shift ID
        - checklists: List of checklist templates with their steps
        - message: Status message if no shift/checklists found
    """
    user = request.user
    today = timezone.now().date()
    now = timezone.now()
    
    # Find the active shift for today — prefer current/upcoming when multiple shifts exist
    shift_qs = AssignedShift.objects.filter(
        Q(staff=user) | Q(staff_members=user),
        shift_date=today,
        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS']
    ).distinct().prefetch_related('task_templates').order_by('start_time')
    active_shift = shift_qs.filter(end_time__gt=now).first() or shift_qs.first()
    
    if not active_shift:
        return Response({
            'shift_id': None,
            'checklists': [],
            'message': 'No active shift found for today'
        })
    
    # Get task templates assigned to this shift
    task_templates = active_shift.task_templates.filter(is_active=True)
    
    if not task_templates.exists():
        return Response({
            'shift_id': str(active_shift.id),
            'checklists': [],
            'message': 'No task templates assigned to this shift'
        })
    
    # Get checklist templates linked to these task templates
    checklist_templates = ChecklistTemplate.objects.filter(
        task_template__in=task_templates,
        is_active=True
    ).prefetch_related('steps').distinct()
    
    # If no checklist templates found via task_template link, 
    # try to find by matching category to template_type
    if not checklist_templates.exists():
        task_types = task_templates.values_list('template_type', flat=True)
        checklist_templates = ChecklistTemplate.objects.filter(
            restaurant=user.restaurant,
            is_active=True,
            category__in=task_types
        ).prefetch_related('steps').distinct()
    
    # Build response with checklist details
    checklists_data = []
    for template in checklist_templates:
        steps = template.steps.all().order_by('order')
        checklists_data.append({
            'id': str(template.id),
            'name': template.name,
            'description': template.description,
            'category': template.category,
            'estimated_duration_minutes': (
                int(template.estimated_duration.total_seconds() / 60) 
                if template.estimated_duration else None
            ),
            'requires_supervisor_approval': template.requires_supervisor_approval,
            'total_steps': steps.count(),
            'steps': [
                {
                    'id': str(step.id),
                    'order': step.order,
                    'title': step.title,
                    'description': step.description,
                    'step_type': step.step_type,
                    'is_required': step.is_required,
                    'requires_photo': step.requires_photo,
                    'requires_note': step.requires_note,
                }
                for step in steps
            ]
        })
    
    return Response({
        'shift_id': str(active_shift.id),
        'shift_date': active_shift.shift_date.isoformat(),
        'shift_start': active_shift.start_time.isoformat() if active_shift.start_time else None,
        'checklists': checklists_data,
        'message': f'Found {len(checklists_data)} checklist(s) for your shift' if checklists_data else 'No checklists assigned to your shift'
    })

@api_view(['GET'])
@authentication_classes([])
@perm_classes([permissions.AllowAny])
def agent_get_shift_checklists(request):
    """
    Agent-authenticated endpoint to get checklist templates assigned to a staff member's active shift.
    
    Query Params:
        staff_id: ID of the staff member
        
    Returns:
        - shift_id
        - checklists
        - message
    """
    # Validate Agent Key (same pattern as timeclock)
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    
    if not expected_key:
        return Response({'error': 'Agent key not configured'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
            
    if not auth_header or auth_header != f"Bearer {expected_key}":
        return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
        
    staff_id = request.query_params.get('staff_id')
    if not staff_id:
        return Response({'error': 'staff_id is required'}, status=status.HTTP_400_BAD_REQUEST)
        
    try:
        user = CustomUser.objects.get(id=staff_id)
    except CustomUser.DoesNotExist:
        return Response({'error': 'Staff not found'}, status=status.HTTP_404_NOT_FOUND)
        
    today = timezone.now().date()
    now = timezone.now()
    
    # Find the active shift for today — prefer current/upcoming when multiple shifts exist
    shift_qs = AssignedShift.objects.filter(
        Q(staff=user) | Q(staff_members=user),
        shift_date=today,
        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS']
    ).distinct().prefetch_related('task_templates').order_by('start_time')
    active_shift = shift_qs.filter(end_time__gt=now).first() or shift_qs.first()
    
    if not active_shift:
        return Response({
            'shift_id': None,
            'checklists': [],
            'message': 'No active shift found for today'
        })
    
    # Get task templates assigned to this shift
    task_templates = active_shift.task_templates.filter(is_active=True)
    
    # Get checklist templates linked to these task templates
    checklist_templates = ChecklistTemplate.objects.filter(
        task_template__in=task_templates,
        is_active=True
    ).prefetch_related('steps').distinct()
    
    # If no checklist templates found via task_template link, 
    # try to find by matching category to template_type
    if not checklist_templates.exists() and task_templates.exists():
        task_types = task_templates.values_list('template_type', flat=True)
        checklist_templates = ChecklistTemplate.objects.filter(
            restaurant=user.restaurant,
            is_active=True,
            category__in=task_types
        ).prefetch_related('steps').distinct()
    
    # Build response with checklist details (mirrors existing get_shift_checklists structure)
    checklists_data = []
    for template in checklist_templates:
        steps = template.steps.all().order_by('order')
        checklists_data.append({
            'id': str(template.id),
            'name': template.name,
            'description': template.description,
            'category': template.category,
            'estimated_duration_minutes': (
                int(template.estimated_duration.total_seconds() / 60) 
                if template.estimated_duration else None
            ),
            'requires_supervisor_approval': template.requires_supervisor_approval,
            'total_steps': steps.count(),
            'steps': [
                {
                    'id': str(step.id),
                    'order': step.order,
                    'title': step.title,
                    'description': step.description,
                    'step_type': step.step_type,
                    'is_required': step.is_required,
                    'requires_photo': step.requires_photo,
                    'requires_note': step.requires_note,
                }
                for step in steps
            ]
        })
    
    return Response({
        'shift_id': str(active_shift.id),
        'checklists': checklists_data,
        'message': f'Found {len(checklists_data)} checklist(s) for your shift' if checklists_data else 'No checklists assigned to your shift'
    })

@api_view(['POST'])
@authentication_classes([])
@perm_classes([permissions.AllowAny])
def agent_initiate_shift_checklists(request):
    """
    Agent-authenticated endpoint to initiate the first checklist for a staff member's active shift.
    
    Accepts:
        staff_id: ID of the staff member (in body)
        
    Returns:
        - execution_id
        - checklist (name, etc)
        - current_step (first step)
        - status: started/restored
    """
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
        
    try:
        user = CustomUser.objects.get(id=staff_id)
    except CustomUser.DoesNotExist:
        return Response({'error': 'Staff not found'}, status=status.HTTP_404_NOT_FOUND)
        
    today = timezone.now().date()
    
    # 1. Find the active shift
    active_shift = AssignedShift.objects.filter(
        staff=user,
        shift_date=today,
        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS']
    ).prefetch_related('task_templates').first()
    
    if not active_shift:
        return Response({'status': 'no_shift', 'message': 'No active shift found'})
    
    # 2. Find associated checklists
    checklist_templates = ChecklistTemplate.objects.filter(
        task_template__in=active_shift.task_templates.filter(is_active=True),
        is_active=True
    ).prefetch_related('steps').distinct()
    
    if not checklist_templates.exists():
        # Fallback: try to find by matching category to template_type of assigned tasks
        task_types = active_shift.task_templates.filter(is_active=True).values_list('template_type', flat=True)
        if task_types:
            checklist_templates = ChecklistTemplate.objects.filter(
                restaurant=user.restaurant,
                is_active=True,
                category__in=task_types
            ).prefetch_related('steps').distinct()
            
    # Final Fallback: if STILL no checklists, just get all active ones for this restaurant
    # This ensures something is always triggered if any checklist exists.
    if not checklist_templates.exists():
        checklist_templates = ChecklistTemplate.objects.filter(
            restaurant=user.restaurant,
            is_active=True
        ).prefetch_related('steps').distinct()
        
    if not checklist_templates.exists():
        return Response({'status': 'no_checklists', 'message': 'No checklists assigned'})
        
    # 3. Pick the first one
    template = checklist_templates.first() # Deterministic ordering? default pk
    
    if not template.steps.exists():
        return Response({'status': 'empty_checklist', 'message': 'Checklist has no steps'})

    # 4. Ensure execution exists
    execution, created = ChecklistExecution.objects.get_or_create(
        template=template,
        assigned_to=user,
        assigned_shift=active_shift,
        defaults={
            'status': 'NOT_STARTED',
            'task': None # Link to specific task if we had one
        }
    )
    
    if created:
        # Pre-create step responses
        for step in template.steps.all():
            ChecklistStepResponse.objects.create(execution=execution, step=step)
            
    # 5. Start if not started
    if execution.status == 'NOT_STARTED':
        execution.start_execution()
        try:
            AuditLog.create_log(
                restaurant=user.restaurant,
                user=user,
                action_type='CREATE',
                entity_type='ChecklistExecution',
                entity_id=str(execution.id),
                description='Checklist execution auto-started by agent',
                old_values={},
                new_values={'status': 'IN_PROGRESS'},
                ip_address=request.META.get('REMOTE_ADDR', ''),
                user_agent='Lua Agent'
            )
        except Exception:
            pass
            
    # 6. Return first incomplete step info
    # Find next incomplete step
    next_response = execution.step_responses.filter(is_completed=False).select_related('step').order_by('step__order').first()
    
    if not next_response:
        return Response({'status': 'completed', 'message': 'All steps completed'})
        
    step = next_response.step
    total_steps = template.steps.count()
    
    return Response({
        'status': 'started',
        'execution_id': str(execution.id),
        'checklist': {
            'id': str(template.id),
            'name': template.name,
            'total_steps': total_steps
        },
        'current_step': {
            'index': step.order,
            'total': total_steps,
            'id': str(step.id),
            'title': step.title,
            'description': step.description,
            'requires_photo': step.requires_photo,
            'step_type': step.step_type
        },
        'message': f'Starting checklist: {template.name}'
    })

@api_view(['POST'])
@authentication_classes([])
@perm_classes([permissions.AllowAny])
def agent_sync_checklist_response(request, execution_id):
    """
    Agent-authenticated endpoint to sync checklist response data.
    """
    # Validate Agent Key
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    
    if not expected_key or auth_header != f"Bearer {expected_key}":
        return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
        
    try:
        execution = ChecklistExecution.objects.get(id=execution_id)
    except ChecklistExecution.DoesNotExist:
        return Response({'error': 'Execution not found'}, status=status.HTTP_404_NOT_FOUND)
        
    data = request.data.copy()
    data['execution_id'] = str(execution_id)
    
    serializer = ChecklistSyncSerializer(data=data, context={'request': request, 'bypass_user_check': True})
    if serializer.is_valid():
        sync_service = ChecklistSyncService()
        result = sync_service.sync_execution_data(execution, serializer.validated_data)
        
        try:
            AuditLog.create_log(
                restaurant=execution.template.restaurant,
                user=execution.assigned_to,
                action_type='UPDATE',
                entity_type='ChecklistExecution',
                entity_id=str(execution.id),
                description='Checklist response synced by agent',
                old_values={},
                new_values={'synced_items': result['synced_items']},
                ip_address=request.META.get('REMOTE_ADDR', ''),
                user_agent='Lua Agent'
            )
        except Exception:
            pass
            
        return Response({
            'success': True,
            'synced_items': result['synced_items'],
            'conflicts': result['conflicts']
        })
        
    return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@authentication_classes([])
@perm_classes([permissions.AllowAny])
def agent_complete_checklist_execution(request, execution_id):
    """
    Agent-authenticated endpoint to complete a checklist execution.
    """
    # Validate Agent Key
    auth_header = request.headers.get('Authorization')
    expected_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', None)
    
    if not expected_key or auth_header != f"Bearer {expected_key}":
        return Response({'error': 'Unauthorized'}, status=status.HTTP_401_UNAUTHORIZED)
        
    try:
        execution = ChecklistExecution.objects.get(id=execution_id)
    except ChecklistExecution.DoesNotExist:
        return Response({'error': 'Execution not found'}, status=status.HTTP_404_NOT_FOUND)
        
    if execution.status != 'IN_PROGRESS':
        return Response({'error': 'Checklist execution must be in progress'}, status=status.HTTP_400_BAD_REQUEST)
        
    completion_notes = request.data.get('completion_notes', 'Completed via Agent')
    execution.complete_execution(completion_notes)
    
    try:
        AuditLog.create_log(
            restaurant=execution.template.restaurant,
            user=execution.assigned_to,
            action_type='UPDATE',
            entity_type='ChecklistExecution',
            entity_id=str(execution.id),
            description='Checklist completed by agent',
            old_values={},
            new_values={'status': 'COMPLETED'},
            ip_address=request.META.get('REMOTE_ADDR', ''),
            user_agent='Lua Agent'
        )
    except Exception:
        pass
        
    return Response({'success': True, 'status': 'COMPLETED'})