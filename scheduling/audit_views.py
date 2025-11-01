"""
API Views for Audit Trail System
Provides endpoints for accessing and managing audit logs
"""

from datetime import datetime, timedelta
from django.http import HttpResponse
from django.utils import timezone
from django.contrib.contenttypes.models import ContentType
from rest_framework import viewsets, permissions, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django_filters.rest_framework import DjangoFilterBackend
from django_filters import rest_framework as filters

from .audit import AuditLog, AuditTrailService, AuditActionType, AuditSeverity
from .permissions import IsManagerOrAdmin
from .serializers import AuditLogSerializer

class AuditLogFilter(filters.FilterSet):
    """Filter set for audit logs"""
    start_date = filters.DateTimeFilter(field_name='timestamp', lookup_expr='gte')
    end_date = filters.DateTimeFilter(field_name='timestamp', lookup_expr='lte')
    action = filters.ChoiceFilter(choices=AuditActionType.choices)
    severity = filters.ChoiceFilter(choices=AuditSeverity.choices)
    user_id = filters.NumberFilter(field_name='user__id')
    content_type = filters.CharFilter(field_name='content_type__model')
    
    class Meta:
        model = AuditLog
        fields = ['start_date', 'end_date', 'action', 'severity', 'user_id', 'content_type']

class AuditLogPagination(PageNumberPagination):
    """Custom pagination for audit logs"""
    page_size = 50
    page_size_query_param = 'page_size'
    max_page_size = 500

class AuditLogViewSet(viewsets.ReadOnlyModelViewSet):
    """
    ViewSet for audit logs - read-only access for managers and admins
    """
    serializer_class = AuditLogSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    pagination_class = AuditLogPagination
    filter_backends = [DjangoFilterBackend]
    filterset_class = AuditLogFilter
    ordering = ['-timestamp']
    
    def get_queryset(self):
        """Filter audit logs by restaurant"""
        return AuditLog.objects.filter(
            restaurant=self.request.user.restaurant
        ).select_related('user', 'content_type')
    
    @action(detail=False, methods=['get'])
    def summary(self, request):
        """Get audit log summary statistics"""
        queryset = self.get_queryset()
        
        # Apply date filters if provided
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        if start_date:
            try:
                start_date = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
                queryset = queryset.filter(timestamp__gte=start_date)
            except ValueError:
                pass
        
        if end_date:
            try:
                end_date = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
                queryset = queryset.filter(timestamp__lte=end_date)
            except ValueError:
                pass
        
        # Calculate statistics
        total_activities = queryset.count()
        
        # Activities by action type
        action_stats = {}
        for action_choice in AuditActionType.choices:
            action = action_choice[0]
            count = queryset.filter(action=action).count()
            if count > 0:
                action_stats[action] = {
                    'count': count,
                    'label': action_choice[1],
                    'percentage': round((count / total_activities) * 100, 2) if total_activities > 0 else 0
                }
        
        # Activities by severity
        severity_stats = {}
        for severity_choice in AuditSeverity.choices:
            severity = severity_choice[0]
            count = queryset.filter(severity=severity).count()
            if count > 0:
                severity_stats[severity] = {
                    'count': count,
                    'label': severity_choice[1],
                    'percentage': round((count / total_activities) * 100, 2) if total_activities > 0 else 0
                }
        
        # Top active users
        user_stats = (
            queryset.values('user__id', 'user__first_name', 'user__last_name')
            .annotate(activity_count=models.Count('id'))
            .order_by('-activity_count')[:10]
        )
        
        # Recent critical activities
        critical_activities = queryset.filter(
            severity=AuditSeverity.CRITICAL
        ).order_by('-timestamp')[:10]
        
        # Daily activity trend (last 30 days)
        thirty_days_ago = timezone.now() - timedelta(days=30)
        daily_activities = []
        for i in range(30):
            date = thirty_days_ago + timedelta(days=i)
            count = queryset.filter(
                timestamp__date=date.date()
            ).count()
            daily_activities.append({
                'date': date.strftime('%Y-%m-%d'),
                'count': count
            })
        
        return Response({
            'total_activities': total_activities,
            'action_statistics': action_stats,
            'severity_statistics': severity_stats,
            'top_users': [
                {
                    'user_id': stat['user__id'],
                    'name': f"{stat['user__first_name']} {stat['user__last_name']}",
                    'activity_count': stat['activity_count']
                }
                for stat in user_stats
            ],
            'critical_activities': AuditLogSerializer(critical_activities, many=True).data,
            'daily_trend': daily_activities
        })
    
    @action(detail=False, methods=['get'])
    def export(self, request):
        """Export audit logs"""
        format_type = request.query_params.get('format', 'json')
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        # Parse dates
        start_datetime = None
        end_datetime = None
        
        if start_date:
            try:
                start_datetime = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {'error': 'Invalid start_date format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        if end_date:
            try:
                end_datetime = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                return Response(
                    {'error': 'Invalid end_date format'},
                    status=status.HTTP_400_BAD_REQUEST
                )
        
        try:
            # Export audit trail
            export_data = AuditTrailService.export_audit_trail(
                restaurant=request.user.restaurant,
                start_date=start_datetime,
                end_date=end_datetime,
                format=format_type
            )
            
            # Log the export activity
            AuditTrailService.log_activity(
                user=request.user,
                action=AuditActionType.EXPORT,
                description=f"Exported audit trail ({format_type})",
                severity=AuditSeverity.MEDIUM,
                metadata={
                    'format': format_type,
                    'start_date': start_date,
                    'end_date': end_date,
                    'record_count': len(export_data.split('\n')) if format_type == 'json' else 'unknown'
                },
                request=request
            )
            
            # Return as file download
            response = HttpResponse(export_data, content_type='application/json')
            filename = f"audit_trail_{timezone.now().strftime('%Y%m%d_%H%M%S')}.{format_type}"
            response['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
            
        except ValueError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_400_BAD_REQUEST
            )
        except Exception as e:
            return Response(
                {'error': 'Export failed'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )
    
    @action(detail=False, methods=['get'])
    def user_activity(self, request):
        """Get activity summary for a specific user"""
        user_id = request.query_params.get('user_id')
        if not user_id:
            return Response(
                {'error': 'user_id parameter is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            from django.contrib.auth import get_user_model
            User = get_user_model()
            user = User.objects.get(id=user_id, restaurant=request.user.restaurant)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Parse date filters
        start_date = request.query_params.get('start_date')
        end_date = request.query_params.get('end_date')
        
        start_datetime = None
        end_datetime = None
        
        if start_date:
            try:
                start_datetime = datetime.fromisoformat(start_date.replace('Z', '+00:00'))
            except ValueError:
                pass
        
        if end_date:
            try:
                end_datetime = datetime.fromisoformat(end_date.replace('Z', '+00:00'))
            except ValueError:
                pass
        
        # Get user activity summary
        summary = AuditTrailService.get_user_activity_summary(
            user=user,
            start_date=start_datetime,
            end_date=end_datetime
        )
        
        # Get recent activities
        recent_activities = AuditLog.objects.filter(
            user=user,
            restaurant=request.user.restaurant
        )
        
        if start_datetime:
            recent_activities = recent_activities.filter(timestamp__gte=start_datetime)
        if end_datetime:
            recent_activities = recent_activities.filter(timestamp__lte=end_datetime)
        
        recent_activities = recent_activities.order_by('-timestamp')[:20]
        
        return Response({
            'user': {
                'id': user.id,
                'name': f"{user.first_name} {user.last_name}",
                'email': user.email,
                'role': getattr(user, 'role', 'Unknown')
            },
            'summary': {
                'total_activities': summary['total_activities'],
                'action_counts': summary['action_counts'],
                'severity_counts': summary['severity_counts'],
                'first_activity': summary['first_activity'].timestamp.isoformat() if summary['first_activity'] else None,
                'last_activity': summary['last_activity'].timestamp.isoformat() if summary['last_activity'] else None,
            },
            'recent_activities': AuditLogSerializer(recent_activities, many=True).data
        })
    
    @action(detail=False, methods=['get'])
    def object_trail(self, request):
        """Get audit trail for a specific object"""
        content_type_name = request.query_params.get('content_type')
        object_id = request.query_params.get('object_id')
        
        if not content_type_name or not object_id:
            return Response(
                {'error': 'content_type and object_id parameters are required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        try:
            content_type = ContentType.objects.get(model=content_type_name.lower())
            model_class = content_type.model_class()
            
            # Get the object and verify it belongs to the user's restaurant
            content_object = model_class.objects.get(id=object_id)
            
            # Check if object belongs to user's restaurant
            if hasattr(content_object, 'restaurant') and content_object.restaurant != request.user.restaurant:
                return Response(
                    {'error': 'Object not found'},
                    status=status.HTTP_404_NOT_FOUND
                )
            
        except (ContentType.DoesNotExist, model_class.DoesNotExist):
            return Response(
                {'error': 'Object not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Get audit trail for the object
        audit_trail = AuditTrailService.get_object_audit_trail(content_object)
        
        return Response({
            'object': {
                'type': content_type_name,
                'id': object_id,
                'str': str(content_object)
            },
            'audit_trail': AuditLogSerializer(audit_trail, many=True).data
        })
    
    @action(detail=False, methods=['get'])
    def recent_critical(self, request):
        """Get recent critical activities"""
        hours = int(request.query_params.get('hours', 24))
        since = timezone.now() - timedelta(hours=hours)
        
        critical_activities = self.get_queryset().filter(
            severity__in=[AuditSeverity.HIGH, AuditSeverity.CRITICAL],
            timestamp__gte=since
        ).order_by('-timestamp')[:50]
        
        return Response({
            'activities': AuditLogSerializer(critical_activities, many=True).data,
            'count': critical_activities.count(),
            'since': since.isoformat()
        })