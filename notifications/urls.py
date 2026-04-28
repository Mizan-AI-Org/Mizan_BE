from django.urls import path
from . import views, views_agent

app_name = 'notifications'

urlpatterns = [
    path('', views.NotificationListView.as_view(), name='notification-list'),
    path('stats/', views.notification_stats, name='notification-stats'),
    path('<uuid:notification_id>/read/', views.mark_notification_read, name='mark-notification-read'),
    path('<uuid:notification_id>/delete/', views.delete_notification, name='delete-notification'),

    path('mark-all-read/', views.mark_all_notifications_read, name='mark-all-notifications-read'),
    path('bulk-actions/', views.bulk_notification_actions, name='bulk-notification-actions'),

    path('preferences/', views.NotificationPreferenceView.as_view(), name='notification-preferences'),

    path('device-tokens/', views.user_device_tokens, name='user-device-tokens'),
    path('device-tokens/register/', views.register_device_token, name='register-device-token'),
    path('device-tokens/unregister/', views.unregister_device_token, name='unregister-device-token'),

    path('test/', views.send_test_notification, name='send-test-notification'),

    path('announcements/create/', views.create_announcement, name='create-announcement'),
    path('announcements/<uuid:notification_id>/ack/', views.acknowledge_announcement, name='acknowledge-announcement'),
    path('announcements/report-issue/', views.report_delivery_issue, name='report-delivery-issue'),

    path('health-check/', views.health_check_notifications, name='health-check-notifications'),

    path('whatsapp/webhook/', views.whatsapp_webhook, name='whatsapp-webhook'),

    path('agent/send-whatsapp/', views_agent.send_whatsapp_from_agent, name='agent-send-whatsapp'),
    path('agent/staff-captured-order/', views_agent.agent_create_staff_captured_order, name='agent-staff-captured-order'),
    path('agent/announcement/', views_agent.agent_send_announcement, name='agent-send-announcement'),
    path('agent/start-whatsapp-checklist/', views_agent.agent_start_whatsapp_checklist, name='agent-start-whatsapp-checklist'),
    path('agent/checklist/respond/', views_agent.agent_checklist_respond, name='agent-checklist-respond'),
    path('agent/preview-checklist/', views_agent.agent_preview_checklist, name='agent-preview-checklist'),
    path('agent/voice-reply/', views_agent.agent_voice_reply, name='agent-voice-reply'),
]
