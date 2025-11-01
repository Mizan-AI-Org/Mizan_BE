from django.urls import path
from . import views

app_name = 'notifications'

urlpatterns = [
    # Notification CRUD operations
    path('', views.NotificationListView.as_view(), name='notification-list'),
    path('stats/', views.notification_stats, name='notification-stats'),
    path('<int:notification_id>/read/', views.mark_notification_read, name='mark-notification-read'),
    path('<int:notification_id>/delete/', views.delete_notification, name='delete-notification'),
    path('mark-all-read/', views.mark_all_notifications_read, name='mark-all-notifications-read'),
    path('bulk-actions/', views.bulk_notification_actions, name='bulk-notification-actions'),
    
    # Notification preferences
    path('preferences/', views.NotificationPreferenceView.as_view(), name='notification-preferences'),
    
    # Device token management
    path('device-tokens/', views.user_device_tokens, name='user-device-tokens'),
    path('device-tokens/register/', views.register_device_token, name='register-device-token'),
    path('device-tokens/unregister/', views.unregister_device_token, name='unregister-device-token'),
    
    # Testing and utilities
    path('test/', views.send_test_notification, name='send-test-notification'),
]
