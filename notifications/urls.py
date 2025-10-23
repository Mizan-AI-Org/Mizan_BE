from django.urls import path
from . import views

urlpatterns = [
    path('notifications/', views.NotificationList.as_view(), name='notification-list'),
    path('notifications/<uuid:notification_id>/mark-read/', views.mark_notification_as_read, name='mark-notification-read'),
    path('notifications/mark-all-read/', views.mark_all_notifications_as_read, name='mark-all-notifications-read'),
    path('device-token/register/', views.DeviceTokenRegisterAPIView.as_view(), name='device-token-register'),
    path('device-token/unregister/', views.DeviceTokenUnregisterAPIView.as_view(), name='device-token-unregister'),
]
