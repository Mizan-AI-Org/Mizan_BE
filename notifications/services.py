import requests
import json
from django.conf import settings
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.mail import send_mail
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification, DeviceToken
import firebase_admin
from firebase_admin import messaging
import logging

logger = logging.getLogger(__name__)

class NotificationService:
    """Comprehensive notification service supporting multiple channels"""
    
    def __init__(self):
        self.channel_layer = get_channel_layer()
    
    def send_shift_notification(self, shift, notification_type='SHIFT_ASSIGNED', custom_message=None):
        """Send comprehensive shift notifications through multiple channels"""
        try:
            # Prepare notification data
            notification_data = self._prepare_shift_notification_data(shift, notification_type, custom_message)
            
            # Send through all enabled channels
            channels_used = []
            
            # 1. In-app notification (always sent)
            self._send_in_app_notification(notification_data)
            channels_used.append('app')
            
            # 2. WhatsApp notification (if enabled and phone available)
            if self._should_send_whatsapp(shift.staff):
                if self._send_whatsapp_notification(notification_data):
                    channels_used.append('whatsapp')
            
            # 3. Push notification (if device tokens available)
            if self._send_push_notification(notification_data):
                channels_used.append('push')
            
            # 4. Email notification (if enabled)
            if self._should_send_email(shift.staff):
                if self._send_email_notification(notification_data):
                    channels_used.append('email')
            
            # Update shift notification tracking
            shift.notification_sent = True
            shift.notification_sent_at = timezone.now()
            shift.notification_channels = channels_used
            shift.save(update_fields=['notification_sent', 'notification_sent_at', 'notification_channels'])
            
            logger.info(f"Shift notification sent successfully via {channels_used} for shift {shift.id}")
            return True, channels_used
            
        except Exception as e:
            logger.error(f"Failed to send shift notification for shift {shift.id}: {str(e)}")
            return False, []
    
    def _prepare_shift_notification_data(self, shift, notification_type, custom_message=None):
        """Prepare notification data for shift notifications"""
        # Format shift times with timezone
        start_time = shift.start_time.strftime('%Y-%m-%d %H:%M')
        end_time = shift.end_time.strftime('%Y-%m-%d %H:%M')
        
        # Generate message based on type
        if custom_message:
            message = custom_message
        else:
            message_templates = {
                'SHIFT_ASSIGNED': f"🔔 New Shift Assigned\n\n📅 Date: {start_time} - {end_time}\n📍 Location: {shift.workspace_location or 'Main Area'}\n🏢 Department: {shift.department or 'General'}",
                'SHIFT_UPDATED': f"📝 Shift Updated\n\n📅 New Time: {start_time} - {end_time}\n📍 Location: {shift.workspace_location or 'Main Area'}",
                'SHIFT_CANCELLED': f"❌ Shift Cancelled\n\n📅 Original Time: {start_time} - {end_time}",
                'SHIFT_REMINDER': f"⏰ Shift Reminder\n\n📅 Upcoming: {start_time} - {end_time}\n📍 Location: {shift.workspace_location or 'Main Area'}",
            }
            message = message_templates.get(notification_type, f"Shift notification: {start_time} - {end_time}")
        
        # Add equipment and preparation info if available
        if shift.equipment_needed:
            equipment_list = ', '.join(shift.equipment_needed)
            message += f"\n🛠️ Equipment: {equipment_list}"
        
        if shift.preparation_instructions:
            message += f"\n📋 Instructions: {shift.preparation_instructions}"
        
        if shift.safety_briefing_required:
            message += f"\n⚠️ Safety briefing required before shift"
        
        return {
            'recipient': shift.staff,
            'message': message,
            'notification_type': notification_type,
            'shift': shift,
            'title': f"Shift {notification_type.replace('_', ' ').title()}",
            'data': {
                'shift_id': str(shift.id),
                'start_time': shift.start_time.isoformat(),
                'end_time': shift.end_time.isoformat(),
                'location': shift.workspace_location,
                'department': shift.department,
            }
        }
    
    def _send_in_app_notification(self, notification_data):
        """Send in-app notification via WebSocket"""
        try:
            # Create database notification
            notification = Notification.objects.create(
                recipient=notification_data['recipient'],
                message=notification_data['message'],
                notification_type=notification_data['notification_type']
            )
            
            # Send WebSocket notification
            # Match consumer group naming: user_<id>_notifications
            group_name = f'user_{str(notification_data["recipient"].id)}_notifications'
            
            message_data = {
                'type': 'notification_message',
                'message': {
                    'id': str(notification.id),
                    'message': notification.message,
                    'notification_type': notification.notification_type,
                    'created_at': notification.created_at.isoformat(),
                    'is_read': notification.is_read,
                    'data': notification_data.get('data', {})
                }
            }
            
            async_to_sync(self.channel_layer.group_send)(group_name, message_data)
            return True
            
        except Exception as e:
            logger.error(f"Failed to send in-app notification: {str(e)}")
            return False
    
    def _send_whatsapp_notification(self, notification_data):
        """Send WhatsApp notification using WhatsApp Business API"""
        try:
            recipient = notification_data['recipient']
            
            # Check if user has WhatsApp number
            if not hasattr(recipient, 'phone_number') or not recipient.phone_number:
                return False
            
            # WhatsApp API configuration (you'll need to set these in settings)
            whatsapp_token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            whatsapp_phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            
            if not whatsapp_token or not whatsapp_phone_id:
                logger.warning("WhatsApp credentials not configured")
                return False
            
            # Format phone number (remove any non-digits and ensure proper format)
            phone = ''.join(filter(str.isdigit, recipient.phone_number))
            if not phone.startswith('1') and len(phone) == 10:  # US number without country code
                phone = '1' + phone
            
            # Prepare WhatsApp message
            url = f"https://graph.facebook.com/v17.0/{whatsapp_phone_id}/messages"
            
            headers = {
                'Authorization': f'Bearer {whatsapp_token}',
                'Content-Type': 'application/json',
            }
            
            # Use template message for better delivery rates
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {
                    "body": notification_data['message']
                }
            }
            
            response = requests.post(url, headers=headers, json=payload, timeout=10)
            
            if response.status_code == 200:
                logger.info(f"WhatsApp notification sent successfully to {phone}")
                return True
            else:
                logger.error(f"WhatsApp API error: {response.status_code} - {response.text}")
                return False
                
        except Exception as e:
            logger.error(f"Failed to send WhatsApp notification: {str(e)}")
            return False
    
    def _send_push_notification(self, notification_data):
        """Send push notification via Firebase"""
        try:
            recipient = notification_data['recipient']
            device_tokens = DeviceToken.objects.filter(user=recipient)
            
            if not device_tokens.exists():
                return False
            
            if not firebase_admin._apps:
                logger.warning("Firebase not initialized")
                return False
            
            # Prepare FCM message
            fcm_message = messaging.MulticastMessage(
                tokens=[token.token for token in device_tokens],
                notification=messaging.Notification(
                    title=notification_data['title'],
                    body=notification_data['message'][:100] + '...' if len(notification_data['message']) > 100 else notification_data['message']
                ),
                data=notification_data.get('data', {}),
                android=messaging.AndroidConfig(
                    notification=messaging.AndroidNotification(
                        icon='ic_notification',
                        color='#3498db'
                    )
                ),
                apns=messaging.APNSConfig(
                    payload=messaging.APNSPayload(
                        aps=messaging.Aps(
                            badge=1,
                            sound='default'
                        )
                    )
                )
            )
            
            response = messaging.send_multicast(fcm_message)
            
            # Remove invalid tokens
            if response.failure_count > 0:
                failed_tokens = []
                for idx, resp in enumerate(response.responses):
                    if not resp.success:
                        failed_tokens.append(device_tokens[idx].token)
                
                DeviceToken.objects.filter(token__in=failed_tokens).delete()
                logger.info(f"Removed {len(failed_tokens)} invalid device tokens")
            
            if response.success_count > 0:
                logger.info(f"Push notification sent to {response.success_count} devices")
                return True
            
            return False
            
        except Exception as e:
            logger.error(f"Failed to send push notification: {str(e)}")
            return False
    
    def _send_email_notification(self, notification_data):
        """Send email notification"""
        try:
            recipient = notification_data['recipient']
            
            if not recipient.email:
                return False
            
            # Render email template
            context = {
                'user': recipient,
                'message': notification_data['message'],
                'shift': notification_data.get('shift'),
                'title': notification_data['title']
            }
            
            html_message = render_to_string('notifications/shift_notification_email.html', context)
            plain_message = notification_data['message']
            
            send_mail(
                subject=notification_data['title'],
                message=plain_message,
                from_email=settings.DEFAULT_FROM_EMAIL,
                recipient_list=[recipient.email],
                html_message=html_message,
                fail_silently=False
            )
            
            logger.info(f"Email notification sent to {recipient.email}")
            return True
            
        except Exception as e:
            logger.error(f"Failed to send email notification: {str(e)}")
            return False
    
    def _should_send_whatsapp(self, user):
        """Check if WhatsApp notifications should be sent to user"""
        # Check user preferences (you can extend this based on user settings)
        user_preferences = getattr(user, 'notification_preferences', {})
        return user_preferences.get('whatsapp_enabled', True) and hasattr(user, 'phone_number')
    
    def _should_send_email(self, user):
        """Check if email notifications should be sent to user"""
        user_preferences = getattr(user, 'notification_preferences', {})
        return user_preferences.get('email_enabled', True) and user.email
    
    def send_bulk_notifications(self, shifts, notification_type='SHIFT_ASSIGNED'):
        """Send notifications for multiple shifts"""
        results = []
        
        for shift in shifts:
            success, channels = self.send_shift_notification(shift, notification_type)
            results.append({
                'shift_id': shift.id,
                'success': success,
                'channels': channels
            })
        
        return results
    
    def send_custom_notification(self, recipient, message, notification_type='OTHER', channels=None):
        """Send custom notification through specified channels"""
        if channels is None:
            channels = ['app']  # Default to in-app only
        
        notification_data = {
            'recipient': recipient,
            'message': message,
            'notification_type': notification_type,
            'title': 'Notification'
        }
        
        channels_used = []
        
        if 'app' in channels:
            if self._send_in_app_notification(notification_data):
                channels_used.append('app')
        
        if 'whatsapp' in channels and self._should_send_whatsapp(recipient):
            if self._send_whatsapp_notification(notification_data):
                channels_used.append('whatsapp')
        
        if 'push' in channels:
            if self._send_push_notification(notification_data):
                channels_used.append('push')
        
        if 'email' in channels and self._should_send_email(recipient):
            if self._send_email_notification(notification_data):
                channels_used.append('email')
        
        return len(channels_used) > 0, channels_used


# Singleton instance
notification_service = NotificationService()