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
