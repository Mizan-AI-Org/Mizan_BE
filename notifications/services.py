import requests
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

    # ------------------------------------------------------------------------------------
    # SHIFT NOTIFICATIONS
    # ------------------------------------------------------------------------------------
    def send_shift_notification(self, shift, notification_type='SHIFT_ASSIGNED'):
        """Send notification about shift assignment/update"""
        message = ""
        title = "Shift Update"
        
        shift_date_str = shift.shift_date.strftime('%A, %b %d')
        # Handle start_time/end_time being None or not datetime (safety)
        try:
             time_str = f"{shift.start_time.strftime('%H:%M')} - {shift.end_time.strftime('%H:%M')}"
        except Exception:
             time_str = "Time TBD"

        if notification_type == 'SHIFT_ASSIGNED':
            title = "New Shift Assigned"
            message = f"You have been assigned a new shift:\nRole: {shift.get_role_display()}\nDate: {shift_date_str}\nTime: {time_str}"
        elif notification_type == 'SHIFT_UPDATED':
            title = "Shift Updated"
            message = f"Your shift on {shift_date_str} has been updated.\nNew Time: {time_str}"
        elif notification_type == 'SHIFT_CANCELLED':
            title = "Shift Cancelled"
            message = f"Your shift on {shift_date_str} has been cancelled."
            
        return self.send_custom_notification(
            recipient=shift.staff,
            message=message,
            title=title,
            notification_type=notification_type,
            channels=['app', 'whatsapp', 'push']
        )

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
            ok, wamid = self._send_whatsapp_notification(data)
            if ok:
                channels_used.append('whatsapp')
                # Log WhatsApp attempt
                NotificationLog.objects.create(
                    notification=notification,
                    channel='whatsapp',
                    recipient_address=getattr(recipient, 'phone', 'unknown'),
                    status='SENT',
                    external_id=wamid
                )

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
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')
            
            if not token or not phone_id:
                return False, None

            phone = ''.join(filter(str.isdigit, phone))
            url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
            print(f"WhatsApp URL: {url}", flush=True)
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": "hello_world",
                    "language": {"code": "en_US"}
                }
            }
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": "cuntom_template",
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
            
            wamid = None
            if resp.status_code in (200, 201):
                try:
                    wamid = resp.json().get('messages', [{}])[0].get('id')
                except Exception:
                    pass
            
            return resp.status_code == 200, wamid

        except Exception as e:
            logger.error(f"WhatsApp error: {e}")
            print(f"WhatsApp exception: {e}", flush=True)
            return False, None

    def send_whatsapp_invitation(self, phone, first_name, restaurant_name, invite_link, support_contact=None, invitation_token=None):
        """
        Sends a staff invitation via WhatsApp using a Flow or a template+text.
        """
        try:
            clean_phone = ''.join(filter(str.isdigit, str(phone)))
            flow_id = getattr(settings, 'WHATSAPP_INVITATION_FLOW_ID', None)
            
            if flow_id:
                # Use Flow
                header_text = "Welcome to Mizan AI!"
                body_text = f"Hi {first_name}! 👋 {restaurant_name} has invited you to join their team. Click below to get started!"
                return self.send_whatsapp_flow(
                    to_phone=clean_phone,
                    flow_id=flow_id,
                    flow_cta="Join the Team",
                    flow_token=invitation_token or "invitation_flow",
                    header_text=header_text,
                    body_text=body_text,
                    flow_data={
                        "restaurant_name": restaurant_name,
                        "first_name": first_name
                    }
                )

            # Fallback to Template + Text
            template_ok, _ = self.send_whatsapp_template(
                to_phone=clean_phone,
                template_name="hello_world",
                language_code="en_US"
            )
            
            if not template_ok:
                logger.warning(f"Failed to send 'hello_world' template to {clean_phone}")

            # Now send the actual invitation text.
            # Note: This might still fail if the window isn't open, 
            # but the template message above serves as the "primer".
            message = (
                f"Hi {first_name}! 👋 Welcome to the team at *{restaurant_name}*!\n\n"
                f"You've been invited to join us. Mizan AI will be your companion for schedules, tasks, and team updates. 🚀\n\n"
                f"Ready to get started? Tap below to accept your invitation:\n\n"
                f"{invite_link}\n\n"
                f"We're excited to have you with us! ✨"
            )

            if support_contact:
                message += f"\n\nIf you need help, contact support at: {support_contact}"

            ok, wamid = self.send_whatsapp_text(clean_phone, message)
            
            return ok, {"method": "template+text", "wamid": wamid}

        except Exception as e:
            logger.error(f"Failed to send WhatsApp invitation: {e}")
            return False, {"error": str(e)}

    def send_whatsapp_template(self, to_phone, template_name, language_code="en_US", components=None):
        """
        Sends a WhatsApp Message Template. Required for initiating conversations (first contact).
        """
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')

            if not token or not phone_id:
                return False, None

            phone = ''.join(filter(str.isdigit, str(to_phone)))
            url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
            
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "template",
                "template": {
                    "name": template_name,
                    "language": {
                        "code": language_code
                    }
                }
            }
            
            if components:
                payload["template"]["components"] = components

            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            
            if resp.status_code not in (200, 201):
                logger.error(f"Meta Template Error: {resp.status_code} - {resp.text}")
                print(f"❌ Meta Template Error: {resp.status_code} - {resp.text}", file=sys.stderr)
            
            wamid = None
            if resp.status_code in (200, 201):
                try:
                    wamid = resp.json().get('messages', [{}])[0].get('id')
                except Exception:
                    pass

            return resp.status_code in (200, 201), wamid
        except Exception as e:
            logger.error(f"Exception in send_whatsapp_template: {e}")
            return False, None

    def send_whatsapp_text(self, to_phone, body):
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')
            if not token or not phone_id:
                return False, None
            phone = ''.join(filter(str.isdigit, str(to_phone)))
            url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "text": {"preview_url": False, "body": str(body)}
            }
            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            if resp.status_code not in (200, 201):
                logger.error(f"Meta Text Error: {resp.status_code} - {resp.text}")
                print(f"❌ Meta Text Error: {resp.status_code} - {resp.text}", file=sys.stderr)
            
            wamid = None
            if resp.status_code in (200, 201):
                try:
                    wamid = resp.json().get('messages', [{}])[0].get('id')
                except Exception:
                    pass

            return resp.status_code in (200, 201), wamid
        except Exception as e:
            logger.error(f"Exception in send_whatsapp_text: {e}")
            return False, None
    
    def send_whatsapp_buttons(self, to_phone, body_text, buttons):
        """Sends native WhatsApp quick reply buttons (max 3)."""
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')
            if not token or not phone_id:
                return False, None
            phone = ''.join(filter(str.isdigit, str(to_phone)))
            url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
            
            action_buttons = []
            for btn in buttons[:3]:
                action_buttons.append({
                    "type": "reply",
                    "reply": {
                        "id": str(btn.get("id")),
                        "title": str(btn.get("title"))[:20]  # Title limit is 20 chars
                    }
                })

            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "button",
                    "body": {"text": str(body_text)},
                    "action": {"buttons": action_buttons}
                }
            }
            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            if resp.status_code not in (200, 201):
                logger.error(f"WhatsApp Buttons Error: {resp.status_code} - {resp.text}")
            
            wamid = None
            if resp.status_code in (200, 201):
                try:
                    wamid = resp.json().get('messages', [{}])[0].get('id')
                except Exception:
                    pass

            return resp.status_code in (200, 201), wamid
        except Exception as e:
            logger.error(f"Exception in send_whatsapp_buttons: {e}")
            return False, None

    def send_whatsapp_location_request(self, to_phone, body_text):
        """Sends a native WhatsApp request for live/current location."""
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')
            if not token or not phone_id:
                return False, None
            phone = ''.join(filter(str.isdigit, str(to_phone)))
            url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
            
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "location_request_message",
                    "body": {"text": str(body_text)},
                    "action": {"name": "send_location"}
                }
            }
            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            if resp.status_code not in (200, 201):
                logger.error(f"WhatsApp Location Request Error: {resp.status_code} - {resp.text}")
            
            wamid = None
            if resp.status_code in (200, 201):
                try:
                    wamid = resp.json().get('messages', [{}])[0].get('id')
                except Exception:
                    pass

            return resp.status_code in (200, 201), wamid
        except Exception as e:
            logger.error(f"Exception in send_whatsapp_location_request: {e}")
            return False, None
    
    def send_whatsapp_flow(self, to_phone, flow_id, flow_cta, flow_token="unused", screen_id="WELCOME", flow_data=None, header_text="", body_text="", footer_text=""):
        """Sends a Meta WhatsApp Flow message."""
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            phone_id = getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')
            if not token or not phone_id:
                return False, None
            
            phone = ''.join(filter(str.isdigit, str(to_phone)))
            url = f"https://graph.facebook.com/{api_version}/{phone_id}/messages"
            
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "flow",
                    "header": {"type": "text", "text": header_text} if header_text else None,
                    "body": {"text": body_text},
                    "footer": {"text": footer_text} if footer_text else None,
                    "action": {
                        "name": "flow",
                        "parameters": {
                            "flow_token": flow_token,
                            "flow_id": flow_id,
                            "flow_cta": flow_cta,
                            "flow_action": "navigate",
                            "flow_action_payload": {
                                "screen": screen_id,
                                "data": flow_data or {}
                            }
                        }
                    }
                }
            }
            # Remove None values
            if not payload["interactive"]["header"]: payload["interactive"].pop("header")
            if not payload["interactive"]["footer"]: payload["interactive"].pop("footer")

            resp = requests.post(
                url,
                headers={'Authorization': f"Bearer {token}"},
                json=payload
            )
            
            wamid = None
            if resp.status_code in (200, 201):
                try:
                    wamid = resp.json().get('messages', [{}])[0].get('id')
                except Exception:
                    pass
            else:
                logger.error(f"WhatsApp Flow Error: {resp.status_code} - {resp.text}")

            return resp.status_code in (200, 201), wamid
        except Exception as e:
            logger.error(f"Exception in send_whatsapp_flow: {e}")
            return False, None
    
    def fetch_whatsapp_media_url(self, media_id):
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            api_version = getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')
            if not token:
                return None
            url = f"https://graph.facebook.com/{api_version}/{media_id}"
            r = requests.get(url, headers={'Authorization': f"Bearer {token}"})
            if r.status_code == 200:
                data = r.json()
                return data.get('url')
            return None
        except Exception:
            return None
    
    def download_media_bytes(self, media_url):
        try:
            token = getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)
            if not token or not media_url:
                return None
            r = requests.get(media_url, headers={'Authorization': f"Bearer {token}"})
            if r.status_code == 200:
                return r.content
            return None
        except Exception:
            return None
    
    def transcribe_audio_bytes(self, audio_bytes, filename="audio.ogg"):
        try:
            api_key = getattr(settings, 'OPENAI_API_KEY', '')
            if not api_key or not audio_bytes:
                return None
            import requests as _rq
            files = {
                'file': (filename, audio_bytes, 'audio/ogg')
            }
            data = {
                'model': 'whisper-1'
            }
            headers = {
                'Authorization': f'Bearer {api_key}'
            }
            resp = _rq.post('https://api.openai.com/v1/audio/transcriptions', headers=headers, data=data, files=files)
            if resp.status_code == 200:
                j = resp.json()
                return j.get('text')
            return None
        except Exception:
            return None
    
    def send_lua_incident(self, user, description, metadata=None):
        try:
            api_url = getattr(settings, 'LUA_API_URL', 'https://api.heylua.ai')
            agent_id = getattr(settings, 'LUA_AGENT_ID', '')
            api_key = getattr(settings, 'LUA_WEBHOOK_API_KEY', '')
            if not agent_id or not api_key:
                return False, None
            role_map = {
                'WAITER': 'server',
                'SERVER': 'server',
                'MANAGER': 'manager',
                'CHEF': 'chef',
                'COOK': 'cook',
                'BARTENDER': 'bartender',
                'HOST': 'host',
                'BUSSER': 'busser',
                'DISHWASHER': 'dishwasher',
            }
            lua_role = role_map.get(getattr(user, 'role', '') or '', 'server')
            url = f"{api_url}/webhooks/{agent_id}/staff-management-events"
            body = {
                "eventType": "incident_reported",
                "staffId": str(user.id),
                "staffName": user.get_full_name() or user.email,
                "role": lua_role,
                "details": {
                    "incidentDescription": str(description)
                },
                "metadata": metadata or {},
                "timestamp": timezone.now().isoformat()
            }
            headers = {
                "x-api-key": api_key,
                "x-role": lua_role,
                "content-type": "application/json"
            }
            r = requests.post(url, json=body, headers=headers, timeout=15)
            try:
                j = r.json()
            except Exception:
                j = None
            return r.status_code in (200, 201), j
        except Exception:
            return False, None

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
