import requests
import json
from django.conf import settings
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.mail import send_mail
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification, DeviceToken, NotificationLog
import firebase_admin
from firebase_admin import messaging
import logging

logger = logging.getLogger(__name__)


class NotificationService:
    """Unified notification service (NO DUPLICATES)"""

    def __init__(self):
        self.channel_layer = get_channel_layer()

    # ------------------------------------------------------------------------------------
    # MAIN FIX: send_custom_notification() NO LONGER CREATES DUPLICATES
    # ------------------------------------------------------------------------------------
    def send_custom_notification(
        self,
        recipient,
        message=None,
        notification_type='OTHER',
        channels=None,
        sender=None,
        title='Notification',
        override_preferences=False,
        notification=None,     # <── NEW
    ):
        """
        Unified notification sender.
        - If `notification` is provided → USE IT (do NOT create a new one)
        - If not provided → create a new Notification
        """

        if channels is None:
            channels = ['app']

        # -------------------------------------------------------------
        # OPTION A — existing notification (Announcement, Scheduled, etc)
        # -------------------------------------------------------------
        if notification is not None:
            # Already created by serializer/view/scheduler
            pass

        # -------------------------------------------------------------
        # OPTION B — create a new one (shifts, tasks, sms, etc)
        # -------------------------------------------------------------
        else:
            notification = Notification.objects.create(
                recipient=recipient,
                message=message,
                notification_type=notification_type,
                title=title,
                sender=sender
            )

        data = {
            'recipient': recipient,
            'message': notification.message,
            'notification_type': notification.notification_type,
            'title': notification.title,
            'sender': sender,
            'notification': notification,
        }

        channels_used = []

        # WebSocket
        if 'app' in channels:
            ok, _ = self._send_in_app_notification(data, existing_notification=notification)
            if ok:
                channels_used.append('app')

        # WhatsApp
        if 'whatsapp' in channels and self._should_send_whatsapp(recipient):
            if self._send_whatsapp_notification(data):
                channels_used.append('whatsapp')

        # Push
        if 'push' in channels:
            if self._send_push_notification(data):
                channels_used.append('push')

        # Email
        if 'email' in channels and (override_preferences or self._should_send_email(recipient)):
            if self._send_email_notification(data):
                channels_used.append('email')

        # Final update
        notification.channels_sent = channels_used
        notification.delivery_status = {
            ch: {
                'status': 'SENT',
                'timestamp': timezone.now().isoformat(),
            }
            for ch in channels_used
        }
        notification.save()

        return True, channels_used

    # ====================================================================================
    # INTERNAL METHODS (UNCHANGED EXCEPT NEVER CREATE A NOTIFICATION TWICE)
    # ====================================================================================
    def _send_in_app_notification(self, notification_data, existing_notification=None):
        """WebSocket real-time event without creating duplicate notifications."""
        try:
            notification = existing_notification

            # Log
            try:
                NotificationLog.objects.create(
                    notification=notification,
                    channel='app',
                    recipient_address=str(notification.recipient.id),
                    status='SENT'
                )
            except Exception:
                pass

            group = f"user_{notification.recipient.id}_notifications"

            # IMPORTANT FIX → match consumer handler name
            async_to_sync(self.channel_layer.group_send)(
                group,
                {
                    'type': 'send_notification',  # must match consumer method!
                    'notification': {
                        'id': str(notification.id),
                        'title': notification.title,
                        'message': notification.message,
                        'notification_type': notification.notification_type,
                        'created_at': notification.created_at.isoformat(),
                        'is_read': notification.is_read,
                        'data': notification_data.get('data', {})
                    }
                }
            )

            return True, notification

        except Exception as e:
            logger.error(f"IN-APP ERROR: {e}")
            return False, existing_notification

    # ----------------------------------------------------------------------

    def _send_whatsapp_notification(self, data):
        try:
            recipient = data['recipient']
            phone = getattr(recipient, 'phone_number', None)

            if not phone:
                return False

            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)

            if not token or not phone_id:
                return False

            phone = ''.join(filter(str.isdigit, phone))
            url = f"https://graph.facebook.com/v17.0/{phone_id}/messages"

            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": data['message']}
            }

            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )

            return resp.status_code == 200

        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            return False

    # ----------------------------------------------------------------------

    def _send_push_notification(self, data):
        try:
            tokens = DeviceToken.objects.filter(user=data['recipient'])
            if not tokens.exists():
                return False

            if not firebase_admin._apps:
                return False

            msg = messaging.MulticastMessage(
                tokens=[t.token for t in tokens],
                notification=messaging.Notification(
                    title=data['title'],
                    body=data['message']
                )
            )
            response = messaging.send_multicast(msg)
            return response.success_count > 0

        except Exception as e:
            logger.error(f"Push error: {e}")
            return False

    # ----------------------------------------------------------------------

    def _send_email_notification(self, data):
        try:
            recipient = data['recipient']
            if not recipient.email:
                return False

            html = render_to_string('notifications/shift_notification_email.html', {
                'user': recipient,
                'message': data['message'],
                'title': data['title']
            })

            send_mail(
                subject=data['title'],
                message=data['message'],
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                html_message=html
            )

            return True

        except Exception as e:
            logger.error(f"Email error: {e}")
            return False

    # ----------------------------------------------------------------------
    # PREFERENCE HELPERS
    # ----------------------------------------------------------------------

    def _should_send_whatsapp(self, user):
        pref = getattr(user, 'notification_preference', None)
        return not pref or pref.whatsapp_enabled

    def _should_send_email(self, user):
        pref = getattr(user, 'notification_preference', None)
        return not pref or pref.email_enabled

    
# SINGLETON INSTANCE
notification_service = NotificationService()


