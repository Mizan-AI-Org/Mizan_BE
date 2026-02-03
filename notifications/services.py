import requests, json
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
import tempfile
import shutil
import subprocess

logger = logging.getLogger(__name__)
from core.i18n import get_effective_language, whatsapp_language_code, tr


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
            restaurant = getattr(getattr(shift, 'schedule', None), 'restaurant', None) or getattr(recipient, 'restaurant', None)
            lang = get_effective_language(user=recipient, restaurant=restaurant)
                
            title = "Shift Update"
            message = ""
            channels = ['app', 'push']
            
            start_str = shift.start_time.strftime('%a, %b %d at %H:%M') if shift.start_time else "Unknown time"
            
            if notification_type == 'SHIFT_ASSIGNED':
                title = tr("notify.shift.assigned.title", lang)
                message = tr("notify.shift.assigned.body", lang, start=start_str)
                channels.append('email')
                channels.append('whatsapp')
            elif notification_type == 'SHIFT_UPDATED':
                title = tr("notify.shift.updated.title", lang)
                message = tr("notify.shift.updated.body", lang, start=start_str)
            elif notification_type == 'SHIFT_CANCELLED':
                title = tr("notify.shift.cancelled.title", lang)
                message = tr("notify.shift.cancelled.body", lang, start=start_str)
                channels.append('email')
                channels.append('whatsapp')
            elif notification_type == 'SHIFT_REMINDER':
                title = tr("notify.shift.reminder.title", lang)
                message = tr("notify.shift.reminder.body", lang, start=start_str)
                channels.append('whatsapp')
                
                # Use dedicated WhatsApp template if possible
                phone = getattr(recipient, 'phone', None)
                if 'whatsapp' in channels and phone:
                    template_name = getattr(settings, 'WHATSAPP_TEMPLATE_CLOCK_IN_REMINDER', 'clock_in_reminder')
                    duration = shift.get_shift_duration_hours()
                    duration_str = f"{duration:.1f} hours" if duration % 1 != 0 else f"{int(duration)} hours"
                    
                    components = [
                        {
                            "type": "body",
                            "parameters": [
                                {"type": "text", "text": recipient.first_name or "Staff"},
                                {"type": "text", "text": "30 minutes"},
                                {"type": "text", "text": shift.workspace_location or restaurant.name if restaurant else "Restaurant"},
                                {"type": "text", "text": shift.notes or "Your Shift"},
                                {"type": "text", "text": duration_str},
                            ]
                        }
                    ]
                    # We send the template directly and remove 'whatsapp' from channels for send_custom_notification
                    ok, _ = self.send_whatsapp_template(
                        phone=phone,
                        template_name=template_name,
                        language_code=whatsapp_language_code(lang),
                        components=components
                    )
                    if ok:
                        channels.remove('whatsapp')

            elif notification_type == 'CLOCK_IN_REMINDER':
                title = tr("notify.shift.clockin_reminder.title", lang)
                message = tr("notify.shift.clockin_reminder.body", lang, start="10 minutes")
                channels.append('whatsapp')

                phone = getattr(recipient, 'phone', None)
                if 'whatsapp' in channels and phone:
                    body = (
                        f"⏰ *Clock-In Reminder*\n\n"
                        f"Hi {recipient.first_name or 'there'}, your shift starts in *10 minutes*.\n\n"
                        f"Please tap the button below to start your Clock-In process and verify your location."
                    )
                    buttons = [
                        {"id": "clock_in_now", "title": "📍 Clock-In Now"}
                    ]
                    ok, _ = self.send_whatsapp_buttons(phone, body, buttons)
                    if ok:
                        channels.remove('whatsapp')

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

    def _normalize_phone(self, phone):
        """Normalize phone number to digits only for Lua Agent."""
        if not phone:
            return ""
        return ''.join(filter(str.isdigit, str(phone)))

    # ------------------------------------------------------------------------------------
    # LUA AGENT INTEGRATION
    # ------------------------------------------------------------------------------------

    def send_lua_staff_invite(self, invitation_token, phone, first_name, restaurant_name, invite_link, role='staff', language='en'):
        """
        Notify Lua agent about a new staff invitation.
        This triggers Miya to send a WhatsApp template message.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"  # user-events-production
            
            url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
            if not url:
                url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}"
            
            normalized_phone = self._normalize_phone(phone)
            
            payload = {
                "eventType": "staff_invite",
                "staffId": f"invite_{str(invitation_token)[:8]}",
                "staffName": first_name or "New Staff",
                "role": role.lower() if role else "staff",
                "details": {
                    "phone": normalized_phone,
                    "phoneNumber": normalized_phone, # Add for compatibility with different agent handlers
                    "inviteLink": invite_link,
                    "restaurantName": restaurant_name,
                    "invitationToken": invitation_token,
                    # Let Miya send the invite in the right language
                    "language": get_effective_language(user=None, restaurant=None, fallback=language),
                },
                "timestamp": timezone.now().isoformat()
            }

            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lua_api_key}",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": "manager"
            }
            
            print(f"[LuaInvite] Calling webhook for {first_name} at {url}", file=sys.stderr)
            print(f"[LuaInvite] Payload: {json.dumps(payload)}", file=sys.stderr)
            resp = requests.post(url, json=payload, headers=headers, timeout=10)
            print(f"[LuaInvite] Response: {resp.status_code} - {resp.text}", file=sys.stderr)

            try:
                info = resp.json()
            except Exception:
                info = {"error": "Invalid JSON response", "raw": resp.text}

            if resp.status_code in (200, 201):
                return True, info
            else:
                logger.warning(f"[LuaInvite] Failed: {resp.status_code} - {resp.text}")
                return False, {"error": resp.text, "status_code": resp.status_code, "info": info}
                
        except Exception as e:
            logger.error(f"[LuaInvite] Unexpected error: {str(e)}", exc_info=True)
            return False, {"error": str(e)}

    def send_lua_invitation_accepted(self, invitation_token, phone, first_name, flow_data=None, language='en'):
        """
        Notify Lua agent that an invitation was accepted.
        This allows Miya to send a 'Welcome' message with the staff person's PIN.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"  # user-events-production
            url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}"
            
            payload = {
                "eventType": "staff_invitation_accepted",
                "staffId": "invitation_" + str(invitation_token)[:8],
                "staffName": first_name,
                "role": "server",
                "details": {
                    "phoneNumber": self._normalize_phone(phone),
                    "invitationToken": invitation_token,
                    "flowData": flow_data or {},
                    "language": get_effective_language(user=None, restaurant=None, fallback=language),
                },
                "timestamp": timezone.now().isoformat()
            }
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lua_api_key}",
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
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"  # user-events-production
            url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}"
            
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
                    "phone": self._normalize_phone(getattr(user, 'phone', None)),
                    **(metadata or {})
                },
                "timestamp": timezone.now().isoformat()
            }
            
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lua_api_key}",
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
            from .models import NotificationLog
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

            phone_digits = ''.join(filter(str.isdigit, phone))
            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            print(f"WhatsApp URL: {url}", flush=True)

            # For general notifications, use plain text (templates are handled by dedicated methods)
            body = message or ""
            if title:
                body = f"*{title}*\n\n{body}".strip()

            payload = {
                "messaging_product": "whatsapp",
                "to": phone_digits,
                "type": "text",
                "text": {"body": body}
            }

            print(f"WhatsApp payload: {payload}", flush=True)
            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            print(f"WhatsApp response: {resp.status_code} - {resp.text}", flush=True)
            ok = resp.status_code == 200
            external_id = None
            response_data = {}
            try:
                response_data = resp.json()
                if isinstance(response_data, dict):
                    external_id = str(response_data.get('messages', [{}])[0].get('id')) if response_data.get('messages') else None
            except Exception:
                response_data = {"raw": resp.text}

            # Audit log
            try:
                NotificationLog.objects.create(
                    notification=data.get('notification'),
                    channel='whatsapp',
                    recipient_address=phone_digits,
                    status='SENT' if ok else 'FAILED',
                    external_id=external_id,
                    response_data=response_data,
                    error_message=None if ok else resp.text[:500]
                )
            except Exception:
                pass

            return ok

        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            print(f"WhatsApp exception: {e}", flush=True)
            # Best-effort audit log
            try:
                from .models import NotificationLog
                recipient = data.get('recipient')
                phone = getattr(recipient, 'phone', None) if recipient else None
                phone_digits = ''.join(filter(str.isdigit, str(phone or '')))
                NotificationLog.objects.create(
                    notification=data.get('notification'),
                    channel='whatsapp',
                    recipient_address=phone_digits or (str(phone) if phone else ''),
                    status='FAILED',
                    response_data={},
                    error_message=str(e)[:500]
                )
            except Exception:
                pass
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

    def send_whatsapp_text(self, phone, body, notification=None):
        """Send a plain text WhatsApp message via Meta Cloud API"""
        try:
            from .models import NotificationLog
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            if not token or not phone_id or not phone:
                return False, {"error": "WhatsApp not configured"}
            
            phone = ''.join(filter(str.isdigit, phone))
            default_cc = getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '')
            if phone.startswith('0'):
                phone = phone.lstrip('0')
            if not re.match(r"^\d{10,15}$", phone):
                if default_cc and re.match(r"^\d{9,14}$", phone):
                    phone = f"{default_cc}{phone}"
            if not re.match(r"^\d{10,15}$", phone):
                return False, {"error": "Invalid phone format"}
            
            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "text",
                "text": {"body": body}
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

            # Audit log (best-effort)
            try:
                NotificationLog.objects.create(
                    notification=notification,
                    channel='whatsapp',
                    recipient_address=phone,
                    status='SENT' if ok else 'FAILED',
                    external_id=external_id,
                    response_data=data if isinstance(data, dict) else {"raw": str(data)},
                    error_message=None if ok else str(data)[:500],
                )
            except Exception:
                pass

            return ok, {"status_code": resp.status_code, "data": data, "external_id": external_id}
        except Exception as e:
            logger.error(f"WhatsApp text error: {e}")
            try:
                from .models import NotificationLog
                NotificationLog.objects.create(
                    notification=notification,
                    channel='whatsapp',
                    recipient_address=''.join(filter(str.isdigit, str(phone or ''))),
                    status='FAILED',
                    response_data={},
                    error_message=str(e)[:500],
                )
            except Exception:
                pass
            return False, {"error": str(e)}

    def send_whatsapp_template(self, phone, template_name, language_code='en_US', components=None, notification=None):
        """Send a WhatsApp template message via Meta Cloud API"""
        try:
            from .models import NotificationLog
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            if not token or not phone_id or not phone:
                return False, {"error": "WhatsApp not configured"}
            
            phone = ''.join(filter(str.isdigit, phone))
            default_cc = getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '')
            if phone.startswith('0'):
                phone = phone.lstrip('0')
            if not re.match(r"^\d{10,15}$", phone):
                if default_cc and re.match(r"^\d{9,14}$", phone):
                    phone = f"{default_cc}{phone}"
            if not re.match(r"^\d{10,15}$", phone):
                return False, {"error": "Invalid phone format"}
            
            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {"code": language_code},
                    "components": components or []
                }
            }
            
            logger.info(f"Sending WhatsApp template '{template_name}' to {phone}")
            resp = requests.post(url, headers={'Authorization': f"Bearer {token}"}, json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {"error": resp.text}
            
            ok = resp.status_code == 200
            if not ok:
                logger.warning(f"WhatsApp template failed: {resp.status_code} - {data}")
            external_id = None
            if isinstance(data, dict):
                external_id = str(data.get('messages', [{}])[0].get('id')) if data.get('messages') else None

            # Audit log
            try:
                NotificationLog.objects.create(
                    notification=notification,
                    channel='whatsapp',
                    recipient_address=phone,
                    status='SENT' if ok else 'FAILED',
                    external_id=external_id,
                    response_data=data if isinstance(data, dict) else {"raw": str(data)},
                    error_message=None if ok else str(data)[:500],
                )
            except Exception:
                pass

            return ok, {"status_code": resp.status_code, "data": data, "external_id": external_id}
        except Exception as e:
            logger.error(f"WhatsApp template error: {e}")
            try:
                from .models import NotificationLog
                NotificationLog.objects.create(
                    notification=notification,
                    channel='whatsapp',
                    recipient_address=''.join(filter(str.isdigit, str(phone or ''))),
                    status='FAILED',
                    response_data={},
                    error_message=str(e)[:500],
                )
            except Exception:
                pass
            return False, {"error": str(e)}

    def send_whatsapp_buttons(self, phone, body, buttons):
        """
        Send an interactive WhatsApp message with up to 3 quick-reply buttons.
        buttons: [{ "id": "yes", "title": "✅ Yes" }, ...]
        """
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            if not token or not phone_id or not phone:
                return False, {"error": "WhatsApp not configured"}

            phone = ''.join(filter(str.isdigit, phone))
            default_cc = getattr(settings, 'WHATSAPP_DEFAULT_COUNTRY_CODE', '')
            if phone.startswith('0'):
                phone = phone.lstrip('0')
            if not re.match(r"^\d{10,15}$", phone):
                if default_cc and re.match(r"^\d{9,14}$", phone):
                    phone = f"{default_cc}{phone}"
            if not re.match(r"^\d{10,15}$", phone):
                return False, {"error": "Invalid phone format"}

            btns = list(buttons or [])[:3]
            action_buttons = []
            for b in btns:
                bid = str(b.get('id') or '')[:256]
                title = str(b.get('title') or '')[:20]  # WhatsApp limit
                if not bid or not title:
                    continue
                action_buttons.append({
                    "type": "reply",
                    "reply": {"id": bid, "title": title}
                })

            if not action_buttons:
                # Fallback to plain text
                return self.send_whatsapp_text(phone, body)

            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": body},
                    "action": {"buttons": action_buttons}
                }
            }
            resp = requests.post(url, headers={'Authorization': f"Bearer {token}"}, json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {"error": resp.text}
            ok = resp.status_code == 200
            return ok, {"status_code": resp.status_code, "data": data}
        except Exception as e:
            logger.error(f"WhatsApp buttons error: {e}")
            return False, {"error": str(e)}

    def send_whatsapp_location_request(self, phone, body):
        """
        Prefer a pre-approved template that asks for live location.
        Falls back to plain text prompt.
        """
        try:
            # If you have a Meta template for this, use it:
            ok, resp = self.send_whatsapp_template(
                phone=phone,
                template_name='clock_in_location_request',
                language_code='en_US',
                components=[]
            )
            if ok:
                return ok, resp
        except Exception:
            pass
        return self.send_whatsapp_text(phone, body)

    # ----------------------------------------------------------------------
    # WHATSAPP MEDIA + VOICE NOTE TRANSCRIPTION
    # ----------------------------------------------------------------------

    def fetch_whatsapp_media_url(self, media_id):
        """
        Fetch a temporary download URL for a WhatsApp media_id.
        https://developers.facebook.com/docs/whatsapp/cloud-api/reference/media
        """
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            if not token or not media_id:
                return None, None

            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{media_id}"
            resp = requests.get(url, headers={'Authorization': f"Bearer {token}"}, timeout=10)
            if resp.status_code != 200:
                logger.warning(f"WhatsApp media lookup failed: {resp.status_code} - {resp.text}")
                return None, None

            data = resp.json()
            return data.get('url'), data.get('mime_type')
        except Exception as e:
            logger.error(f"fetch_whatsapp_media_url error: {e}")
            return None, None

    def download_media_bytes(self, media_url):
        """Download media bytes from a WhatsApp media URL."""
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            if not token or not media_url:
                return None

            resp = requests.get(media_url, headers={'Authorization': f"Bearer {token}"}, timeout=30)
            if resp.status_code != 200:
                logger.warning(f"WhatsApp media download failed: {resp.status_code} - {resp.text[:200]}")
                return None
            return resp.content
        except Exception as e:
            logger.error(f"download_media_bytes error: {e}")
            return None

    def transcribe_audio_bytes(self, audio_bytes, input_mime_type=None):
        """
        Transcribe voice-note audio bytes.

        Current implementation uses OpenAI Whisper (`whisper-1`) via REST.
        If the incoming audio is OGG/OPUS (common for WhatsApp), we attempt to convert
        to WAV using ffmpeg when available.
        """
        if not audio_bytes:
            return None

        api_key = getattr(settings, 'OPENAI_API_KEY', '') or ''
        if not api_key:
            logger.warning("OPENAI_API_KEY not configured; skipping transcription")
            return None

        tmp_in = None
        tmp_out = None
        try:
            # Write input audio to temp file
            tmp_in = tempfile.NamedTemporaryFile(delete=False, suffix='.ogg')
            tmp_in.write(audio_bytes)
            tmp_in.flush()
            tmp_in.close()

            audio_path = tmp_in.name

            # Convert if needed/possible (WhatsApp often sends audio/ogg; codecs=opus)
            ffmpeg = shutil.which('ffmpeg')
            if ffmpeg:
                tmp_out = tempfile.NamedTemporaryFile(delete=False, suffix='.wav')
                tmp_out.close()
                out_path = tmp_out.name

                # -y overwrite, mono 16k improves STT reliability
                cmd = [ffmpeg, '-y', '-i', audio_path, '-ac', '1', '-ar', '16000', out_path]
                try:
                    subprocess.run(cmd, check=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    audio_path = out_path
                except Exception as e:
                    logger.warning(f"ffmpeg convert failed; falling back to original bytes: {e}")
            else:
                logger.info("ffmpeg not found; sending raw audio bytes to STT provider")

            stt_url = "https://api.openai.com/v1/audio/transcriptions"
            headers = {"Authorization": f"Bearer {api_key}"}
            with open(audio_path, 'rb') as f:
                files = {
                    'file': (audio_path.split('/')[-1], f),
                }
                data = {
                    'model': 'whisper-1',
                    'response_format': 'json',
                }
                resp = requests.post(stt_url, headers=headers, files=files, data=data, timeout=60)
            if resp.status_code != 200:
                logger.warning(f"STT failed: {resp.status_code} - {resp.text[:300]}")
                return None

            payload = resp.json()
            text = payload.get('text')
            if text:
                text = str(text).strip()
            return text or None
        except Exception as e:
            logger.error(f"transcribe_audio_bytes error: {e}")
            return None
        finally:
            # Cleanup temp files
            try:
                if tmp_in and tmp_in.name:
                    shutil.os.unlink(tmp_in.name)
            except Exception:
                pass
            try:
                if tmp_out and tmp_out.name:
                    shutil.os.unlink(tmp_out.name)
            except Exception:
                pass

    # ----------------------------------------------------------------------
    # PREFERENCE HELPERS
    # ----------------------------------------------------------------------

    def _should_send_whatsapp(self, user):
        pref = getattr(user, 'notification_preferences', None) or getattr(user, 'notification_preference', None)
        return not pref or pref.whatsapp_enabled

    def _should_send_email(self, user):
        pref = getattr(user, 'notification_preferences', None) or getattr(user, 'notification_preference', None)
        return not pref or pref.email_enabled

    
# SINGLETON INSTANCE
notification_service = NotificationService()

