import requests, json
import re
from django.conf import settings
from django.utils import timezone
from django.template.loader import render_to_string
from django.core.mail import send_mail
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification, DeviceToken, NotificationLog, WhatsAppSession
import firebase_admin
from firebase_admin import messaging
import logging, sys
import tempfile
import shutil
import subprocess

logger = logging.getLogger(__name__)

# Lua webhooks may take time (template lookup + WhatsApp API). Use a generous timeout.
LUA_WEBHOOK_TIMEOUT = 25


class NotificationService:
    """Unified notification service (NO DUPLICATES)"""

    def __init__(self):
        self.channel_layer = get_channel_layer()

    def send_shift_notification(self, shift, notification_type='SHIFT_ASSIGNED', recipient=None):
        """Helper to send shift-related notifications. Optional recipient overrides shift.staff (e.g. when notifying each staff_member)."""
        try:
            recipient = recipient or shift.staff
            if not recipient:
                return False
                
            title = "Shift Update"
            message = ""
            channels = ['app', 'push']
            
            start_str = shift.start_time.strftime('%a, %b %d at %H:%M') if shift.start_time else "Unknown time"
            
            if notification_type == 'SHIFT_ASSIGNED':
                title = "New Shift Assigned"
                message = f"You have been assigned a new shift on {start_str}."
                # Staff are notified via WhatsApp only (not email) for scheduled shifts.
                channels.append('whatsapp')
                # Prefer Miya (Lua) to send the WhatsApp so the message comes from the assistant
                lua_url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
                lua_agent_id = getattr(settings, 'LUA_AGENT_ID', None)
                staff = recipient  # use passed recipient (e.g. when notifying each staff_member)
                if (lua_url or lua_agent_id) and getattr(staff, 'phone', None):
                    ok, _ = self.send_lua_shift_assigned(
                        phone=staff.phone,
                        first_name=staff.first_name or "Team Member",
                        start_str=start_str,
                        message=message,
                        shift_id=getattr(shift, 'id', None),
                    )
                    if ok:
                        pass  # Miya sent WhatsApp; channel already added
                    # If Lua failed, send_custom_notification will still send via 'whatsapp' channel
                # else: whatsapp already in channels for direct send
            elif notification_type == 'SHIFT_UPDATED':
                title = "Shift Updated"
                message = f"Your shift on {start_str} has been updated."
                channels.append('whatsapp')
            elif notification_type == 'SHIFT_CANCELLED':
                title = "Shift Cancelled"
                message = f"Your shift on {start_str} has been cancelled."
                channels.append('whatsapp')
            elif notification_type == 'SHIFT_REMINDER':
                title = "Upcoming Shift Reminder"
                message = f"Reminder: You have a shift starting soon on {start_str}."
                channels.append('whatsapp')
            elif notification_type == 'CLOCK_IN_REMINDER':
                # Clock-in reminder: send via Miya (Lua) when configured, else direct WhatsApp template
                title = "Clock-In Reminder"
                message = f"Please clock in for your shift starting at {start_str}."
                staff = recipient
                if getattr(staff, 'phone', None):
                    restaurant = getattr(getattr(shift, 'schedule', None), 'restaurant', None)
                    now = timezone.now()
                    shift_start = getattr(shift, 'start_time', None)
                    if shift_start:
                        try:
                            shift_start = timezone.localtime(shift_start)
                        except Exception:
                            pass
                    start_time = shift_start.strftime('%H:%M') if shift_start and hasattr(shift_start, 'strftime') else ''
                    minutes_until = int(max(0, (shift_start - now).total_seconds() // 60)) if shift_start else 0
                    minutes_from_now = f"{minutes_until} minutes"
                    location = getattr(restaurant, 'address', None) or getattr(restaurant, 'name', None) or "Restaurant"
                    shift_end = getattr(shift, 'end_time', None)
                    if shift_end:
                        try:
                            shift_end = timezone.localtime(shift_end)
                        except Exception:
                            pass
                    duration_str = ""
                    if shift_start and shift_end and hasattr(shift_start, 'strftime') and hasattr(shift_end, 'strftime'):
                        from datetime import timedelta
                        dur = shift_end - shift_start
                        if dur.total_seconds() < 0:
                            dur = dur + timedelta(days=1)
                        mins = int(dur.total_seconds() // 60)
                        duration_str = f"{mins // 60}h {mins % 60}m"
                    role = (getattr(shift, 'role', '') or '').upper() or 'Shift'
                    notes = (getattr(shift, 'notes', '') or '').strip()
                    shift_description = f"{role}" + (f" • {notes}" if notes else "")
                    # Prefer Miya (Lua) so the reminder comes from the assistant
                    lua_url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
                    lua_agent_id = getattr(settings, 'LUA_AGENT_ID', None)
                    if lua_url or lua_agent_id:
                        ok, _ = self.send_lua_clock_in_reminder(
                            phone=staff.phone,
                            first_name=staff.first_name or "Team Member",
                            start_time_str=start_time,
                            minutes_until_str=minutes_from_now,
                            location=location,
                            shift_id=getattr(shift, 'id', None),
                            template_name=getattr(settings, 'WHATSAPP_TEMPLATE_STAFF_CLOCK_IN', 'staff_clock_in'),
                            shift_description=shift_description,
                            duration=duration_str,
                        )
                        if not ok:
                            # Fallback: send direct WhatsApp if Miya webhook failed
                            components = [
                                {
                                    "type": "body",
                                    "parameters": [
                                        {"type": "text", "text": (staff.first_name or "Team Member")[:255]},
                                        {"type": "text", "text": str(start_time)[:20]},
                                        {"type": "text", "text": str(minutes_from_now)[:50]},
                                        {"type": "text", "text": str(location)[:255]},
                                    ],
                                }
                            ]
                            self.send_whatsapp_template(
                                phone=staff.phone,
                                template_name=getattr(settings, 'WHATSAPP_TEMPLATE_STAFF_CLOCK_IN', 'staff_clock_in'),
                                language_code='en_US',
                                components=components,
                            )
                    else:
                        components = [
                            {
                                "type": "body",
                                "parameters": [
                                    {"type": "text", "text": (staff.first_name or "Team Member")[:255]},
                                    {"type": "text", "text": str(start_time)[:20]},
                                    {"type": "text", "text": str(minutes_from_now)[:50]},
                                    {"type": "text", "text": str(location)[:255]},
                                ],
                            }
                        ]
                        self.send_whatsapp_template(
                            phone=staff.phone,
                            template_name=getattr(settings, 'WHATSAPP_TEMPLATE_STAFF_CLOCK_IN', 'staff_clock_in'),
                            language_code='en_US',
                            components=components,
                        )
                # App/push get plain message in all cases
                channels = ['app', 'push']

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
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"  # user-events-production production
            url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
            if not url:
                url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}"
            
            payload = {
                "eventType": "staff_invite",
                "staffId": f"invite_{str(invitation_token)[:8]}",
                "staffName": first_name or "Staff Member",
                "role": role.lower() if role else "staff",
                "details": {
                    "phone": self._normalize_phone(phone),
                    "inviteLink": invite_link,
                    "restaurantName": restaurant_name,
                    "invitationToken": invitation_token,
                    "language": language
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
            resp = requests.post(url, json=payload, headers=headers, timeout=LUA_WEBHOOK_TIMEOUT)
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
                    "flowData": flow_data or {}
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
            resp = requests.post(url, json=payload, headers=headers, timeout=LUA_WEBHOOK_TIMEOUT)
            
            if resp.status_code in (200, 201):
                return True, resp.json()
            else:
                logger.warning(f"[LuaAccept] Failed: {resp.status_code} - {resp.text}")
                return False, {"error": resp.text, "status_code": resp.status_code}
                
        except Exception as e:
            logger.error(f"[LuaAccept] Unexpected error: {str(e)}")
            return False, {"error": str(e)}

    def send_lua_staff_activated(self, phone, first_name, restaurant_name, user_id, pin_code=None, batch_id=None):
        """
        ONE-TAP activation handoff: notify Lua agent that a staff account was just activated.
        Miya sends the welcome message (schedule, clock in, checklists, updates). No outbound
        message is sent from Django before this; the first message from the user triggered activation.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"
            url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
            if not url:
                url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}"
            success_message = (
                "Congratulations! Your account has been successfully activated. Welcome to the team!"
            )
            payload = {
                "eventType": "staff_activated",
                "staffId": user_id,
                "staffName": first_name or "Staff",
                "messageForUser": success_message,
                "details": {
                    "phoneNumber": self._normalize_phone(phone),
                    "restaurantName": restaurant_name,
                    "userId": user_id,
                    "pinCode": pin_code,
                    "batchId": batch_id or "",
                    "messageForUser": success_message,
                },
                "timestamp": timezone.now().isoformat(),
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lua_api_key}",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": "manager",
            }
            logger.info(f"[LuaStaffActivated] Handoff for {first_name} at {restaurant_name}")
            resp = requests.post(url, json=payload, headers=headers, timeout=LUA_WEBHOOK_TIMEOUT)
            if resp.status_code in (200, 201):
                return True, (resp.json() if resp.text else {})
            logger.warning(f"[LuaStaffActivated] Failed: {resp.status_code} - {resp.text}")
            return False, {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.error(f"[LuaStaffActivated] Unexpected error: {str(e)}")
            return False, {"error": str(e)}

    def send_lua_clock_in_reminder(
        self,
        phone,
        first_name,
        start_time_str,
        minutes_until_str,
        location,
        shift_id=None,
        template_name=None,
        shift_description=None,
        duration=None,
    ):
        """
        Notify Miya (Lua) to send the clock-in reminder shortly before a staff shift.
        Miya sends the WhatsApp template (e.g. staff_clock_in or clock_in_reminder) so the reminder comes from the assistant.
        shift_description and duration support 5-parameter templates ({{4}} Shift, {{5}} Duration).
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"
            url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
            if not url:
                url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}"
            template_name = template_name or getattr(
                settings, 'WHATSAPP_TEMPLATE_STAFF_CLOCK_IN', 'staff_clock_in'
            )
            payload = {
                "eventType": "clock_in_reminder",
                "details": {
                    "phoneNumber": self._normalize_phone(phone),
                    "staffFirstName": (first_name or "Team Member").strip(),
                    "shiftStartTime": start_time_str or "",
                    "minutesUntil": minutes_until_str or "",
                    "location": (location or "Restaurant").strip(),
                    "shiftId": str(shift_id) if shift_id else None,
                    "templateName": template_name,
                    "shiftDescription": (shift_description or "").strip() or (start_time_str or ""),
                    "duration": (duration or "").strip() or "",
                },
                "timestamp": timezone.now().isoformat(),
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lua_api_key}",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": "manager",
            }
            logger.info(
                f"[LuaClockInReminder] Sending clock-in reminder for {first_name} at {url}"
            )
            resp = requests.post(
                url, json=payload, headers=headers, timeout=LUA_WEBHOOK_TIMEOUT
            )
            if resp.status_code in (200, 201):
                return True, (resp.json() if resp.text else {})
            logger.warning(
                f"[LuaClockInReminder] Failed: {resp.status_code} - {resp.text}"
            )
            return False, {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.error(f"[LuaClockInReminder] Unexpected error: {str(e)}")
            return False, {"error": str(e)}

    def send_lua_shift_assigned(
        self,
        phone,
        first_name,
        start_str,
        message,
        shift_id=None,
    ):
        """
        Notify Miya (Lua) that a shift was assigned so Miya can send the WhatsApp message.
        Keeps shift-assigned messages coming from the assistant when the webhook is configured.
        """
        try:
            from accounts.services import LUA_AGENT_ID, LUA_WEBHOOK_API_KEY
            import os
            lua_api_key = getattr(settings, 'LUA_API_KEY', None) or os.environ.get('LUA_API_KEY', '')
            webhook_id = "77f06520-d115-41b1-865e-afe7814ce82d"
            url = getattr(settings, 'LUA_USER_EVENTS_WEBHOOK', None)
            if not url:
                url = f"https://webhook.heylua.ai/{LUA_AGENT_ID}/{webhook_id}" if getattr(settings, 'LUA_AGENT_ID', None) else None
            if not url:
                return False, {"error": "No Lua user-events webhook configured"}
            payload = {
                "eventType": "shift_assigned",
                "details": {
                    "phoneNumber": self._normalize_phone(phone),
                    "staffFirstName": (first_name or "Team Member").strip(),
                    "shiftStartTime": start_str or "",
                    "message": (message or "").strip(),
                    "shiftId": str(shift_id) if shift_id else None,
                },
                "timestamp": timezone.now().isoformat(),
            }
            headers = {
                "Content-Type": "application/json",
                "Authorization": f"Bearer {lua_api_key}",
                "Api-Key": lua_api_key,
                "x-api-key": LUA_WEBHOOK_API_KEY,
                "x-role": "manager",
            }
            logger.info(f"[LuaShiftAssigned] Sending shift-assigned event for {first_name} to {url}")
            resp = requests.post(url, json=payload, headers=headers, timeout=LUA_WEBHOOK_TIMEOUT)
            if resp.status_code in (200, 201):
                return True, (resp.json() if resp.text else {})
            logger.warning(f"[LuaShiftAssigned] Failed: {resp.status_code} - {resp.text}")
            return False, {"error": resp.text, "status_code": resp.status_code}
        except Exception as e:
            logger.error(f"[LuaShiftAssigned] Unexpected error: {str(e)}")
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
            resp = requests.post(url, json=payload, headers=headers, timeout=LUA_WEBHOOK_TIMEOUT)
            
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
        """
        Send WhatsApp template via Meta API directly.
        Do NOT use for staff invites: staff invitations must go through Miya (Lua agent)
        via send_lua_staff_invite() so the approved Lua template (e.g. staff_invitation) is used.
        """
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

    def send_announcement_to_audience(
        self,
        restaurant_id,
        title,
        message,
        sender=None,
        staff_ids=None,
        roles=None,
        departments=None,
        channels=None,
    ):
        """
        Send an announcement (in-app + WhatsApp) to staff in a restaurant.
        Used by Miya when a manager says e.g. "Announce: No work tomorrow."
        - restaurant_id: UUID of the restaurant.
        - title: Short title (e.g. "Announcement").
        - message: Body text to send.
        - sender: User who triggered (manager); optional.
        - staff_ids: Optional list of UUIDs to target specific staff.
        - roles: Optional list of role names (CustomUser.role), e.g. ["CHEF", "WAITER"].
        - departments: Optional list of department names (StaffProfile.department).
        - channels: List of channels; default ["app", "whatsapp"].
        Returns (success: bool, notification_count: int, error_message: str|None).
        """
        from django.db.models import Q
        from accounts.models import CustomUser

        if channels is None:
            channels = ["app", "whatsapp"]
        staff_ids = staff_ids or []
        roles = roles or []
        departments = departments or []

        try:
            qs = CustomUser.objects.filter(
                restaurant_id=restaurant_id,
                is_active=True,
            )
            # When targeting specific staff_ids (e.g. Miya "inform Salima"), include them even if no phone — we still send in-app.
            if not staff_ids:
                qs = qs.exclude(Q(phone__isnull=True) | Q(phone=""))
            if sender:
                qs = qs.exclude(id=sender.id)

            if staff_ids or roles or departments:
                filters = Q()
                if staff_ids:
                    filters |= Q(id__in=staff_ids)
                if roles:
                    filters |= Q(role__in=roles)
                if departments:
                    filters |= Q(profile__department__in=departments)
                qs = qs.filter(filters)

            recipients = list(qs.distinct())
            if not recipients:
                return False, 0, "No recipients found for the given audience", {}

            sent = 0
            whatsapp_sent = 0
            recipients_without_phone = []  # got in-app only (no WhatsApp) — so Miya can say "couldn't reach by WhatsApp"
            for recipient in recipients:
                try:
                    notification = Notification.objects.create(
                        recipient=recipient,
                        sender=sender,
                        title=title or "Announcement",
                        message=message,
                        notification_type="ANNOUNCEMENT",
                        priority="MEDIUM",
                        data={"source": "miya_announcement", "channels": channels},
                    )
                    ok, _ = self.send_custom_notification(
                        recipient=recipient,
                        notification=notification,
                        channels=channels,
                        sender=sender,
                        title=notification.title,
                        message=notification.message,
                        notification_type="ANNOUNCEMENT",
                    )
                    if ok:
                        sent += 1
                        channels_used = getattr(notification, "channels_sent", []) or []
                        if "whatsapp" in channels_used:
                            whatsapp_sent += 1
                        elif "whatsapp" in channels and not (getattr(recipient, "phone", None) or "").strip():
                            full_name = f"{(getattr(recipient, 'first_name') or '').strip()} {(getattr(recipient, 'last_name') or '').strip()}".strip() or str(recipient.id)
                            recipients_without_phone.append({"id": str(recipient.id), "full_name": full_name})
                except Exception as e:
                    logger.warning("Announcement send failed for %s: %s", recipient.id, e)
            details = {"whatsapp_sent": whatsapp_sent, "recipients_without_phone": recipients_without_phone}
            return True, sent, None, details
        except Exception as e:
            logger.exception("send_announcement_to_audience failed: %s", e)
            return False, 0, str(e), {}

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

    def send_staff_activated_welcome(self, phone, first_name, restaurant_name, language_code='en_US'):
        """
        Send the staff_activated_welcome WhatsApp template after account activation.
        Template: Welcome {{first_name}}! Your staff account for {{restaurant_name}} has been
        successfully activated. You're now ready to clock in, receive tasks, and manage your shifts.
        """
        template_name = getattr(settings, 'WHATSAPP_TEMPLATE_STAFF_ACTIVATED_WELCOME', 'staff_activated_welcome')
        first_name = (first_name or 'Staff').strip()
        restaurant_name = (restaurant_name or '').strip() or 'your restaurant'
        components = [
            {
                "type": "body",
                "parameters": [
                    {"type": "text", "text": first_name},
                    {"type": "text", "text": restaurant_name},
                ],
            },
        ]
        # If template has a header (e.g. "Welcome {{1}}!"), add it
        if getattr(settings, 'WHATSAPP_TEMPLATE_STAFF_ACTIVATED_WELCOME_HAS_HEADER', False):
            components.insert(0, {
                "type": "header",
                "parameters": [{"type": "text", "text": first_name}],
            })
        return self.send_whatsapp_template(phone, template_name, language_code=language_code, components=components)

    def send_staff_checklist_step(self, phone, question_text, language_code='en_US'):
        """
        Send one checklist step using the approved staff_checklist template if configured.
        Template body should have one variable {{1}} = question (e.g. "Have you clean glassware and bar surface").
        Buttons Yes/No/N/A are defined in the template. Returns True if sent via template, False to use interactive fallback.
        """
        template_name = getattr(settings, 'WHATSAPP_TEMPLATE_STAFF_CHECKLIST', None) or ''
        if not template_name or not (question_text or '').strip():
            return False
        question = (question_text or '').strip()[:1024]
        components = [{"type": "body", "parameters": [{"type": "text", "text": question}]}]
        ok, _ = self.send_whatsapp_template(phone, template_name, language_code=language_code, components=components)
        return ok

    def send_shift_review_request(self, phone, first_name, language_code=None):
        """
        Send the shift_review WhatsApp template (Hi {{1}}, how was your shift today?) with quick-reply buttons.
        Template has one body variable: first name. Buttons Bad/Decent/Good/Great are defined in the template.
        """
        template_name = getattr(settings, 'WHATSAPP_TEMPLATE_SHIFT_REVIEW', 'shift_review')
        lang = language_code or getattr(settings, 'WHATSAPP_TEMPLATE_SHIFT_REVIEW_LANGUAGE', 'en_US')
        first_name = (first_name or 'there').strip()
        components = [{"type": "body", "parameters": [{"type": "text", "text": first_name}]}]
        return self.send_whatsapp_template(phone, template_name, language_code=lang, components=components)

    def start_conversational_checklist_after_clock_in(self, user, active_shift, phone_digits=None):
        """
        Start the step-by-step conversational checklist (WhatsApp) for a staff who just clocked in.
        Call this after clock-in via WhatsApp webhook OR after clock-in via app/API so they receive
        the first checklist step immediately.
        Returns True if checklist was started and first step sent, False otherwise.
        """
        try:
            from scheduling.models import AssignedShift, ShiftTask, ShiftChecklistProgress
        except Exception as e:
            logger.warning("start_conversational_checklist_after_clock_in: could not import scheduling models: %s", e)
            return False
        if not user or not active_shift:
            return False
        # Do not restart if checklist is already completed (safeguard)
        existing = ShiftChecklistProgress.objects.filter(shift=active_shift, staff=user, status='COMPLETED').first()
        if existing:
            return False
        phone = (phone_digits or (getattr(user, "phone", None) or "")).strip()
        if not phone:
            return False
        phone_digits = "".join(filter(str.isdigit, phone))
        if len(phone_digits) < 10:
            return False
        session = WhatsAppSession.objects.filter(phone=phone_digits).first()
        if not session:
            session = WhatsAppSession.objects.create(phone=phone_digits, user=user)
        elif not session.user_id and user:
            session.user = user
            session.save(update_fields=["user"])

        def _ensure_shift_tasks_from_templates(shift_obj):
            try:
                if ShiftTask.objects.filter(shift=shift_obj).exists():
                    return
            except Exception:
                return
            try:
                templates = list(shift_obj.task_templates.all())
            except Exception:
                templates = []
            for tpl in templates:
                steps = []
                try:
                    if getattr(tpl, "sop_steps", None):
                        steps = list(tpl.sop_steps or [])
                    elif getattr(tpl, "tasks", None):
                        steps = list(tpl.tasks or [])
                except Exception:
                    steps = []
                if not steps:
                    steps = [{"title": getattr(tpl, "name", "Task"), "description": getattr(tpl, "description", "") or ""}]
                for step in steps:
                    if isinstance(step, str):
                        title = step.strip()[:255] or getattr(tpl, "name", "Task")
                        desc = ""
                    elif isinstance(step, dict):
                        title = (step.get("title") or step.get("name") or step.get("task") or getattr(tpl, "name", "Task"))[:255]
                        desc = (step.get("description") or step.get("details") or "").strip()
                    else:
                        title = getattr(tpl, "name", "Task")
                        desc = ""
                    v_req = bool(step.get("verification_required", False) if isinstance(step, dict) else False) or bool(getattr(tpl, "verification_required", False))
                    v_type = (step.get("verification_type") or getattr(tpl, "verification_type", "NONE") or "NONE") if isinstance(step, dict) else (getattr(tpl, "verification_type", "NONE") or "NONE")
                    v_inst = (step.get("verification_instructions") or getattr(tpl, "verification_instructions", None)) if isinstance(step, dict) else getattr(tpl, "verification_instructions", None)
                    v_cl = (step.get("verification_checklist") or getattr(tpl, "verification_checklist", []) or []) if isinstance(step, dict) else (getattr(tpl, "verification_checklist", []) or [])
                    ShiftTask.objects.create(
                        shift=shift_obj,
                        title=title,
                        description=desc,
                        status="TODO",
                        assigned_to=user,
                        verification_required=v_req,
                        verification_type=v_type,
                        verification_instructions=v_inst,
                        verification_checklist=v_cl,
                    )

        _ensure_shift_tasks_from_templates(active_shift)

        tasks_qs = ShiftTask.objects.filter(shift=active_shift).exclude(status__in=["COMPLETED", "CANCELLED"])
        priority_order = {"URGENT": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
        tasks = sorted(list(tasks_qs), key=lambda t: (priority_order.get((t.priority or "MEDIUM").upper(), 2), t.created_at))
        task_ids = [str(t.id) for t in tasks]
        if not task_ids:
            session.state = "idle"
            session.save(update_fields=["state"])
            return False

        try:
            ShiftChecklistProgress.objects.update_or_create(
                shift=active_shift,
                staff=user,
                defaults={
                    "channel": "whatsapp",
                    "phone": phone_digits,
                    "task_ids": task_ids,
                    "current_task_id": task_ids[0],
                    "responses": {},
                    "status": "IN_PROGRESS",
                },
            )
        except Exception as e:
            logger.warning("ShiftChecklistProgress create failed: %s", e)

        session.context["checklist"] = {
            "shift_id": str(active_shift.id),
            "tasks": task_ids,
            "current_task_id": task_ids[0],
            "responses": {},
            "started_at": timezone.now().isoformat(),
        }
        session.state = "in_checklist"
        session.save(update_fields=["state", "context"])

        if active_shift.end_time and timezone.now() > active_shift.end_time:
            self.send_whatsapp_text(phone_digits, "⏱️ This shift has already ended. No checklist to run.")
            try:
                ShiftChecklistProgress.objects.filter(
                    shift=active_shift, staff=user, status="IN_PROGRESS"
                ).update(status="CANCELLED", completed_at=timezone.now())
            except Exception:
                pass
            session.context.pop("checklist", None)
            session.state = "idle"
            session.save(update_fields=["state", "context"])
            return False

        first_task = tasks[0]
        if getattr(first_task, "verification_required", False) and str(getattr(first_task, "verification_type", "NONE")).upper() == "PHOTO":
            msg = (
                f"📋 *Task 1/{len(task_ids)}*\n\n"
                f"*{first_task.title}*\n"
                f"{first_task.description or ''}\n\n"
                f"📸 Please complete this task and send a photo as evidence."
            )
            session.context["awaiting_verification_for_task_id"] = str(first_task.id)
            session.state = "awaiting_task_photo"
            session.save(update_fields=["state", "context"])
            self.send_whatsapp_text(phone_digits, msg)
        else:
            question_text = (first_task.title or "").strip()
            if (getattr(first_task, "description", None) or "").strip():
                question_text = f"{question_text}. {(first_task.description or '').strip()}"
            if not self.send_staff_checklist_step(phone_digits, question_text):
                task_msg = (
                    f"📋 *Task 1/{len(task_ids)}*\n\n"
                    f"*{first_task.title}*\n"
                    f"{first_task.description or ''}\n\n"
                    "Is this complete?"
                )
                buttons = [
                    {"id": "yes", "title": "✅ Yes"},
                    {"id": "no", "title": "❌ No"},
                    {"id": "n_a", "title": "➖ N/A"},
                ]
                self.send_whatsapp_buttons(phone_digits, task_msg, buttons)
        return True

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

    def send_whatsapp_location_request_interactive(self, phone, body_text):
        """
        Send a free-form interactive message with a native "Share Location" button.
        Use when clock_in_location_request template is not available or fails.
        https://developers.facebook.com/docs/whatsapp/cloud-api/guides/send-messages/location-request-messages
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
            text = (body_text or "Please share your location to clock in.").strip()[:4096]
            url = f"https://graph.facebook.com/{getattr(settings, 'WHATSAPP_API_VERSION', 'v22.0')}/{phone_id}/messages"
            payload = {
                "messaging_product": "whatsapp",
                "to": phone,
                "type": "interactive",
                "interactive": {
                    "type": "location_request_message",
                    "body": {"text": text},
                    "action": {"name": "send_location"}
                }
            }
            resp = requests.post(url, headers={'Authorization': f"Bearer {token}"}, json=payload)
            try:
                data = resp.json()
            except Exception:
                data = {"error": resp.text}
            ok = resp.status_code == 200
            if not ok:
                logger.warning("WhatsApp location request failed: %s - %s", resp.status_code, data)
            return ok, {"status_code": resp.status_code, "data": data}
        except Exception as e:
            logger.error("send_whatsapp_location_request_interactive error: %s", e)
            return False, {"error": str(e)}

    def send_whatsapp_location_request(self, phone, body):
        """
        Send clock-in location request with a working "Share Location" button.
        Tries: (1) official clock_in_location_request template, (2) interactive location_request_message.
        Never falls back to plain text (no button). Clock-in must not proceed without location payload.
        """
        template_name = getattr(settings, 'WHATSAPP_TEMPLATE_CLOCK_IN_LOCATION', 'clock_in_location_request')
        fallback_body = body or "Please share your live location to clock in."
        try:
            ok, resp = self.send_whatsapp_template(
                phone=phone,
                template_name=template_name,
                language_code='en_US',
                components=[]
            )
            if ok:
                return ok, resp
        except Exception:
            pass
        try:
            ok, resp = self.send_whatsapp_location_request_interactive(phone, fallback_body)
            if ok:
                return ok, resp
        except Exception:
            pass
        # Retry interactive once; do NOT send plain text (no Share Location button)
        try:
            ok, resp = self.send_whatsapp_location_request_interactive(phone, fallback_body)
            if ok:
                return ok, resp
        except Exception:
            pass
        logger.warning("send_whatsapp_location_request: template and interactive failed for %s", phone)
        return False, {"error": "Location request (template and interactive) failed"}

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

