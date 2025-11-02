from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone
from django.db.models import Q
from django.shortcuts import get_object_or_404
from accounts.permissions import IsAdminOrSuperAdmin

from .models import Notification, NotificationPreference, DeviceToken
from .serializers import (
    NotificationSerializer, 
    NotificationPreferenceSerializer,
    DeviceTokenSerializer,
    AnnouncementCreateSerializer
)
from .services import notification_service


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationListView(generics.ListAPIView):
    """List notifications for the authenticated user"""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        user = self.request.user
        queryset = Notification.objects.filter(recipient=user).order_by('-created_at')
        
        # Filter by read status
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(read_at__isnull=False)
            else:
                queryset = queryset.filter(read_at__isnull=True)
        
        # Filter by notification type
        notification_type = self.request.query_params.get('type')
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)
        
        # Filter by priority
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        
        return queryset


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_notification_read(request, notification_id):
    """Mark a specific notification as read"""
    try:
        notification = get_object_or_404(
            Notification, 
            id=notification_id, 
            recipient=request.user
        )
        
        if not notification.read_at:
            notification.mark_as_read()
            
        return Response({
            'success': True,
            'message': 'Notification marked as read',
            'read_at': notification.read_at
        })
        
    except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAdminOrSuperAdmin])
def create_announcement(request):
    """Create and send announcement to all restaurant staff"""
    try:
        serializer = AnnouncementCreateSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create notifications for all staff
        notifications = serializer.create_notifications(sender=request.user)
        targeted = bool(
            serializer.validated_data.get('recipients_staff_ids') or 
            serializer.validated_data.get('recipients_departments')
        )
        
        # Send via notification service for immediate delivery
        for notification in notifications:
            notification_service.send_custom_notification(
                recipient=notification.recipient,
                message=notification.message,
                notification_type='ANNOUNCEMENT',
                channels=['app']
            )
        
        return Response({
            'success': True,
            'message': (
                f"Announcement sent to {len(notifications)} targeted recipients"
                if targeted else
                f"Announcement sent to {len(notifications)} staff members"
            ),
            'notification_count': len(notifications),
            'title': serializer.validated_data['title'],
            'targeted': targeted
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_all_notifications_read(request):
    """Mark all unread notifications as read for the user"""
    try:
        unread_notifications = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True
        )
        
        count = unread_notifications.count()
        unread_notifications.update(read_at=timezone.now())
        
        return Response({
            'success': True,
            'message': f'{count} notifications marked as read',
            'count': count
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def notification_stats(request):
    """Get notification statistics for the user"""
    try:
        user = request.user
        
        total_count = Notification.objects.filter(recipient=user).count()
        unread_count = Notification.objects.filter(
            recipient=user, 
            read_at__isnull=True
        ).count()
        
        # Count by type
        type_counts = {}
        for notification_type, _ in Notification.NOTIFICATION_TYPES:
            count = Notification.objects.filter(
                recipient=user,
                notification_type=notification_type
            ).count()
            if count > 0:
                type_counts[notification_type] = count
        
        # Count by priority
        priority_counts = {}
        for priority, _ in Notification.PRIORITY_LEVELS:
            count = Notification.objects.filter(
                recipient=user,
                priority=priority
            ).count()
            if count > 0:
                priority_counts[priority] = count
        
        return Response({
            'total_count': total_count,
            'unread_count': unread_count,
            'type_counts': type_counts,
            'priority_counts': priority_counts
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


class NotificationPreferenceView(generics.RetrieveUpdateAPIView):
    """Get and update notification preferences for the authenticated user"""
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        preference, created = NotificationPreference.objects.get_or_create(
            user=self.request.user
        )
        return preference


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def register_device_token(request):
    """Register or update a device token for push notifications"""
    try:
        token = request.data.get('token')
        device_type = request.data.get('device_type', 'UNKNOWN')
        device_name = request.data.get('device_name', '')
        
        if not token:
            return Response({
                'success': False,
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Deactivate existing tokens for this user and device type
        DeviceToken.objects.filter(
            user=request.user,
            device_type=device_type
        ).update(is_active=False)
        
        # Create or update the token
        device_token, created = DeviceToken.objects.update_or_create(
            user=request.user,
            token=token,
            defaults={
                'device_type': device_type,
                'device_name': device_name,
                'is_active': True,
                'last_used': timezone.now()
            }
        )
        
        return Response({
            'success': True,
            'message': 'Device token registered successfully',
            'token_id': device_token.id,
            'created': created
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def unregister_device_token(request):
    """Unregister a device token"""
    try:
        token = request.data.get('token')
        
        if not token:
            return Response({
                'success': False,
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        DeviceToken.objects.filter(
            user=request.user,
            token=token
        ).update(is_active=False)
        
        return Response({
            'success': True,
            'message': 'Device token unregistered successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def user_device_tokens(request):
    """List active device tokens for the user"""
    try:
        tokens = DeviceToken.objects.filter(
            user=request.user,
            is_active=True
        ).order_by('-last_used')
        
        serializer = DeviceTokenSerializer(tokens, many=True)
        
        return Response({
            'success': True,
            'tokens': serializer.data
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def send_test_notification(request):
    """Send a test notification to the user (for testing purposes)"""
    try:
        message = request.data.get('message', 'This is a test notification')
        channels = request.data.get('channels', ['app'])
        
        notification_service.send_custom_notification(
            recipient=request.user,
            message=message,
            notification_type='SYSTEM_ALERT',
            channels=channels
        )
        
        return Response({
            'success': True,
            'message': 'Test notification sent successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def delete_notification(request, notification_id):
    """Delete a specific notification"""
    try:
        notification = get_object_or_404(
            Notification, 
            id=notification_id, 
            recipient=request.user
        )
        
        notification.delete()
        
        return Response({
            'success': True,
            'message': 'Notification deleted successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_notification_actions(request):
    """Perform bulk actions on notifications"""
    try:
        action = request.data.get('action')  # 'mark_read', 'delete'
        notification_ids = request.data.get('notification_ids', [])
        
        if not action or not notification_ids:
            return Response({
                'success': False,
                'error': 'Action and notification_ids are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        notifications = Notification.objects.filter(
            id__in=notification_ids,
            recipient=request.user
        )
        
        if action == 'mark_read':
            count = notifications.filter(read_at__isnull=True).update(
                read_at=timezone.now()
            )
            message = f'{count} notifications marked as read'
            
        elif action == 'delete':
            count = notifications.count()
            notifications.delete()
            message = f'{count} notifications deleted'
            
        else:
            return Response({
                'success': False,
                'error': 'Invalid action'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': True,
            'message': message,
            'count': count
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)
