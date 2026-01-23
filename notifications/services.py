import requests
import re
from django.conf import settings
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.mail import send_mail
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification, DeviceToken, NotificationLog
import firebase_admin
from firebase_admin import messaging
import logging, sys

logger = logging.getLogger(__name__)


class NotificationService:
    """Unified notification service (NO DUPLICATES)"""

    def __init__(self):
        self.channel_layer = get_channel_layer()

    def send_shift_notification(self, shift, notification_type='SHIFT_ASSIGNED'):
        """Helper to send shift-related notifications"""
        try:
            recipient = shift.staff
            if not recipient:
                return False
                
            title = "Shift Update"
            message = ""
            channels = ['app', 'push']
            
            start_str = shift.start_time.strftime('%a, %b %d at %H:%M') if shift.start_time else "Unknown time"
            
            if notification_type == 'SHIFT_ASSIGNED':
                title = "New Shift Assigned"
                message = f"You have been assigned a new shift on {start_str}."
                channels.append('email')
                channels.append('whatsapp')
            elif notification_type == 'SHIFT_UPDATED':
                title = "Shift Updated"
                message = f"Your shift on {start_str} has been updated."
            elif notification_type == 'SHIFT_CANCELLED':
                title = "Shift Cancelled"
                message = f"Your shift on {start_str} has been cancelled."
                channels.append('email')
                channels.append('whatsapp')
            elif notification_type == 'SHIFT_REMINDER':
                title = "Upcoming Shift Reminder"
                message = f"Reminder: You have a shift starting soon on {start_str}."
                channels.append('whatsapp')
                
            return self.send_custom_notification(
                recipient=recipient,
                message=message,
                notification_type=notification_type,
                title=title,
                channels=channels
            )
        except Exception as e:
            logger.error(f"Error sending shift notification: {e}")
            return False

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

    # ------------------------------------------------------------------------------------
    # LUA AGENT INTEGRATION
    # ------------------------------------------------------------------------------------

    def send_lua_staff_invite(self, invitation_token, phone, first_name, restaurant_name, invite_link):
        """
        Notify Lua agent about a new staff invitation.
        This triggers Miya to send a WhatsApp template message.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "2c4e416e-3203-4a75-8c96-c1af923f85bd"  # staff-management-events
            url = f"https://api.heylua.ai/developer/webhooks/{LUA_AGENT_ID}/{webhook_id}"
            
            payload = {
                "eventType": "staff_invite",
                "staffId": "invitation_" + str(invitation_token)[:8],
                "staffName": first_name,
                "role": "server", # Default role for invite logic
                "details": {
                    "phone": phone,
                    "inviteLink": invite_link,
                    "restaurantName": restaurant_name,
                    "invitationToken": invitation_token
                },
                "timestamp": timezone.now().isoformat()
            }
            
            headers = {
                "Content-Type": "application/json",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": "manager"
            }
            
            logger.info(f"[LuaInvite] Calling webhook for {first_name} at {url}")
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            
            if resp.status_code in (200, 201):
                return True, resp.json()
            else:
                logger.warning(f"[LuaInvite] Failed: {resp.status_code} - {resp.text}")
                return False, {"error": resp.text, "status_code": resp.status_code}
                
        except Exception as e:
            logger.error(f"[LuaInvite] Unexpected error: {str(e)}")
            return False, {"error": str(e)}

    def send_lua_invitation_accepted(self, invitation_token, phone, first_name, flow_data=None):
        """
        Notify Lua agent that an invitation was accepted.
        This allows Miya to send a 'Welcome' message with the staff person's PIN.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "2c4e416e-3203-4a75-8c96-c1af923f85bd"  # staff-management-events
            url = f"https://api.heylua.ai/developer/webhooks/{LUA_AGENT_ID}/{webhook_id}"
            
            payload = {
                "eventType": "staff_invitation_accepted",
                "staffId": "invitation_" + str(invitation_token)[:8],
                "staffName": first_name,
                "role": "server",
                "details": {
                    "phoneNumber": phone,
                    "invitationToken": invitation_token,
                    "flowData": flow_data or {}
                },
                "timestamp": timezone.now().isoformat()
            }
            
            headers = {
                "Content-Type": "application/json",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": "manager"
            }
            
            logger.info(f"[LuaAccept] Calling webhook for {first_name} at {url}")
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            
            if resp.status_code in (200, 201):
                return True, resp.json()
            else:
                logger.warning(f"[LuaAccept] Failed: {resp.status_code} - {resp.text}")
                return False, {"error": resp.text, "status_code": resp.status_code}
                
        except Exception as e:
            logger.error(f"[LuaAccept] Unexpected error: {str(e)}")
            return False, {"error": str(e)}

    def send_lua_incident(self, user, description, metadata=None):
        """
        Forward incident report to Lua agent for analysis.
        This allows Miya to analyze and respond to incidents.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "2c4e416e-3203-4a75-8c96-c1af923f85bd"  # staff-management-events
            url = f"https://api.heylua.ai/developer/webhooks/{LUA_AGENT_ID}/{webhook_id}"
            
            # Normalize role for the webhook
            user_role = getattr(user, 'role', 'server')
            if user_role:
                user_role = user_role.lower()
            else:
                user_role = 'server'
            
            payload = {
                "eventType": "incident_reported",
                "staffId": str(user.id),
                "staffName": user.get_full_name(),
                "role": user_role,
                "details": {
                    "incidentDescription": description,
                    **(metadata or {})
                },
                "timestamp": timezone.now().isoformat()
            }
            
            headers = {
                "Content-Type": "application/json",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": user_role
            }
            
            logger.info(f"[LuaIncident] Calling webhook for {user.get_full_name()} at {url}")
            resp = requests.post(url, json=payload, headers=headers, timeout=5)
            
            if resp.status_code in (200, 201):
                return True, resp.json()
            else:
                logger.warning(f"[LuaIncident] Failed: {resp.status_code} - {resp.text}")
                return False, {"error": resp.text, "status_code": resp.status_code}
                
        except Exception as e:
            logger.error(f"[LuaIncident] Unexpected error: {str(e)}")
            return False, {"error": str(e)}

    # ====================================================================================
    # INTERNAL METHODS (UNCHANGED EXCEPT NEVER CREATE A NOTIFICATION TWICE)
    # ====================================================================================
    def _send_in_app_notification(self, notification_data, existing_notification=None):
        """WebSocket real-time event without creating duplicate notifications."""
        try:
            print("Sending in-app notification...", flush=True, file=sys.stderr)
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
            print(f"Group: {group}", flush=True, file=sys.stderr)
            print(f"current user: {notification.recipient}", flush=True, file=sys.stderr)
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
        print(f"data for WhatsApp: {data}", flush=True, file=sys.stderr)
        try:
            recipient = data['recipient']
            phone = getattr(recipient, 'phone', None)
            title = data['title']
            message = data['message']
            # phone = getattr(recipient, 'phone', None)
            print(f"Recipient object: {recipient}", flush=True, file=sys.stderr)
            if not phone:
                return False
            print(f"Recipient phone: {phone}", flush=True, file=sys.stderr)
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            
            if not token or not phone_id:
                return False

            phone = ''.join(filter(str.isdigit, phone))
            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            print(f"WhatsApp URL: {url}", flush=True)
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": getattr(settings, 'WHATSAPP_TEMPLATE_INVITE', 'onboarding_invite_v1'),
                    "language": {"code": "en_US"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": title},
                                {"type": "text", "text": message}
                            ]
                        }
                    ]
                }
            }

            print(f"WhatsApp payload: {payload}", flush=True)
            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            print(f"WhatsApp response: {resp.status_code} - {resp.text}", flush=True)
            return resp.status_code == 200

        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            print(f"WhatsApp exception: {e}", flush=True)
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

    def send_whatsapp_invitation(self, phone, first_name, restaurant_name, invite_link, support_contact):
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            if not token or not phone_id or not phone:
                return False, None
            phone = ''.join(filter(str.isdigit, phone))
            default_cc = getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '')
            if phone.startswith('0'):
                phone = phone.lstrip('0')
            if not re.match(r"^\d{10,15}$", phone):
                if default_cc and re.match(r"^\d{9,14}$", phone):
                    phone = f"{default_cc}{phone}"
            if not re.match(r"^\d{10,15}$", phone):
                return False, {"error": "Invalid recipient phone format"}
            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            template_name = getattr(settings, 'WHATSAPP_TEMPLATE_INVITE', 'onboarding_invite_v1')
            brand = getattr(settings, 'WHATSAPP_BRAND_NAME', 'Mizan AI')
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": "en_US"},
                    "components": [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": brand},
                                {"type": "text", "text": first_name or ''},
                                {"type": "text", "text": invite_link},
                                {"type": "text", "text": support_contact or ''},
                            ]
                        }
                    ]
                }
            }
            resp = requests.post(url, headers={'Authorization': f"Bearer {token}"}, json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {"error": resp.text}
            ok = resp.status_code == 200
            external_id = None
            if isinstance(data, dict):
                external_id = str(data.get('messages', [{}])[0].get('id')) if data.get('messages') else None
            return ok, {"status_code": resp.status_code, "data": data, "external_id": external_id}
        except Exception as e:
            logger.error(f"WhatsApp invitation error: {e}")
            return False, {"error": str(e)}

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
