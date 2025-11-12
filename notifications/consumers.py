# notifications/consumers.py

import json
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken, UntypedToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

User = get_user_model()

class NotificationConsumer(AsyncWebsocketConsumer):

    @database_sync_to_async
    def get_user_from_token(self, token_key):
        """
        Tries to authenticate a user from a JWT token.
        """
        try:
            # This will validate the token's signature and expiration
            AccessToken(token_key)
            # You can also use UntypedToken if you don't need to check the 'token_type'
            token = UntypedToken(token_key)
            user = User.objects.get(id=token['user_id'])
            return user
        except (InvalidToken, TokenError, User.DoesNotExist):
            # Token is invalid, expired, or user doesn't exist
            return None

    async def connect(self):
        """
        Called when the websocket is trying to connect.
        """
        # Get the token from the query string
        # self.scope['query_string'] is a byte string
        query_params = parse_qs(self.scope['query_string'].decode())
        token = query_params.get('token', [None])[0]

        if not token:
            await self.close(code=4001)
            return

        # Authenticate the user from the token
        self.user = await self.get_user_from_token(token)

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4002)
            return
            
        # At this point, the user is authenticated
        self.group_name = f'user_{self.user.id}_notifications'

        # Join the user's private group
        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )

        await self.accept()

    async def disconnect(self, close_code):
        # Leave group
        if hasattr(self, 'group_name'):
            await self.channel_layer.group_discard(
                self.group_name,
                self.channel_name
            )

    async def receive(self, text_data):
        # We don't need to handle incoming messages from the client
        pass

    async def send_notification(self, event):
        """
        This method is called when a message is sent to the group.
        It receives the event and sends its 'notification' payload
        to the connected client.
        """
        notification_data = event['notification']

        # Send message to WebSocket
        await self.send(text_data=json.dumps({
            'type': 'notification_message',
            'notification': notification_data
        }))