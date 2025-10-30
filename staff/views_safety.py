from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from django.db.models import Sum, Count
from django.utils import timezone

from accounts.models import Restaurant
from staff.models_safety import (
    StandardOperatingProcedure,
    SafetyChecklist,
    ScheduleTask,
    SafetyConcernReport,
    SafetyRecognition
)
from staff.serializers_safety import (
    StandardOperatingProcedureSerializer,
    SafetyChecklistSerializer,
    ScheduleTaskSerializer,
    SafetyConcernReportSerializer,
    SafetyRecognitionSerializer
)
from staff.permissions import IsManagerOrReadOnly, IsStaffMember

class StandardOperatingProcedureViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Standard Operating Procedures
    """
    serializer_class = StandardOperatingProcedureSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrReadOnly]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return StandardOperatingProcedure.objects.all()
        
        # Filter by restaurants the user has access to
        restaurants = Restaurant.objects.filter(staff=user)
        return StandardOperatingProcedure.objects.filter(restaurant__in=restaurants)

class SafetyChecklistViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Safety Checklists
    """
    serializer_class = SafetyChecklistSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrReadOnly]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return SafetyChecklist.objects.all()
        
        # Filter by restaurants the user has access to
        restaurants = Restaurant.objects.filter(staff=user)
        return SafetyChecklist.objects.filter(restaurant__in=restaurants)

class ScheduleTaskViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Schedule Tasks
    """
    serializer_class = ScheduleTaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsStaffMember]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return ScheduleTask.objects.all()
        
        # Staff can see tasks assigned to their schedules
        return ScheduleTask.objects.filter(schedule__staff=user)
    
    @action(detail=True, methods=['post'])
    def complete_task(self, request, pk=None):
        """Mark a task as completed"""
        task = self.get_object()
        
        if task.status == 'COMPLETED':
            return Response({"detail": "Task is already completed"}, status=status.HTTP_400_BAD_REQUEST)
        
        task.status = 'COMPLETED'
        task.completed_at = timezone.now()
        task.completed_by = request.user
        task.completion_notes = request.data.get('completion_notes', '')
        task.save()
        
        serializer = self.get_serializer(task)
        return Response(serializer.data)
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, IsManagerOrReadOnly])
    def reassign_task(self, request, pk=None):
        """Reassign a task to a different schedule"""
        task = self.get_object()
        new_schedule_id = request.data.get('schedule_id')
        
        if not new_schedule_id:
            return Response({"detail": "New schedule ID is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        task.schedule_id = new_schedule_id
        task.save()
        
        serializer = self.get_serializer(task)
        return Response(serializer.data)

class SafetyConcernReportViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Safety Concern Reports
    """
    serializer_class = SafetyConcernReportSerializer
    permission_classes = [permissions.IsAuthenticated]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return SafetyConcernReport.objects.all()
        
        # Staff can see reports for their restaurants, but not anonymous ones from others
        restaurants = Restaurant.objects.filter(staff=user)
        return SafetyConcernReport.objects.filter(
            restaurant__in=restaurants
        ).exclude(
            is_anonymous=True, 
            reporter__isnull=False,
            reporter__id__ne=user.id
        )
    
    @action(detail=True, methods=['post'], permission_classes=[permissions.IsAuthenticated, IsManagerOrReadOnly])
    def update_status(self, request, pk=None):
        """Update the status of a safety concern report"""
        report = self.get_object()
        new_status = request.data.get('status')
        resolution_notes = request.data.get('resolution_notes', '')
        
        if not new_status:
            return Response({"detail": "New status is required"}, status=status.HTTP_400_BAD_REQUEST)
        
        report.status = new_status
        report.resolution_notes = resolution_notes
        
        if new_status in ['ADDRESSED', 'RESOLVED']:
            report.resolved_by = request.user
            report.resolved_at = timezone.now()
        
        report.save()
        
        serializer = self.get_serializer(report)
        return Response(serializer.data)

class SafetyRecognitionViewSet(viewsets.ModelViewSet):
    """
    API endpoint for Safety Recognitions
    """
    serializer_class = SafetyRecognitionSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrReadOnly]
    
    def get_queryset(self):
        user = self.request.user
        if user.is_staff or user.is_superuser:
            return SafetyRecognition.objects.all()
        
        # Staff can see recognitions for their restaurants
        restaurants = Restaurant.objects.filter(staff=user)
        return SafetyRecognition.objects.filter(restaurant__in=restaurants)
    
    @action(detail=False, methods=['get'])
    def leaderboard(self, request):
        """Get safety recognition leaderboard"""
        restaurant_id = request.query_params.get('restaurant')
        
        queryset = self.get_queryset()
        if restaurant_id:
            queryset = queryset.filter(restaurant_id=restaurant_id)
        
        # Group by staff and sum points
        leaderboard = queryset.values('staff', 'staff__first_name', 'staff__last_name') \
            .annotate(
                total_points=Sum('points'),
                recognition_count=Count('id')
            ) \
            .order_by('-total_points')[:10]
        
        return Response(leaderboard)