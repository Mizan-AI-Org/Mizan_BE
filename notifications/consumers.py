import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken
from .models import Notification
from .serializers import NotificationSerializer

User = get_user_model()

class NotificationConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated:
            await self.close()
            return

        self.group_name = f'user_{self.user.id}_notifications'

        # Join group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave group
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        # We don't expect to receive messages from the frontend for now
        pass

    async def send_notification(self, event):
        notification = event['notification']
        await self.send(text_data=json.dumps({
            'type': 'notification_message',
            'message': notification
        }))

    @database_sync_to_async
    def get_user_from_token(self, token_key):
        try:
            access_token = AccessToken(token_key)
            user = User.objects.get(id=access_token['user_id'])
            return user
        except Exception as e:
            return None

    async def websocket_connect(self, message):
        query_string = self.scope['query_string'].decode()
        token_param = dict(qc.split('=') for qc in query_string.split('&') if '=' in qc).get('token')

        if token_param:
            self.scope['user'] = await self.get_user_from_token(token_param)
        
        await super().websocket_connect(message)
