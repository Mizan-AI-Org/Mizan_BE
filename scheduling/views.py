from rest_framework import generics, permissions, status, viewsets
from rest_framework.response import Response
from rest_framework.decorators import action
from django.utils import timezone
from django.db.models import Q
from datetime import datetime, timedelta

from .models import (
    ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift, 
    ShiftSwapRequest, TaskCategory, ShiftTask
)
from .serializers import (
    ScheduleTemplateSerializer,
    TemplateShiftSerializer,
    WeeklyScheduleSerializer,
    AssignedShiftSerializer,
    ShiftSwapRequestSerializer,
    TaskCategorySerializer,
    ShiftTaskSerializer,
)
from .services import SchedulingService
from accounts.views import IsAdmin, IsSuperAdmin, IsManagerOrAdmin


class ScheduleTemplateListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant).order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class ScheduleTemplateRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant)

class TemplateShiftListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TemplateShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        template_id = self.kwargs.get('template_pk')
        return TemplateShift.objects.filter(template__id=template_id, template__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        template_id = self.kwargs.get('template_pk')
        template = ScheduleTemplate.objects.get(id=template_id, restaurant=self.request.user.restaurant)
        serializer.save(template=template)

class TemplateShiftRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TemplateShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        template_id = self.kwargs.get('template_pk')
        return TemplateShift.objects.filter(template__id=template_id, template__restaurant=self.request.user.restaurant)

class WeeklyScheduleListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant).order_by('-week_start')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class WeeklyScheduleRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant)


class WeeklyScheduleViewSet(viewsets.ModelViewSet):
    """ViewSet for weekly schedules with analytics endpoints"""
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    
    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant)
    
    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)
    
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
        schedule = self.get_object()
        schedule.is_published = True
        schedule.save()
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
            return Response({'detail': message}, status=status.HTTP_400_BAD_REQUEST)

class AssignedShiftListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        schedule_id = self.kwargs.get('schedule_pk')
        return AssignedShift.objects.filter(schedule__id=schedule_id, schedule__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        schedule_id = self.kwargs.get('schedule_pk')
        schedule = WeeklySchedule.objects.get(id=schedule_id, restaurant=self.request.user.restaurant)
        serializer.save(schedule=schedule, restaurant=self.request.user.restaurant)

class AssignedShiftRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        schedule_id = self.kwargs.get('schedule_pk')
        return AssignedShift.objects.filter(schedule__id=schedule_id, schedule__restaurant=self.request.user.restaurant)


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
        
        return queryset.order_by('shift_date', 'start_time')
    
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
        serializer.save(created_by=self.request.user)

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