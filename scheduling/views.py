from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from django.utils import timezone
from django.db.models import Q
from django.db import IntegrityError
from django.core.exceptions import ValidationError
from rest_framework.exceptions import ValidationError as DRFValidationError
from datetime import datetime, timedelta
import logging, sys

from .models import (
    ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift, 
    ShiftSwapRequest, TaskCategory, ShiftTask, Timesheet, TimesheetEntry
)
from .serializers import (
    ScheduleTemplateSerializer,
    TemplateShiftSerializer,
    WeeklyScheduleSerializer,
    AssignedShiftSerializer,
    ShiftSwapRequestSerializer,
    TaskCategorySerializer,
    ShiftTaskSerializer,
    TimesheetSerializer,
    TimesheetEntrySerializer,
)
from .services import SchedulingService, OptimizationService
from .task_assignment_service import TaskAssignmentService
from accounts.views import IsManagerOrAdmin
from notifications.services import notification_service
from django.conf import settings
from core.utils import build_tenant_context
from accounts.services import RoleManagementService
from accounts.models import CustomUser, AuditLog

# Module logger
logger = logging.getLogger(__name__)


class ScheduleTemplateListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant).order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class ScheduleTemplateRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant)

class TemplateShiftListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TemplateShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        template_id = self.kwargs.get('template_pk')
        return TemplateShift.objects.filter(template__id=template_id, template__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        template_id = self.kwargs.get('template_pk')
        template = ScheduleTemplate.objects.get(id=template_id, restaurant=self.request.user.restaurant)
        serializer.save(template=template)

class TemplateShiftRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TemplateShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        template_id = self.kwargs.get('template_pk')
        return TemplateShift.objects.filter(template__id=template_id, template__restaurant=self.request.user.restaurant)

class WeeklyScheduleListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant).order_by('-week_start')

    def list(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        try:
            queryset = self.get_queryset()
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
        except Exception as e:
            logger.exception("WeeklySchedule list failed")
            return Response({"detail": "Failed to load weekly schedules"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except IntegrityError:
            return Response(
                {"detail": "A weekly schedule for this restaurant and week_start already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class WeeklyScheduleRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant)


class WeeklyScheduleViewSet(viewsets.ModelViewSet):
    """ViewSet for weekly schedules with analytics endpoints"""
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    
    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant)
    
    def list(self, request, *args, **kwargs):
        import logging
        logger = logging.getLogger(__name__)
        try:
            queryset = self.filter_queryset(self.get_queryset())
            page = self.paginate_queryset(queryset)
            if page is not None:
                serializer = self.get_serializer(page, many=True)
                return self.get_paginated_response(serializer.data)
            serializer = self.get_serializer(queryset, many=True)
            return Response(serializer.data)
        except Exception:
            logger.exception("WeeklySchedule v2 list failed")
            return Response({"detail": "Failed to load weekly schedules"}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        try:
            self.perform_create(serializer)
        except IntegrityError:
            return Response(
                {"detail": "A weekly schedule for this restaurant and week_start already exists."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)
    
    @action(detail=True, methods=['get'])
    def coverage(self, request, pk=None):
        """Get staff coverage details for schedule"""
        schedule = self.get_object()
        role = request.query_params.get('role')
        
        coverage_data = SchedulingService.get_staff_coverage(str(schedule.id), role)
        return Response(coverage_data)
    
    @action(detail=True, methods=['get'])
    def analytics(self, request, pk=None):
        """Get comprehensive analytics for schedule"""
        schedule = self.get_object()
        analytics = SchedulingService.get_schedule_analytics(str(schedule.id))
        return Response(analytics)
    
    @action(detail=True, methods=['post'])
    def publish(self, request, pk=None):
        """Publish schedule"""
        ctx = build_tenant_context(request)
        if not ctx:
            return Response({'detail': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)
        schedule = self.get_object()
        if str(getattr(request.user.restaurant, 'id', '')) != str(getattr(schedule.restaurant, 'id', '')):
            return Response({'detail': 'Cross-tenant access denied'}, status=status.HTTP_403_FORBIDDEN)
        if not RoleManagementService.check_user_permission(request.user, schedule.restaurant, 'schedule.edit'):
            return Response({'detail': 'Insufficient permissions'}, status=status.HTTP_403_FORBIDDEN)
        schedule.is_published = True
        schedule.save()
        shifts = AssignedShift.objects.filter(schedule=schedule, schedule__restaurant=request.user.restaurant).select_related('staff')
        by_staff = {}
        for s in shifts:
            key = str(s.staff.id)
            by_staff.setdefault(key, {'user': s.staff, 'items': []})
            by_staff[key]['items'].append(s)
        for _, data in by_staff.items():
            user = data['user']
            lines = []
            lines.append('📅 Your Schedule is Live!')
            lines.append(f"Week of {schedule.week_start} to {schedule.week_end}")
            lines.append('Shifts:')
            for it in data['items']:
                lines.append(f"{it.shift_date} {it.start_time} - {it.end_time} ({it.role})")
            hrs = sum([getattr(it, 'actual_hours', 0) or 0 for it in data['items']])
            lines.append(f"Total hours: {round(hrs, 2)}")
            view_link = f"{settings.FRONTEND_URL}/staff-dashboard"
            swap_link = f"{settings.FRONTEND_URL}/dashboard/swap-requests"
            lines.append(f"View full schedule: {view_link}")
            lines.append(f"Need changes? Request swap: {swap_link}")
            msg = '\n'.join(lines)
            notification_service.send_custom_notification(
                recipient=user,
                message=msg,
                notification_type='SHIFT_ASSIGNED',
                title='Your Schedule is Live',
                channels=['whatsapp','app']
            )
        return Response({'detail': 'Schedule published successfully'})
    
    @action(detail=True, methods=['post'])
    def generate_from_template(self, request, pk=None):
        """Generate shifts from template"""
        schedule = self.get_object()
        template_id = request.data.get('template_id')
        week_start = request.data.get('week_start')
        
        if not template_id or not week_start:
            return Response(
                {'detail': 'template_id and week_start are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            week_start_date = datetime.strptime(week_start, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Invalid date format. Use YYYY-MM-DD'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        success, message = SchedulingService.generate_schedule_from_template(
            str(schedule.id),
            template_id,
            week_start_date
        )
        
        if success:
            return Response({'detail': message})
        else:
            logger.debug("Shift reminder: no notification sent (trace omitted)")
    
    @action(detail=False, methods=['post'])
    def optimize(self, request):
        """
        Generate optimized schedule for a week
        """
        week_start = request.data.get('week_start')
        department = request.data.get('department')
        
        if not week_start:
            return Response(
                {'detail': 'week_start is required (YYYY-MM-DD)'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Build tenant context if needed, but request.user.restaurant should be available
        if not request.user.restaurant:
             return Response(
                {'detail': 'User is not associated with a restaurant'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        result = OptimizationService.optimize_schedule(
            str(request.user.restaurant.id),
            week_start,
            department
        )
        
        if result.get('error'):
            return Response(result, status=status.HTTP_400_BAD_REQUEST)
            
        return Response(result)

class AssignedShiftListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        schedule_id = self.kwargs.get('schedule_pk')
        return AssignedShift.objects.filter(
            schedule__id=schedule_id, schedule__restaurant=self.request.user.restaurant
        ).prefetch_related('task_templates', 'tasks', 'staff_members').select_related('staff', 'schedule')

    def perform_create(self, serializer):
        schedule_id = self.kwargs.get('schedule_pk')
        schedule = WeeklySchedule.objects.get(id=schedule_id, restaurant=self.request.user.restaurant)
        shift = serializer.save(schedule=schedule)
        # Ensure staff receive schedule reminder (same as AssignedShiftViewSet)
        SchedulingService.notify_shift_assignment(shift, force_whatsapp=True)

    def create(self, request, *args, **kwargs):
        """Create assigned shift under a schedule, with friendly duplicate/validation errors."""
        serializer = self.get_serializer(data=request.data)
        try:
            serializer.is_valid(raise_exception=True)
        except DRFValidationError as ve:
            # Surface field errors clearly to the client
            return Response({
                'detail': 'Validation error',
                'errors': ve.detail
            }, status=status.HTTP_400_BAD_REQUEST)

        # Note: We no longer block multiple same-day shifts for the same staff.
        # Overlap prevention is enforced in AssignedShift.clean() and via detect_conflicts.

        try:
            self.perform_create(serializer)
        except IntegrityError:
            # If any DB integrity error occurs, surface a friendly message
            return Response({"detail": "Shift creation violated a database constraint."}, status=status.HTTP_400_BAD_REQUEST)
        except ValidationError as ve:
            # Surface model.clean() validation messages (e.g., overlaps)
            return Response({"detail": str(ve)}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            # Catch-all to avoid 500s and expose the error during testing
            return Response({"detail": f"Shift creation failed: {str(e)}"}, status=status.HTTP_400_BAD_REQUEST)

        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)

class AssignedShiftRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        schedule_id = self.kwargs.get('schedule_pk')
        return AssignedShift.objects.filter(
            schedule__id=schedule_id, schedule__restaurant=self.request.user.restaurant
        ).prefetch_related('task_templates', 'tasks', 'staff_members').select_related('staff', 'schedule')


class AssignedShiftViewSet(viewsets.ModelViewSet):
    """ViewSet for assigned shifts with conflict detection"""
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    def get_queryset(self):
        user = self.request.user
        queryset = AssignedShift.objects.filter(schedule__restaurant=user.restaurant)
        
        # Filter by schedule if provided
        schedule_id = self.request.query_params.get('schedule_id')
        if schedule_id:
            queryset = queryset.filter(schedule__id=schedule_id)
        
        # Filter by staff if provided
        staff_id = self.request.query_params.get('staff_id')
        if staff_id:
            queryset = queryset.filter(staff__id=staff_id)
        
        # Filter by date range if provided
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(shift_date__gte=date_from)
        if date_to:
            queryset = queryset.filter(shift_date__lte=date_to)
        
        return queryset.prefetch_related('task_templates', 'tasks', 'staff_members').select_related(
            'staff', 'schedule'
        ).order_by('shift_date', 'start_time')

    def perform_create(self, serializer):
        """Create shift and send notification"""
        shift = serializer.save()
        # Assign a deterministic "random" color per staff
        SchedulingService.ensure_shift_color(shift)
        # Send notification to staff about the new shift
        # Guarantee WhatsApp delivery for manually created shifts as well.
        SchedulingService.notify_shift_assignment(shift, force_whatsapp=True)
    
    def perform_destroy(self, instance):
        """Delete shift and send notification"""
        # Send notification before deleting
        SchedulingService.notify_shift_cancellation(instance)
        instance.delete()
    
    @action(detail=False, methods=['get'])
    def detect_conflicts(self, request):
        """Detect scheduling conflicts for a staff member"""
        staff_id = request.query_params.get('staff_id')
        shift_date = request.query_params.get('shift_date')
        start_time = request.query_params.get('start_time')
        end_time = request.query_params.get('end_time')
        
        if not all([staff_id, shift_date, start_time, end_time]):
            return Response(
                {'detail': 'staff_id, shift_date, start_time, and end_time are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from datetime import time as time_type
            shift_date_obj = datetime.strptime(shift_date, '%Y-%m-%d').date()
            start_time_obj = datetime.strptime(start_time, '%H:%M:%S').time()
            end_time_obj = datetime.strptime(end_time, '%H:%M:%S').time()
        except ValueError:
            return Response(
                {'detail': 'Invalid date/time format'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            staff = CustomUser.objects.get(id=staff_id)
        except CustomUser.DoesNotExist:
            return Response({'detail': 'Staff not found'}, status=status.HTTP_404_NOT_FOUND)

        if str(getattr(staff.restaurant, 'id', '')) != str(getattr(request.user.restaurant, 'id', '')):
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='OTHER',
                entity_type='AssignedShift',
                description='Cross-tenant conflict check denied',
                entity_id=staff_id,
                old_values={},
                new_values={},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        conflicts = SchedulingService.detect_scheduling_conflicts(
            staff_id,
            shift_date_obj,
            start_time_obj,
            end_time_obj
        )
        
        return Response({
            'has_conflicts': len(conflicts) > 0,
            'conflicts': conflicts
        })

    @action(detail=False, methods=['get'])
    def staff_hours(self, request):
        """Get total working hours for staff in date range"""
        staff_id = request.query_params.get('staff_id')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')

        if not all([staff_id, start_date, end_date]):
            return Response(
                {'detail': 'staff_id, start_date, and end_date are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Invalid date format. Use YYYY-MM-DD'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            staff = CustomUser.objects.get(id=staff_id)
        except CustomUser.DoesNotExist:
            return Response({'detail': 'Staff not found'}, status=status.HTTP_404_NOT_FOUND)

        if str(getattr(staff.restaurant, 'id', '')) != str(getattr(request.user.restaurant, 'id', '')):
            AuditLog.create_log(
                restaurant=request.user.restaurant,
                user=request.user,
                action_type='OTHER',
                entity_type='AssignedShift',
                description='Cross-tenant staff hours denied',
                entity_id=staff_id,
                old_values={},
                new_values={},
                ip_address=request.META.get('REMOTE_ADDR'),
                user_agent=request.META.get('HTTP_USER_AGENT', '')
            )
            return Response({'detail': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)

        hours_data = SchedulingService.calculate_staff_hours(staff_id, start_date_obj, end_date_obj)
        return Response(hours_data)
        staff_id = request.query_params.get('staff_id')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        if not all([staff_id, start_date, end_date]):
            return Response(
                {'detail': 'staff_id, start_date, and end_date are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            start_date_obj = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date_obj = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response(
                {'detail': 'Invalid date format. Use YYYY-MM-DD'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        hours_data = SchedulingService.calculate_staff_hours(staff_id, start_date_obj, end_date_obj)
        return Response(hours_data)
    
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Confirm a shift"""
        shift = self.get_object()
        shift.is_confirmed = True
        shift.status = 'CONFIRMED'
        shift.save()
        return Response({'detail': 'Shift confirmed', 'shift': self.get_serializer(shift).data})
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Mark shift as completed"""
        shift = self.get_object()
        shift.status = 'COMPLETED'
        shift.save()
        return Response({'detail': 'Shift marked as completed', 'shift': self.get_serializer(shift).data})
    
    @action(detail=True, methods=['get'])
    def task_templates(self, request, pk=None):
        """Get all task templates assigned to this shift"""
        from .task_templates import TaskTemplate
        from .serializers import TaskTemplateSerializer
        
        shift = self.get_object()
        templates = shift.task_templates.all()
        serializer = TaskTemplateSerializer(templates, many=True)
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated])
    def my_shift_templates(self, request):
        """Get all task templates assigned to current user's current and upcoming shifts"""
        from .task_templates import TaskTemplate
        from .serializers import TaskTemplateSerializer
        
        user = request.user
        today = timezone.now().date()
        
        # Get current and upcoming shifts for this user
        shifts = AssignedShift.objects.filter(
            staff=user,
            shift_date__gte=today
        ).prefetch_related('task_templates')
        
        # Collect all unique templates with shift context
        templates_data = []
        seen_template_ids = set()
        
        # Import ChecklistExecution locally to avoid circular imports
        try:
            from checklists.models import ChecklistExecution
        except ImportError:
            ChecklistExecution = None
        
        for shift in shifts:
            for template in shift.task_templates.all():
                # We allow the same template to appear multiple times if it's on different shifts
                # it should be unique (handled by set logic if needed, but here we iterate shifts)
                # Actually, if we want to show it per shift, we shouldn't dedup by template ID globally, but per shift?
                # The previous logic deduped globally: "if template.id not in seen_template_ids".
                # This means if I have the same template on Monday and Tuesday, I only see it once?
                # That seems wrong if the user wants to see "Assigned Checklists" for all upcoming shifts.
                # But the user said "only show template/s assigned... when they have their shifts scheduled".
                # If I have a shift today and tomorrow with the same template, I should probably see both?
                # Or maybe just the "next" one?
                # For now, I will keep the dedup logic but maybe scope it? 
                # Wait, the previous logic was:
                # if template.id not in seen_template_ids: ... seen_template_ids.add(template.id)
                # This hides the template for the 2nd shift.
                # I should probably remove this dedup if I want to show all assignments.
                # But let's stick to the plan: Link execution.
                
                # Construct a unique key for the view: template_id + shift_id
                unique_key = f"{template.id}_{shift.id}"
                if unique_key in seen_template_ids:
                    continue
                
                template_dict = TaskTemplateSerializer(template).data
                template_dict['shift_id'] = str(shift.id)
                template_dict['shift_date'] = shift.shift_date
                template_dict['shift_role'] = shift.role
                
                # Find associated ChecklistTemplate
                # We use the reverse relation 'checklist_templates'
                checklist_template = template.checklist_templates.filter(is_active=True).first()
                if checklist_template:
                    template_dict['checklist_template_id'] = str(checklist_template.id)
                
                # Check for existing execution
                if ChecklistExecution:
                    execution = ChecklistExecution.objects.filter(
                        template__task_template=template,
                        assigned_shift=shift,
                        assigned_to=user
                    ).first()
                    
                    if execution:
                        template_dict['execution_id'] = str(execution.id)
                        template_dict['execution_status'] = execution.status
                        template_dict['execution_progress'] = execution.progress_percentage
                
                templates_data.append(template_dict)
                seen_template_ids.add(unique_key)
        
        return Response(templates_data)

class ShiftSwapRequestListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ShiftSwapRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return ShiftSwapRequest.objects.filter(
            Q(requester=user) | Q(receiver=user) | Q(receiver__isnull=True, shift_to_swap__schedule__restaurant=user.restaurant)
        ).order_by('-created_at')

    def perform_create(self, serializer):
        shift_to_swap = serializer.validated_data['shift_to_swap']
        if shift_to_swap.staff != self.request.user:
            raise permissions.ValidationError("You can only request to swap your own shifts.")
        serializer.save(requester=self.request.user)

class ShiftSwapRequestRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ShiftSwapRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        user = self.request.user
        # Users can retrieve/update/delete their own requests or requests where they are the receiver
        return ShiftSwapRequest.objects.filter(Q(requester=user) | Q(receiver=user), shift_to_swap__schedule__restaurant=user.restaurant)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'PENDING':
            return Response({'detail': 'Cannot update a non-pending swap request.'}, status=status.HTTP_400_BAD_REQUEST)

        # Only requester can cancel, only receiver or admin can approve/reject
        new_status = request.data.get('status')

        if new_status == 'CANCELLED':
            if instance.requester != request.user:
                return Response({'detail': 'Only the requester can cancel this request.'}, status=status.HTTP_403_FORBIDDEN)
        elif new_status in ['APPROVED', 'REJECTED']:
            if instance.receiver != request.user and not (request.user.role == 'ADMIN' or request.user.role == 'SUPER_ADMIN'):
                return Response({'detail': 'Only the receiver or an admin can approve/reject this request.'}, status=status.HTTP_403_FORBIDDEN)

            if new_status == 'APPROVED':
                # Logic to swap shifts:
                # 1. Update original shift to be assigned to the receiver
                original_shift = instance.shift_to_swap
                original_shift.staff = instance.receiver
                original_shift.save()

                # 2. If receiver also offered a shift, update that one too
                # This is a simplified model assuming a direct swap, more complex logic for 'open' requests needed
                # For now, if receiver is set, we assume they take the shift.

                # 3. Mark all other pending requests for this shift as CANCELLED
                ShiftSwapRequest.objects.filter(
                    shift_to_swap=original_shift,
                    status='PENDING'
                ).exclude(pk=instance.pk).update(status='CANCELLED')

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(serializer.data)


class TaskCategoryViewSet(viewsets.ModelViewSet):
    serializer_class = TaskCategorySerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        return TaskCategory.objects.filter(restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)


class ShiftTaskViewSet(viewsets.ModelViewSet):
    serializer_class = ShiftTaskSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        queryset = ShiftTask.objects.filter(shift__schedule__restaurant=user.restaurant)
        
        # Filter by assigned_to if query param is provided
        assigned_to = self.request.query_params.get('assigned_to')
        if assigned_to:
            queryset = queryset.filter(assigned_to__id=assigned_to)
        
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
        
        # Filter by shift
        shift_id = self.request.query_params.get('shift_id')
        if shift_id:
            queryset = queryset.filter(shift__id=shift_id)
        
        return queryset.order_by('-priority', 'created_at')

    def perform_create(self, serializer):
        # Log incoming payload essentials for observability
        try:
            payload = {
                'title': self.request.data.get('title'),
                'shift': self.request.data.get('shift'),
                'assigned_to': self.request.data.get('assigned_to'),
                'priority': self.request.data.get('priority'),
                'category': self.request.data.get('category'),
            }
            # logger.info("Creating ShiftTask payload=%s user=%s", payload, self.request.user.id)
        except Exception:
            # Avoid blocking creation on logging issues
            pass

        instance = serializer.save(created_by=self.request.user)

        try:
            # logger.info(
            #     "ShiftTask created id=%s shift=%s assigned_to=%s priority=%s",
            #     getattr(instance, 'id', None),
            #     getattr(instance, 'shift_id', None),
            #     getattr(getattr(instance, 'assigned_to', None), 'id', None),
            #     getattr(instance, 'priority', None),
            # )
            pass
        except Exception:
            pass

    @action(detail=True, methods=['post'])
    def mark_completed(self, request, pk=None):
        """Mark a task as completed"""
        task = self.get_object()
        task.mark_completed()
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        """Start a task (change status to IN_PROGRESS)"""
        task = self.get_object()
        task.status = 'IN_PROGRESS'
        task.save()
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def reassign(self, request, pk=None):
        """Reassign a task to another staff member"""
        task = self.get_object()
        assigned_to_id = request.data.get('assigned_to')
        
        if not assigned_to_id:
            return Response(
                {'detail': 'assigned_to field is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        from accounts.models import CustomUser
        try:
            user = CustomUser.objects.get(
                id=assigned_to_id,
                restaurant=self.request.user.restaurant
            )
            task.assigned_to = user
            task.save()
            serializer = self.get_serializer(task)
            return Response(serializer.data)
        except CustomUser.DoesNotExist:
            return Response(
                {'detail': 'User not found in your restaurant'},
                status=status.HTTP_404_NOT_FOUND
            )

    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, IsManagerOrAdmin])
    def intelligent_assign(self, request, pk=None):
        """Intelligently assign a task using the task assignment service"""
        task = self.get_object()
        
        try:
            assignment_service = TaskAssignmentService(request.user.restaurant)
            assignment = assignment_service.assign_task(task)
            
            if assignment:
                task.assigned_to = assignment['staff']
                task.save()
                
                return Response({
                    'task': self.get_serializer(task).data,
                    'assignment_reason': assignment['reason'],
                    'score': assignment['score']
                })
            else:
                return Response(
                    {'detail': 'No suitable staff member found for this task'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        except Exception as e:
            return Response(
                {'detail': f'Assignment failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['post'], permission_classes=[permissions.IsAuthenticated, IsManagerOrAdmin])
    def bulk_assign(self, request):
        """Assign multiple tasks intelligently"""
        task_ids = request.data.get('task_ids', [])
        
        if not task_ids:
            return Response(
                {'detail': 'task_ids field is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            tasks = ShiftTask.objects.filter(
                id__in=task_ids,
                shift__schedule__restaurant=request.user.restaurant
            )
            
            assignment_service = TaskAssignmentService(request.user.restaurant)
            assignments = assignment_service.assign_multiple_tasks(list(tasks))
            
            # Apply assignments
            for assignment in assignments:
                task = assignment['task']
                task.assigned_to = assignment['staff']
                task.save()
            
            return Response({
                'assignments': [
                    {
                        'task_id': assignment['task'].id,
                        'staff_id': assignment['staff'].id,
                        'staff_name': f"{assignment['staff'].first_name} {assignment['staff'].last_name}",
                        'reason': assignment['reason'],
                        'score': assignment['score']
                    }
                    for assignment in assignments
                ]
            })
        except Exception as e:
            return Response(
                {'detail': f'Bulk assignment failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=True, methods=['get'], permission_classes=[permissions.IsAuthenticated, IsManagerOrAdmin])
    def assignment_recommendations(self, request, pk=None):
        """Get assignment recommendations for a task"""
        task = self.get_object()
        
        try:
            assignment_service = TaskAssignmentService(request.user.restaurant)
            recommendations = assignment_service.get_assignment_recommendations(task)
            
            return Response({
                'recommendations': [
                    {
                        'staff_id': rec['staff'].id,
                        'staff_name': f"{rec['staff'].first_name} {rec['staff'].last_name}",
                        'score': rec['score'],
                        'reason': rec['reason']
                    }
                    for rec in recommendations
                ]
            })
        except Exception as e:
            return Response(
                {'detail': f'Failed to get recommendations: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'], permission_classes=[permissions.IsAuthenticated, IsManagerOrAdmin])
    def workload_analysis(self, request):
        """Get workload analysis for all staff"""
        try:
            assignment_service = TaskAssignmentService(request.user.restaurant)
            analysis = assignment_service.analyze_staff_workload()
            
            return Response({
                'workload_analysis': [
                    {
                        'staff_id': item['staff'].id,
                        'staff_name': f"{item['staff'].first_name} {item['staff'].last_name}",
                        'total_tasks': item['total_tasks'],
                        'high_priority_tasks': item['high_priority_tasks'],
                        'workload_score': item['workload_score'],
                        'status': item['status']
                    }
                    for item in analysis
                ]
            })
        except Exception as e:
            return Response(
                {'detail': f'Workload analysis failed: {str(e)}'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @action(detail=False, methods=['get'])
    def my_tasks(self, request):
        """Get tasks assigned to the current user"""
        try:
            # logger.info(
            #     "Fetching my_tasks for user=%s restaurant=%s",
            #     request.user.id,
            #     getattr(request.user, 'restaurant_id', None)
            # )
            pass
        except Exception:
            pass
        tasks = ShiftTask.objects.filter(
            assigned_to=request.user,
            shift__schedule__restaurant=request.user.restaurant
        ).order_by('-priority', 'created_at')
        try:
            logger.info("my_tasks count=%s for user=%s", tasks.count(), request.user.id)
        except Exception:
            pass
        
        serializer = self.get_serializer(tasks, many=True)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def update_progress(self, request, pk=None):
        """Update task progress with notes and location"""
        task = self.get_object()
        
        # Check if user is assigned to this task
        if task.assigned_to != request.user:
            return Response(
                {'detail': 'You can only update progress for tasks assigned to you'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        progress_percentage = request.data.get('progress_percentage')
        progress_notes = request.data.get('progress_notes', '')
        completion_location = request.data.get('completion_location', '')
        
        if progress_percentage is not None:
            try:
                progress_percentage = int(progress_percentage)
                if 0 <= progress_percentage <= 100:
                    task.progress_percentage = progress_percentage
                    task.progress_notes = progress_notes
                    task.completion_location = completion_location
                    
                    # Update status based on progress
                    if progress_percentage == 0:
                        task.status = 'TODO'
                    elif progress_percentage == 100:
                        task.status = 'COMPLETED'
                        task.completed_at = timezone.now()
                    else:
                        task.status = 'IN_PROGRESS'
                    
                    task.save()
                    serializer = self.get_serializer(task)
                    return Response(serializer.data)
                else:
                    return Response(
                        {'detail': 'Progress percentage must be between 0 and 100'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
            except (ValueError, TypeError):
                return Response(
                    {'detail': 'Progress percentage must be a valid integer'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        return Response(
            {'detail': 'progress_percentage is required'},
            status=status.HTTP_400_BAD_REQUEST
        )

    @action(detail=True, methods=['post'])
    def add_checkpoint(self, request, pk=None):
        """Add a progress checkpoint with optional photo"""
        task = self.get_object()
        
        # Check if user is assigned to this task
        if task.assigned_to != request.user:
            return Response(
                {'detail': 'You can only add checkpoints for tasks assigned to you'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        description = request.data.get('description', '')
        location = request.data.get('location', '')
        progress_percentage = request.data.get('progress_percentage', task.progress_percentage)
        photo = request.FILES.get('photo')
        
        if not description.strip():
            return Response(
                {'detail': 'Description is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create checkpoint data
        checkpoint = {
            'id': f"checkpoint_{len(task.checkpoints)}_{timezone.now().timestamp()}",
            'timestamp': timezone.now().isoformat(),
            'description': description,
            'location': location,
            'progress_percentage': int(progress_percentage)
        }
        
        # Handle photo upload if provided
        if photo:
            # Save photo and add URL to checkpoint
            # For now, we'll just indicate that a photo was uploaded
            checkpoint['photo'] = f"checkpoint_photo_{checkpoint['id']}.jpg"
        
        # Add checkpoint to task
        if not isinstance(task.checkpoints, list):
            task.checkpoints = []
        
        task.checkpoints.append(checkpoint)
        task.progress_percentage = int(progress_percentage)
        
        # Update status based on progress
        if task.progress_percentage == 0:
            task.status = 'TODO'
        elif task.progress_percentage == 100:
            task.status = 'COMPLETED'
            task.completed_at = timezone.now()
        else:
            task.status = 'IN_PROGRESS'
        
        task.save()
        serializer = self.get_serializer(task)
        return Response(serializer.data)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Complete task with optional photo and location"""
        task = self.get_object()
        
        # Check if user is assigned to this task
        if task.assigned_to != request.user:
            return Response(
                {'detail': 'You can only complete tasks assigned to you'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        completion_photo = request.FILES.get('completion_photo')
        completion_location = request.data.get('completion_location', '')
        progress_notes = request.data.get('progress_notes', task.progress_notes or '')
        
        # Mark task as completed
        task.status = 'COMPLETED'
        task.progress_percentage = 100
        task.progress_notes = progress_notes
        task.completion_location = completion_location
        task.completed_at = timezone.now()
        
        # Handle completion photo
        if completion_photo:
            task.completion_photo = completion_photo
        
        task.save()
        serializer = self.get_serializer(task)
        return Response(serializer.data)


# Timesheet ViewSets
class TimesheetViewSet(viewsets.ModelViewSet):
    """ViewSet for managing timesheets"""
    serializer_class = TimesheetSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        # Staff can see only their own timesheets
        if user.role == 'ADMIN' or user.role == 'SUPER_ADMIN' or user.role == 'MANAGER':
            # Admins/managers can see all timesheets for their restaurant
            return Timesheet.objects.filter(restaurant=user.restaurant).order_by('-end_date')
        else:
            # Staff can only see their own timesheets
            return Timesheet.objects.filter(staff=user).order_by('-end_date')
    
    def perform_create(self, serializer):
        # Managers/admins create timesheets for staff
        serializer.save(restaurant=self.request.user.restaurant)
    
    @action(detail=True, methods=['post'])
    def submit(self, request, pk=None):
        """Submit a timesheet for approval"""
        timesheet = self.get_object()
        if timesheet.status != 'DRAFT':
            return Response(
                {'detail': 'Only draft timesheets can be submitted'},
                status=status.HTTP_400_BAD_REQUEST
            )
        timesheet.status = 'SUBMITTED'
        timesheet.submitted_at = timezone.now()
        timesheet.save()
        return Response({'detail': 'Timesheet submitted successfully', 'timesheet': self.get_serializer(timesheet).data})
    
    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        """Approve a submitted timesheet"""
        if not (request.user.role == 'ADMIN' or request.user.role == 'SUPER_ADMIN'):
            return Response(
                {'detail': 'Only admins can approve timesheets'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        timesheet = self.get_object()
        if timesheet.status != 'SUBMITTED':
            return Response(
                {'detail': 'Only submitted timesheets can be approved'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        timesheet.status = 'APPROVED'
        timesheet.approved_at = timezone.now()
        timesheet.approved_by = request.user
        timesheet.save()
        return Response({'detail': 'Timesheet approved successfully', 'timesheet': self.get_serializer(timesheet).data})
    
    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        """Reject a submitted timesheet"""
        if not (request.user.role == 'ADMIN' or request.user.role == 'SUPER_ADMIN'):
            return Response(
                {'detail': 'Only admins can reject timesheets'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        timesheet = self.get_object()
        if timesheet.status != 'SUBMITTED':
            return Response(
                {'detail': 'Only submitted timesheets can be rejected'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        timesheet.status = 'REJECTED'
        timesheet.save()
        return Response({'detail': 'Timesheet rejected', 'timesheet': self.get_serializer(timesheet).data})
    
    @action(detail=True, methods=['post'])
    def mark_paid(self, request, pk=None):
        """Mark timesheet as paid"""
        if not (request.user.role == 'ADMIN' or request.user.role == 'SUPER_ADMIN'):
            return Response(
                {'detail': 'Only admins can mark timesheets as paid'},
                status=status.HTTP_403_FORBIDDEN
            )
        
        timesheet = self.get_object()
        if timesheet.status != 'APPROVED':
            return Response(
                {'detail': 'Only approved timesheets can be marked as paid'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        timesheet.status = 'PAID'
        timesheet.paid_at = timezone.now()
        timesheet.save()
        return Response({'detail': 'Timesheet marked as paid', 'timesheet': self.get_serializer(timesheet).data})
    
    @action(detail=True, methods=['post'])
    def recalculate(self, request, pk=None):
        """Recalculate timesheet totals from shifts"""
        timesheet = self.get_object()
        timesheet.calculate_totals()
        return Response({'detail': 'Timesheet recalculated', 'timesheet': self.get_serializer(timesheet).data})

    @action(detail=False, methods=['get'], url_path='export-payroll')
    def export_payroll(self, request):
        """Export timesheets for pay period as CSV (for payroll systems)."""
        from django.http import HttpResponse
        if not (request.user.role in ('ADMIN', 'SUPER_ADMIN', 'MANAGER')):
            return Response({'detail': 'Only managers can export payroll'}, status=status.HTTP_403_FORBIDDEN)
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        if not start_date or not end_date:
            return Response(
                {'detail': 'start_date and end_date required (YYYY-MM-DD)'},
                status=status.HTTP_400_BAD_REQUEST
            )
        try:
            start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
            end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
        except ValueError:
            return Response({'detail': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
        qs = Timesheet.objects.filter(
            restaurant=request.user.restaurant,
            start_date__lte=end_date,
            end_date__gte=start_date
        ).select_related('staff').order_by('staff__last_name', 'start_date')
        rows = []
        rows.append(['Staff ID', 'Email', 'First Name', 'Last Name', 'Start Date', 'End Date', 'Total Hours', 'Hourly Rate', 'Total Earnings', 'Status'])
        for ts in qs:
            staff = ts.staff
            email = staff.email if staff else ''
            first = getattr(staff, 'first_name', '') or '' if staff else ''
            last = getattr(staff, 'last_name', '') or '' if staff else ''
            rows.append([
                str(ts.staff_id) if ts.staff_id else '',
                email,
                first,
                last,
                ts.start_date.isoformat(),
                ts.end_date.isoformat(),
                str(ts.total_hours),
                str(ts.hourly_rate),
                str(ts.total_earnings),
                ts.status or '',
            ])
        import csv
        from io import StringIO
        buf = StringIO()
        w = csv.writer(buf)
        w.writerows(rows)
        resp = HttpResponse(buf.getvalue(), content_type='text/csv')
        resp['Content-Disposition'] = f'attachment; filename="payroll_export_{start_date}_{end_date}.csv"'
        return resp


class TimesheetEntryViewSet(viewsets.ModelViewSet):
    """ViewSet for managing timesheet entries"""
    serializer_class = TimesheetEntrySerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    
    def get_queryset(self):
        timesheet_id = self.request.query_params.get('timesheet_id')
        if timesheet_id:
            return TimesheetEntry.objects.filter(
                timesheet__id=timesheet_id,
                timesheet__restaurant=self.request.user.restaurant
            )
        return TimesheetEntry.objects.filter(timesheet__restaurant=self.request.user.restaurant)
    
    def perform_create(self, serializer):
        serializer.save()
