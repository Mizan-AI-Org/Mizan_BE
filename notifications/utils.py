"""
Multilingual incident inference helpers for voice/text reporting.
Supports English, Arabic, and French keywords.
"""
from datetime import timedelta
from django.utils import timezone as tz

SAFETY_KEYWORDS = [
    'injury', 'hurt', 'slip', 'fall', 'bleed', 'burn', 'fire', 'unsafe', 'hazard', 'accident',
    'إصابة', 'انزلاق', 'سقوط', 'حرق', 'حادث', 'خطر', 'نار',  # Arabic
    'blessure', 'glissade', 'chute', 'brûlure', 'incident', 'danger', 'feu', 'accident',  # French
]
MAINTENANCE_KEYWORDS = [
    'broken', 'leak', 'maintenance', 'machine', 'equipment', 'fridge', 'freezer', 'oven', 'gas', 'water',
    'مكسور', 'تسرب', 'صيانة', 'جهاز', 'فرن', 'غاز', 'ماء', 'ثلاجة',  # Arabic
    'cassé', 'fuite', 'maintenance', 'machine', 'équipement', 'frigo', 'four', 'gaz', 'eau',  # French
]
HR_KEYWORDS = [
    'harassment', 'abuse', 'discrimination', 'fight', 'threat',
    'مضايقة', 'تحرش', 'تمييز', 'شجار', 'تهديد',  # Arabic
    'harcèlement', 'abus', 'discrimination', 'bagarre', 'menace',  # French
]
SERVICE_KEYWORDS = [
    'customer', 'guest', 'complaint', 'service', 'refund',
    'عميل', 'زبون', 'شكوى', 'خدمة', 'استرداد',  # Arabic
    'client', 'invité', 'plainte', 'service', 'remboursement',  # French
]
CRITICAL_KEYWORDS = [
    'critical', 'life threatening', 'life-threatening', 'fire', 'gas leak',
    'حرج', 'مهدد للحياة', 'حريق', 'تسرب غاز',  # Arabic
    'critique', 'menaçant', 'feu', 'fuite de gaz',  # French
]
HIGH_KEYWORDS = [
    'injury', 'bleeding', 'severe', 'danger', 'urgent',
    'إصابة', 'نزيف', 'خطير', 'خطر', 'عاجل',  # Arabic
    'blessure', 'saignement', 'sévère', 'danger', 'urgent',  # French
]
LOW_KEYWORDS = [
    'minor', 'small', 'low risk', 'low-risk',
    'بسيط', 'طفيف', 'خطر منخفض',  # Arabic
    'mineur', 'petit', 'faible risque',  # French
]
TIME_YESTERDAY = ['yesterday', 'أمس', 'hier']
TIME_TODAY = ['today', 'اليوم', "aujourd'hui"]


def infer_incident_type(text):
    """Infer incident type from text (English, Arabic, or French)."""
    if not text:
        return None
    t_low = text.lower().strip()
    t_norm = (text or '').lower()
    if t_low in ['safety', 'maintenance', 'hr', 'service', 'other', 'general']:
        return t_low.title() if t_low != 'hr' else 'HR'
    if any(k in t_norm for k in SAFETY_KEYWORDS):
        return 'Safety'
    if any(k in t_norm for k in MAINTENANCE_KEYWORDS):
        return 'Maintenance'
    if any(k in t_norm for k in HR_KEYWORDS):
        return 'HR'
    if any(k in t_norm for k in SERVICE_KEYWORDS):
        return 'Service'
    return None


def infer_severity(text):
    """Infer severity from text (English, Arabic, or French)."""
    if not text:
        return 'MEDIUM'
    t_norm = (text or '').lower()
    if any(k in t_norm for k in CRITICAL_KEYWORDS):
        return 'CRITICAL'
    if any(k in t_norm for k in HIGH_KEYWORDS):
        return 'HIGH'
    if any(k in t_norm for k in LOW_KEYWORDS):
        return 'LOW'
    return 'MEDIUM'


def extract_occurred_at(text, now):
    """Extract occurrence time from text. Supports English, Arabic, French."""
    if not text:
        return None
    from dateutil import parser as date_parser
    t_low = text.lower()
    if any(x in t_low for x in TIME_YESTERDAY):
        base = now - timedelta(days=1)
    elif any(x in t_low for x in TIME_TODAY):
        base = now
    else:
        base = now
    try:
        dt = date_parser.parse(text, fuzzy=True, default=base)
        if dt > now + timedelta(days=7):
            return None
        return tz.make_aware(dt) if tz.is_naive(dt) else dt
    except Exception:
        return None


from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import Notification, DeviceToken
from django.contrib.contenttypes.models import ContentType
import firebase_admin
from firebase_admin import messaging

def send_realtime_notification(recipient, verb, description=None, actor=None, target=None, level='default'):
    # Create database notification
    notification = Notification.objects.create(
        recipient=recipient,
        verb=verb,
        description=description,
        level=level,
        actor_content_type=ContentType.objects.get_for_model(actor) if actor else None,
        actor_object_id=str(actor.id) if actor else None,
        target_content_type=ContentType.objects.get_for_model(target) if target else None,
        target_object_id=str(target.id) if target else None,
    )

    # Send WebSocket notification
    channel_layer = get_channel_layer()
    group_name = f'notifications_{str(recipient.id)}'

    message_data = {
        'type': 'send_notification', # This calls the send_notification method in the consumer
        'message': {
            'id': str(notification.id),
            'verb': notification.verb,
            'description': notification.description,
            'level': notification.level,
            'timestamp': notification.timestamp.isoformat(),
            'read': notification.read,
            'actor': str(actor) if actor else None,
            'target': str(target) if target else None,
        }
    }
    
    async_to_sync(channel_layer.group_send)(
        group_name,
        message_data
    )

    # Send FCM push notification
    if firebase_admin._apps:
        device_tokens = DeviceToken.objects.filter(user=recipient)
        if device_tokens.exists():
            tokens = [dt.token for dt in device_tokens]
            fcm_message = messaging.MulticastMessage(
                notification=messaging.Notification(
                    title=f"Mizan: {verb.replace('_', ' ').title()}",
                    body=description or "You have a new notification."
                ),
                data={
                    "notification_id": str(notification.id),
                    "type": verb,
                    "description": description or "",
                    "timestamp": notification.timestamp.isoformat(),
                },
                tokens=tokens,
            )
            try:
                response = messaging.send_each(fcm_message)
                print("FCM message sent successfully:", response.success_count, "succeeded,", response.failure_count, "failed.")
                for token_response in response.responses:
                    if not token_response.success:
                        print(f"Failed to send message to token: {token_response.exception}")
                        # Optionally, remove invalid tokens
                        # if token_response.exception.code == 'UNREGISTERED':
                        #     # Find and delete the invalid token from your database
                        pass
            except Exception as e:
                print(f"Error sending FCM message: {e}")
    else:
        print("Firebase Admin SDK not initialized. Skipping FCM notification.")

    return notification
