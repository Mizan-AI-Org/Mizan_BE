from rest_framework import viewsets, status, filters
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser
from rest_framework.response import Response
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from .permissions import IsManagerOrReadOnly
from django.db import transaction
from django.utils import timezone
from datetime import datetime, timedelta
import logging
from django.db.models import Q
from accounts.models import CustomUser
from typing import Optional, Dict

from .models import (
    Schedule, StaffProfile, StaffDocument, ScheduleChange, 
    ScheduleNotification, StaffAvailability, PerformanceMetric,
    StaffRequest, StaffRequestComment
)
from .models_task import (
    StandardOperatingProcedure, SafetyChecklist, ScheduleTask,
    SafetyConcernReport, SafetyRecognition
)
from .serializers import (
    ScheduleSerializer, StaffProfileSerializer, StaffDocumentSerializer, ScheduleChangeSerializer,
    ScheduleNotificationSerializer, StaffAvailabilitySerializer, PerformanceMetricSerializer,
    StandardOperatingProcedureSerializer, SafetyChecklistSerializer, ScheduleTaskSerializer,
    SafetyConcernReportSerializer, SafetyRecognitionSerializer,
    StaffRequestSerializer, StaffRequestCommentSerializer
)

logger = logging.getLogger(__name__)


def _is_manager(user: CustomUser) -> bool:
    return bool(getattr(user, 'role', None) in ['SUPER_ADMIN', 'ADMIN', 'MANAGER', 'OWNER'])


class StaffRequestViewSet(viewsets.ModelViewSet):
    """
    Manager-first inbox for staff requests.
    - Managers/Admins see all requests for their restaurant
    - Staff can only see their own requests (if any are created in-app later)
    """
    serializer_class = StaffRequestSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['subject', 'description', 'staff_name', 'staff_phone', 'external_id']
    ordering_fields = ['created_at', 'updated_at', 'priority', 'status']
    ordering = ['-created_at']

    def get_queryset(self):
        user = self.request.user
        qs = StaffRequest.objects.all()
        if getattr(user, 'restaurant', None):
            qs = qs.filter(restaurant=user.restaurant)
        status_q = self.request.query_params.get('status')
        if status_q:
            qs = qs.filter(status=str(status_q).upper())
        if not _is_manager(user):
            # Staff view: only their own
            qs = qs.filter(Q(staff=user) | Q(staff_phone__icontains=''.join(filter(str.isdigit, str(getattr(user, 'phone', '') or '')))))
        return qs

    def perform_create(self, serializer):
        """
        In-app creation (optional). Staff request creation should typically come from agent ingestion.
        """
        user = self.request.user
        staff_name = f"{user.first_name} {user.last_name}".strip()
        staff_phone = getattr(user, 'phone', '') or ''
        serializer.save(
            restaurant=user.restaurant,
            staff=user,
            staff_name=staff_name,
            staff_phone=staff_phone,
            source='web',
            status='PENDING',
        )

    def _add_comment(self, req: StaffRequest, author: CustomUser, body: str, kind: str = 'comment', metadata: Optional[Dict] = None):
        StaffRequestComment.objects.create(
            request=req,
            author=author,
            kind=kind,
            body=body or '',
            metadata=metadata or {},
        )

    def _notify_managers(self, req: StaffRequest):
        """
        Send real-time in-app notifications to managers/admins for this restaurant.
        """
        try:
            from notifications.models import Notification
            from notifications.services import notification_service

            managers = CustomUser.objects.filter(
                restaurant=req.restaurant,
                role__in=['MANAGER', 'ADMIN', 'SUPER_ADMIN', 'OWNER'],
                is_active=True,
            )
            for m in managers:
                notif = Notification.objects.create(
                    recipient=m,
                    title="New Staff Request",
                    message=(req.subject or "Staff request") + (f" — {req.staff_name}" if req.staff_name else ""),
                    notification_type='STAFF_REQUEST',
                    priority=req.priority,
                    data={
                        'staff_request_id': str(req.id),
                        'route': f"/dashboard/staff-requests/{req.id}",
                        'status': req.status,
                        'category': req.category,
                    }
                )
                notification_service.send_custom_notification(
                    recipient=m,
                    notification=notif,
                    message=notif.message,
                    notification_type='STAFF_REQUEST',
                    title=notif.title,
                    channels=['app'],
                )
        except Exception:
            # Best-effort: do not fail request creation due to notification issues
            pass

    @action(detail=True, methods=['post'])
    def comment(self, request, pk=None):
        req = self.get_object()
        body = str(request.data.get('body') or '').strip()
        if not body:
            return Response({'success': False, 'error': 'body is required'}, status=status.HTTP_400_BAD_REQUEST)
        self._add_comment(req, request.user, body, kind='comment')
        return Response({'success': True, 'request': self.get_serializer(req).data})

    @action(detail=True, methods=['post'])
    def approve(self, request, pk=None):
        req = self.get_object()
        if not _is_manager(request.user):
            return Response({'success': False, 'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        old = req.status
        req.status = 'APPROVED'
        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        self._add_comment(req, request.user, f"Approved by {request.user.get_full_name()}", kind='status_change', metadata={'from': old, 'to': 'APPROVED'})
        return Response({'success': True, 'request': self.get_serializer(req).data})

    @action(detail=True, methods=['post'])
    def reject(self, request, pk=None):
        req = self.get_object()
        if not _is_manager(request.user):
            return Response({'success': False, 'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        reason = str(request.data.get('reason') or '').strip()
        old = req.status
        req.status = 'REJECTED'
        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        self._add_comment(
            req,
            request.user,
            f"Rejected by {request.user.get_full_name()}" + (f": {reason}" if reason else ""),
            kind='status_change',
            metadata={'from': old, 'to': 'REJECTED', 'reason': reason},
        )
        return Response({'success': True, 'request': self.get_serializer(req).data})

    @action(detail=True, methods=['post'])
    def escalate(self, request, pk=None):
        req = self.get_object()
        if not _is_manager(request.user):
            return Response({'success': False, 'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        note = str(request.data.get('note') or '').strip()
        old = req.status
        req.status = 'ESCALATED'
        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        self._add_comment(
            req,
            request.user,
            f"Escalated by {request.user.get_full_name()}" + (f": {note}" if note else ""),
            kind='status_change',
            metadata={'from': old, 'to': 'ESCALATED', 'note': note},
        )
        return Response({'success': True, 'request': self.get_serializer(req).data})

    @action(detail=True, methods=['post'])
    def close(self, request, pk=None):
        req = self.get_object()
        if not _is_manager(request.user):
            return Response({'success': False, 'error': 'Forbidden'}, status=status.HTTP_403_FORBIDDEN)
        old = req.status
        req.status = 'CLOSED'
        req.reviewed_by = request.user
        req.reviewed_at = timezone.now()
        req.save(update_fields=['status', 'reviewed_by', 'reviewed_at', 'updated_at'])
        self._add_comment(req, request.user, f"Closed by {request.user.get_full_name()}", kind='status_change', metadata={'from': old, 'to': 'CLOSED'})
        return Response({'success': True, 'request': self.get_serializer(req).data})

# Task Management ViewSets
class StandardOperatingProcedureViewSet(viewsets.ModelViewSet):
    """API endpoint for Standard Operating Procedures (SOPs)
    Admins/Managers can create/update/delete; staff have read-only access to relevant SOPs
    """
    queryset = StandardOperatingProcedure.objects.all()
    serializer_class = StandardOperatingProcedureSerializer
    permission_classes = [IsAuthenticated, IsManagerOrReadOnly]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'safety_level']
    ordering_fields = ['title', 'safety_level', 'created_at']
    
    def get_queryset(self):
        """Filter SOPs based on user permissions
        - Admins/Managers: all SOPs for their restaurant
        - Staff: only SOPs linked to their scheduled tasks
        """
        user = self.request.user
        queryset = StandardOperatingProcedure.objects.all()

        # Filter by restaurant
        if getattr(user, 'restaurant', None):
            queryset = queryset.filter(restaurant=user.restaurant)

        # Staff can only view SOPs relevant to their schedules/tasks
        if user.role not in ['SUPER_ADMIN', 'ADMIN', 'MANAGER']:
            sop_ids = ScheduleTask.objects.filter(
                schedule__staff=user
            ).values_list('sop_id', flat=True)
            queryset = queryset.filter(id__in=sop_ids)

        # Optional safety level filter
        safety_level = self.request.query_params.get('safety_level')
        if safety_level:
            queryset = queryset.filter(safety_level=safety_level)

        return queryset
    
    def perform_create(self, serializer):
        """Create a new SOP with the current user's restaurant"""
        serializer.save(restaurant=self.request.user.restaurant)

class SafetyChecklistViewSet(viewsets.ModelViewSet):
    """API endpoint for Safety Checklists"""
    queryset = SafetyChecklist.objects.all()
    serializer_class = SafetyChecklistSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description']
    ordering_fields = ['title', 'frequency', 'created_at']
    
    def get_queryset(self):
        """Filter checklists based on user permissions"""
        user = self.request.user
        queryset = SafetyChecklist.objects.all()
        
        # Filter by restaurant
        if user.restaurant:
            queryset = queryset.filter(restaurant=user.restaurant)
            
        # Filter by frequency if specified
        frequency = self.request.query_params.get('frequency')
        if frequency:
            queryset = queryset.filter(frequency=frequency)
            
        return queryset
    
    def perform_create(self, serializer):
        """Create a new checklist with the current user's restaurant"""
        serializer.save(restaurant=self.request.user.restaurant)

class ScheduleTaskViewSet(viewsets.ModelViewSet):
    """API endpoint for Schedule Tasks"""
    queryset = ScheduleTask.objects.all()
    serializer_class = ScheduleTaskSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'assigned_to__username']
    ordering_fields = ['priority', 'status', 'due_time', 'created_at']
    
    def get_queryset(self):
        """Filter tasks based on user permissions and query parameters"""
        user = self.request.user
        queryset = ScheduleTask.objects.all()
        
        # Filter by restaurant
        if user.restaurant:
            queryset = queryset.filter(schedule__restaurant=user.restaurant)
        
        # Regular staff can only see their assigned tasks
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            queryset = queryset.filter(assigned_to=user)
        
        # Filter by schedule
        schedule_id = self.request.query_params.get('schedule_id')
        if schedule_id:
            queryset = queryset.filter(schedule_id=schedule_id)
            
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
            
        # Filter by priority
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
            
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                queryset = queryset.filter(due_time__gte=start_date)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                end_date = end_date + timedelta(days=1)
                queryset = queryset.filter(due_time__lt=end_date)
            except ValueError:
                pass
                
        return queryset
    
    @action(detail=True, methods=['post'])
    def complete_task(self, request, pk=None):
        """Mark a task as completed"""
        task = self.get_object()
        
        # Check if user is assigned to this task or is admin
        user = request.user
        if task.assigned_to != user and user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {"error": "You are not authorized to complete this task"},
                status=status.HTTP_403_FORBIDDEN
            )
            
        # Update task status and completion details
        task.status = 'COMPLETED'
        task.completion_time = timezone.now()
        task.completion_notes = request.data.get('completion_notes', '')
        task.save()
        
        return Response({
            "message": "Task marked as completed",
            "task_id": task.id,
            "completion_time": task.completion_time
        })
        
    @action(detail=True, methods=['post'])
    def reassign_task(self, request, pk=None):
        """Reassign a task to another staff member"""
        task = self.get_object()
        new_assignee_id = request.data.get('assigned_to')
        
        if not new_assignee_id:
            return Response(
                {"error": "New assignee ID is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Only admins can reassign tasks
        user = request.user
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {"error": "Only administrators can reassign tasks"},
                status=status.HTTP_403_FORBIDDEN
            )
            
        try:
            from accounts.models import CustomUser
            new_assignee = CustomUser.objects.get(id=new_assignee_id)
            
            # Ensure new assignee is in the same restaurant
            if new_assignee.restaurant != user.restaurant:
                return Response(
                    {"error": "Cannot assign task to staff from another restaurant"},
                    status=status.HTTP_400_BAD_REQUEST
                )
                
            task.assigned_to = new_assignee
            task.save()
            
            return Response({
                "message": "Task reassigned successfully",
                "task_id": task.id,
                "new_assignee": new_assignee.username
            })
        except Exception as e:
            return Response(
                {"error": f"Failed to reassign task: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

class SafetyConcernReportViewSet(viewsets.ModelViewSet):
    """API endpoint for Safety Concern Reports"""
    queryset = SafetyConcernReport.objects.all()
    serializer_class = SafetyConcernReportSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'location']
    ordering_fields = ['severity', 'status', 'created_at']
    
    def get_queryset(self):
        """Filter reports based on user permissions"""
        user = self.request.user
        queryset = SafetyConcernReport.objects.all()
        
        # Filter by restaurant
        if user.restaurant:
            queryset = queryset.filter(restaurant=user.restaurant)
        
        # Regular staff can only see their own reports unless anonymous
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            queryset = queryset.filter(Q(reporter=user) | Q(is_anonymous=True))
            
        # Filter by status
        status_param = self.request.query_params.get('status')
        if status_param:
            queryset = queryset.filter(status=status_param)
            
        # Filter by severity
        severity = self.request.query_params.get('severity')
        if severity:
            queryset = queryset.filter(severity=severity)
            
        return queryset
    
    def perform_create(self, serializer):
        """Create a new safety concern report"""
        user = self.request.user
        from accounts.models import Restaurant
        from rest_framework import serializers as drf_serializers
        restaurant = getattr(user, 'restaurant', None) or Restaurant.objects.first()
        if not restaurant:
            raise drf_serializers.ValidationError("Restaurant not found for current user")
        if getattr(user, 'restaurant', None) is None and restaurant:
            user.restaurant = restaurant
            try:
                user.save(update_fields=['restaurant'])
            except Exception:
                pass

        sev = (serializer.validated_data.get('severity') or 'MEDIUM').upper()
        if sev == 'INVESTIGATING':
            sev = 'MEDIUM'

        stat = (serializer.validated_data.get('status') or 'REPORTED').upper()
        if stat == 'INVESTIGATING':
            stat = 'UNDER_REVIEW'

        serializer.save(restaurant=restaurant, severity=sev, status=stat)
    
    @action(detail=True, methods=['post'])
    def update_status(self, request, pk=None):
        """Update the status of a safety concern report"""
        report = self.get_object()
        new_status = request.data.get('status')
        
        # Only admins can update status
        user = request.user
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {"error": "Only administrators can update report status"},
                status=status.HTTP_403_FORBIDDEN
            )
            
        if not new_status:
            return Response(
                {"error": "New status is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        report.status = new_status
        report.resolution_notes = request.data.get('resolution_notes', report.resolution_notes)
        report.save()
        
        return Response({
            "message": "Report status updated successfully",
            "report_id": report.id,
            "new_status": report.status
        })

class SafetyRecognitionViewSet(viewsets.ModelViewSet):
    """API endpoint for Safety Recognition"""
    queryset = SafetyRecognition.objects.all()
    serializer_class = SafetyRecognitionSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['description', 'staff__username']
    ordering_fields = ['points', 'created_at']
    
    def get_queryset(self):
        """Filter recognitions based on user permissions"""
        user = self.request.user
        queryset = SafetyRecognition.objects.all()
        
        # Filter by restaurant
        if user.restaurant:
            queryset = queryset.filter(restaurant=user.restaurant)
        
        # Regular staff can only see their own recognitions
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            queryset = queryset.filter(staff=user)
            
        return queryset
    
    def perform_create(self, serializer):
        """Create a new safety recognition"""
        # Set restaurant and recognized_by from the current user
        serializer.save(
            restaurant=self.request.user.restaurant,
            recognized_by=self.request.user
        )
        
    @action(detail=False, methods=['get'])
    def leaderboard(self, request):
        """Get safety recognition leaderboard"""
        user = request.user
        
        # Get recognitions for the current restaurant
        if user.restaurant:
            from django.db.models import Sum
            
            # Get total points by staff member
            leaderboard = SafetyRecognition.objects.filter(
                restaurant=user.restaurant
            ).values(
                'staff__id', 
                'staff__username', 
                'staff__first_name', 
                'staff__last_name'
            ).annotate(
                total_points=Sum('points')
            ).order_by('-total_points')[:10]  # Top 10
            
            return Response(leaderboard)
        else:
            return Response(
                {"error": "Restaurant not found"},
                status=status.HTTP_400_BAD_REQUEST
            )
class StaffProfileViewSet(viewsets.ModelViewSet):
    """
    API endpoint for staff profiles
    """
    queryset = StaffProfile.objects.all()
    serializer_class = StaffProfileSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter profiles based on user permissions"""
        user = self.request.user
        if user.role in ['SUPER_ADMIN', 'ADMIN']:
            # Admins can see all profiles in their restaurant
            return StaffProfile.objects.filter(user__restaurant=user.restaurant)
        else:
            # Regular staff can only see their own profile
            return StaffProfile.objects.filter(user=user)
    
    def perform_create(self, serializer):
        """Create a new staff profile"""
        serializer.save(user=self.request.user)

class StaffDocumentViewSet(viewsets.ModelViewSet):
    """
    API endpoint for staff documents
    """
    queryset = StaffDocument.objects.all()
    serializer_class = StaffDocumentSerializer
    permission_classes = [IsAuthenticated]
    parser_classes = [MultiPartParser, FormParser, JSONParser]

    def get_queryset(self):
        user = self.request.user
        queryset = StaffDocument.objects.all()
        
        # Filter by staff_id if provided (for admins viewing others)
        staff_id = self.request.query_params.get('staff_id')
        if staff_id:
            queryset = queryset.filter(staff_id=staff_id)
        
        # Permission check
        if user.role not in ['SUPER_ADMIN', 'ADMIN', 'MANAGER']:
            # Regular staff can only see their own documents
            queryset = queryset.filter(staff=user)
        
        return queryset

    def perform_create(self, serializer):
        staff_id = self.request.data.get('staff')
        if staff_id:
             from accounts.models import CustomUser
             staff = CustomUser.objects.get(id=staff_id)
             serializer.save(staff=staff)
        else:
             serializer.save(staff=self.request.user)

class ScheduleViewSet(viewsets.ModelViewSet):
    """
    API endpoint for staff schedules with enhanced reliability
    """
    queryset = Schedule.objects.all()
    serializer_class = ScheduleSerializer
    permission_classes = [IsAuthenticated]
    filter_backends = [filters.SearchFilter, filters.OrderingFilter]
    search_fields = ['title', 'description', 'staff__username', 'staff__email']
    ordering_fields = ['start_time', 'end_time', 'created_at', 'status']
    
    def get_queryset(self):
        """
        Filter schedules based on query parameters and user permissions
        """
        user = self.request.user
        queryset = Schedule.objects.all()
        
        # Base filter by restaurant
        if user.restaurant:
            queryset = queryset.filter(restaurant=user.restaurant)
        
        # Filter by staff member if specified
        staff_id = self.request.query_params.get('staff_id')
        if staff_id:
            queryset = queryset.filter(staff_id=staff_id)
        elif user.role not in ['SUPER_ADMIN', 'ADMIN']:
            # Regular staff can only see their own schedules
            queryset = queryset.filter(staff=user)
        
        # Filter by date range
        start_date = self.request.query_params.get('start_date')
        end_date = self.request.query_params.get('end_date')
        
        if start_date:
            try:
                start_date = datetime.strptime(start_date, '%Y-%m-%d')
                queryset = queryset.filter(start_time__gte=start_date)
            except ValueError:
                pass
                
        if end_date:
            try:
                end_date = datetime.strptime(end_date, '%Y-%m-%d')
                # Add one day to include the entire end date
                end_date = end_date + timedelta(days=1)
                queryset = queryset.filter(start_time__lt=end_date)
            except ValueError:
                pass
        
        # Filter by status
        status = self.request.query_params.get('status')
        if status:
            queryset = queryset.filter(status=status.upper())
            
        return queryset
        
    @action(detail=True, methods=['post'])
    def create_backup(self, request, pk=None):
        """Create a backup of the schedule"""
        from .backup_service import ScheduleBackupService
        
        schedule = self.get_object()
        backup_service = ScheduleBackupService()
        backup_path = backup_service.create_backup(schedule)
        
        if backup_path:
            return Response({
                'status': 'success',
                'message': 'Backup created successfully',
                'backup_path': backup_path
            })
        else:
            return Response({
                'status': 'error',
                'message': 'Failed to create backup'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def restore_backup(self, request, pk=None):
        """Restore a schedule from backup"""
        from .backup_service import ScheduleBackupService
        
        schedule = self.get_object()
        backup_file = request.data.get('backup_file')
        
        backup_service = ScheduleBackupService()
        success = backup_service.restore_from_backup(schedule.id, backup_file)
        
        if success:
            return Response({
                'status': 'success',
                'message': 'Schedule restored successfully'
            })
        else:
            return Response({
                'status': 'error',
                'message': 'Failed to restore schedule'
            }, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def safety_briefing(self, request, pk=None):
        """Mark safety briefing as completed for a schedule"""
        schedule = self.get_object()
        
        # Only admins or the assigned staff can mark briefing as completed
        user = request.user
        if schedule.staff != user and user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {"error": "You are not authorized to update this schedule's safety briefing"},
                status=status.HTTP_403_FORBIDDEN
            )
            
        schedule.safety_briefing_completed = True
        schedule.safety_briefing_completed_at = timezone.now()
        schedule.safety_briefing_completed_by = user
        schedule.save()
        
        return Response({
            "message": "Safety briefing marked as completed",
            "schedule_id": schedule.id,
            "completed_at": schedule.safety_briefing_completed_at
        })
    
    @action(detail=True, methods=['post'])
    def update_safety_compliance(self, request, pk=None):
        """Update safety compliance status for a schedule"""
        schedule = self.get_object()
        
        # Only admins can update safety compliance
        user = request.user
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {"error": "Only administrators can update safety compliance status"},
                status=status.HTTP_403_FORBIDDEN
            )
            
        compliance_status = request.data.get('safety_compliance_status')
        if not compliance_status:
            return Response(
                {"error": "Safety compliance status is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        schedule.safety_compliance_status = compliance_status
        schedule.safety_compliance_notes = request.data.get('safety_compliance_notes', '')
        schedule.save()
        
        return Response({
            "message": "Safety compliance status updated successfully",
            "schedule_id": schedule.id,
            "status": schedule.safety_compliance_status
        })
    
    @action(detail=True, methods=['post'])
    def bid_for_shift(self, request, pk=None):
        """Allow staff to bid for an open shift"""
        schedule = self.get_object()
        user = request.user
        
        # Check if schedule is open for bidding
        if not schedule.is_open_for_bidding:
            return Response(
                {"error": "This shift is not open for bidding"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Check if bidding deadline has passed
        if schedule.bidding_deadline and schedule.bidding_deadline < timezone.now():
            return Response(
                {"error": "Bidding deadline has passed"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Add user to preferred staff
        if schedule.preferred_staff is None:
            schedule.preferred_staff = []
            
        # Check if user already bid for this shift
        if user.id in schedule.preferred_staff:
            return Response(
                {"error": "You have already bid for this shift"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        schedule.preferred_staff.append(user.id)
        schedule.save()
        
        return Response({
            "message": "Successfully bid for shift",
            "schedule_id": schedule.id
        })
    
    @action(detail=True, methods=['post'])
    def assign_from_bids(self, request, pk=None):
        """Assign staff from the list of bidders"""
        schedule = self.get_object()
        
        # Only admins can assign shifts
        user = request.user
        if user.role not in ['SUPER_ADMIN', 'ADMIN']:
            return Response(
                {"error": "Only administrators can assign shifts"},
                status=status.HTTP_403_FORBIDDEN
            )
            
        staff_id = request.data.get('staff_id')
        if not staff_id:
            return Response(
                {"error": "Staff ID is required"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        # Check if staff is in the preferred list
        if schedule.preferred_staff and staff_id not in schedule.preferred_staff:
            return Response(
                {"error": "Selected staff has not bid for this shift"},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        try:
            from accounts.models import CustomUser
            staff = CustomUser.objects.get(id=staff_id)
            
            # Assign the staff to the schedule
            schedule.staff = staff
            schedule.is_open_for_bidding = False  # Close bidding
            schedule.save()
            
            return Response({
                "message": "Staff assigned successfully",
                "schedule_id": schedule.id,
                "staff": staff.username
            })
        except Exception as e:
            return Response(
                {"error": f"Failed to assign staff: {str(e)}"},
                status=status.HTTP_400_BAD_REQUEST
            )

    @transaction.atomic
    def perform_create(self, serializer):
        """Create a new schedule with transaction safety"""
        try:
            # Set restaurant from the current user
            restaurant = self.request.user.restaurant
            
            # Save with transaction to ensure data integrity
            schedule = serializer.save(
                restaurant=restaurant,
                created_by=self.request.user,
                last_modified_by=self.request.user
            )
            
            logger.info(f"Schedule created successfully: {schedule.id}")
            
            # Return success
            return Response(
                {"message": "Schedule created successfully", "id": schedule.id},
                status=status.HTTP_201_CREATED
            )
        except Exception as e:
            # Log the error
            logger.error(f"Error creating schedule: {str(e)}")
            # Transaction will be rolled back automatically
            return Response(
                {"error": "Failed to create schedule", "details": str(e)},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

    @transaction.atomic
    def perform_update(self, serializer):
        """Update a schedule with transaction safety"""
        try:
            # Save with transaction to ensure data integrity
            schedule = serializer.save(last_modified_by=self.request.user)
            logger.info(f"Schedule updated successfully: {schedule.id}")
        except Exception as e:
            # Log the error
            logger.error(f"Error updating schedule: {str(e)}")
            # Transaction will be rolled back automatically
            raise

    @transaction.atomic
    def perform_destroy(self, instance):
        """Delete a schedule with audit trail"""
        try:
            # Create a delete change record
            ScheduleChange.objects.create(
                schedule=instance,
                changed_by=self.request.user,
                previous_data={
                    'title': instance.title,
                    'start_time': instance.start_time.isoformat(),
                    'end_time': instance.end_time.isoformat(),
                    'status': instance.status,
                },
                new_data={},
                change_type='DELETE'
            )
            
            # Delete the instance
            instance.delete()
            logger.info(f"Schedule deleted successfully: {instance.id}")
        except Exception as e:
            # Log the error
            logger.error(f"Error deleting schedule: {str(e)}")
            # Transaction will be rolled back automatically
            raise
    
    @action(detail=True, methods=['post'])
    def confirm(self, request, pk=None):
        """Confirm a schedule"""
        schedule = self.get_object()
        schedule.status = 'CONFIRMED'
        schedule.last_modified_by = request.user
        schedule.save()
        return Response({"status": "Schedule confirmed"})
    
    @action(detail=True, methods=['post'])
    def cancel(self, request, pk=None):
        """Cancel a schedule"""
        schedule = self.get_object()
        schedule.status = 'CANCELLED'
        schedule.last_modified_by = request.user
        schedule.save()
        return Response({"status": "Schedule cancelled"})
    
    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        """Mark a schedule as completed"""
        schedule = self.get_object()
        schedule.status = 'COMPLETED'
        schedule.last_modified_by = request.user
        schedule.save()
        return Response({"status": "Schedule marked as completed"})
    
    @action(detail=False, methods=['get'])
    def upcoming(self, request):
        """Get upcoming schedules for the next 7 days"""
        now = timezone.now()
        end_date = now + timedelta(days=7)
        
        # Filter by staff if not admin
        if request.user.role not in ['SUPER_ADMIN', 'ADMIN']:
            schedules = Schedule.objects.filter(
                staff=request.user,
                start_time__gte=now,
                start_time__lte=end_date,
                status__in=['SCHEDULED', 'CONFIRMED']
            )
        else:
            # For admins, show all upcoming schedules in their restaurant
            schedules = Schedule.objects.filter(
                restaurant=request.user.restaurant,
                start_time__gte=now,
                start_time__lte=end_date,
                status__in=['SCHEDULED', 'CONFIRMED']
            )
            
        serializer = self.get_serializer(schedules, many=True)
        return Response(serializer.data)

class ScheduleChangeViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for viewing schedule change history
    """
    queryset = ScheduleChange.objects.all()
    serializer_class = ScheduleChangeSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter change history based on user permissions"""
        user = self.request.user
        
        # Filter by schedule if provided
        schedule_id = self.request.query_params.get('schedule_id')
        if schedule_id:
            queryset = ScheduleChange.objects.filter(schedule_id=schedule_id)
        else:
            queryset = ScheduleChange.objects.all()
        
        # Filter by restaurant for security
        if user.role in ['SUPER_ADMIN', 'ADMIN']:
            # Admins can see all changes in their restaurant
            return queryset.filter(schedule__restaurant=user.restaurant)
        else:
            # Regular staff can only see changes to their own schedules
            return queryset.filter(schedule__staff=user)

class ScheduleNotificationViewSet(viewsets.ModelViewSet):
    """
    API endpoint for schedule notifications
    """
    queryset = ScheduleNotification.objects.all()
    serializer_class = ScheduleNotificationSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Users can only see their own notifications"""
        return ScheduleNotification.objects.filter(recipient=self.request.user)

@api_view(['PUT'])
@permission_classes([IsAuthenticated])
def update_staff_profile_by_user_id(request, staff_id):
    """
    Update staff profile by User ID.
    If profile doesn't exist, create it.
    """
    try:
        user = CustomUser.objects.get(id=staff_id)
        # Check permissions: Admin/Manager can update anyone, Staff can only update themselves
        if request.user.role not in ['SUPER_ADMIN', 'ADMIN', 'MANAGER'] and request.user.id != user.id:
            return Response({"error": "You do not have permission to update this profile"}, status=status.HTTP_403_FORBIDDEN)
            
        profile, created = StaffProfile.objects.get_or_create(user=user)

        # Update CustomUser fields
        user_fields = ['first_name', 'last_name', 'email', 'phone', 'role']
        user_updated = False
        for field in user_fields:
            if field in request.data:
                setattr(user, field, request.data[field])
                user_updated = True
        
        if user_updated:
            user.save()
        
        # Update StaffProfile fields
        # Check if profile data is nested under 'profile' key (as sent by frontend)
        profile_data = request.data.get('profile', request.data)
        
        serializer = StaffProfileSerializer(profile, data=profile_data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    except CustomUser.DoesNotExist:
        return Response({"error": "User not found"}, status=status.HTTP_404_NOT_FOUND)
    except Exception as e:
        return Response({"error": str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)
    
    @action(detail=True, methods=['post'])
    def mark_read(self, request, pk=None):
        """Mark a notification as read"""
        notification = self.get_object()
        notification.is_read = True
        notification.save()
        return Response({"status": "Notification marked as read"})
    
    @action(detail=False, methods=['post'])
    def mark_all_read(self, request):
        """Mark all notifications as read"""
        ScheduleNotification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
        return Response({"status": "All notifications marked as read"})

class StaffAvailabilityViewSet(viewsets.ModelViewSet):
    """
    API endpoint for staff availability preferences
    """
    queryset = StaffAvailability.objects.all()
    serializer_class = StaffAvailabilitySerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter availability based on user permissions"""
        user = self.request.user
        
        # Filter by staff member if specified
        staff_id = self.request.query_params.get('staff_id')
        
        if user.role in ['SUPER_ADMIN', 'ADMIN'] and staff_id:
            # Admins can see availability for any staff in their restaurant
            return StaffAvailability.objects.filter(
                staff_id=staff_id,
                staff__restaurant=user.restaurant
            )
        else:
            # Regular staff can only see their own availability
            return StaffAvailability.objects.filter(staff=user)
    
    def perform_create(self, serializer):
        """Create availability for the current user if not specified"""
        staff_id = self.request.data.get('staff')
        
        # If staff ID is provided and user is admin, use that
        if staff_id and self.request.user.role in ['SUPER_ADMIN', 'ADMIN']:
            serializer.save()
        else:
            # Otherwise use the current user
            serializer.save(staff=self.request.user)

class PerformanceMetricViewSet(viewsets.ModelViewSet):
    """
    API endpoint for staff performance metrics
    """
    queryset = PerformanceMetric.objects.all()
    serializer_class = PerformanceMetricSerializer
    permission_classes = [IsAuthenticated]
    
    def get_queryset(self):
        """Filter metrics based on user permissions"""
        user = self.request.user
        
        if user.role in ['SUPER_ADMIN', 'ADMIN']:
            # Admins can see metrics for all staff in their restaurant
            return PerformanceMetric.objects.filter(staff__restaurant=user.restaurant)
        else:
            # Regular staff can only see their own metrics
            return PerformanceMetric.objects.filter(staff=user)
