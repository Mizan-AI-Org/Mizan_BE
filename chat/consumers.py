import json
import logging
from channels.generic.websocket import AsyncWebsocketConsumer

logger = logging.getLogger(__name__)
from channels.db import database_sync_to_async
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model
from .models import Message
from .serializers import MessageSerializer

User = get_user_model()

class ChatConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated:
            await self.close()
            return

        # Determine the room name based on whether it's a direct message or group chat
        # For simplicity, let's assume a single group chat for all staff in a restaurant
        self.room_name = f'chat_{self.user.restaurant.id}'
        self.room_group_name = f'chat_{self.user.restaurant.id}'

        await self.channel_layer.group_add(
            self.room_group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.room_group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        data = json.loads(text_data)
        message_content = data['message']
        recipient_id = data.get('recipient_id') # Optional for direct messages

        # Save message to database
        message = await self.create_message(self.user, message_content, self.room_name, recipient_id)
        serialized_message = MessageSerializer(message).data

        # Send message to room group
        await self.channel_layer.group_send(
            self.room_group_name,
            {
                'type': 'chat_message',
                'message': serialized_message
            }
        )

    async def chat_message(self, event):
        message = event['message']
        await self.send(text_data=json.dumps({
            'type': 'chat_message',
            'message': message
        }))

    @database_sync_to_async
    def create_message(self, sender, content, room_name, recipient_id=None):
        recipient = None
        if recipient_id:
            try:
                recipient = User.objects.get(id=recipient_id)
            except User.DoesNotExist:
                logger.warning("Chat consumer: recipient not found for id=%s", recipient_id)
        
        return Message.objects.create(
            sender=sender,
            recipient=recipient,
            room_name=room_name,
            content=content
        )

    @database_sync_to_async
    def get_user_from_token(self, token_key):
        try:
            access_token = AccessToken(token_key)
            user = User.objects.get(id=access_token['user_id'])
            return user
        except Exception:
            return None

    async def websocket_connect(self, message):
        query_string = self.scope['query_string'].decode()
        token_param = dict(qc.split('=') for qc in query_string.split('&') if '=' in qc).get('token')

        if token_param:
            self.scope['user'] = await self.get_user_from_token(token_param)
        
        await super().websocket_connect(message)
