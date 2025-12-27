# notifications/consumers.py

import json, sys
from urllib.parse import parse_qs
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from rest_framework_simplejwt.tokens import AccessToken, UntypedToken
from rest_framework_simplejwt.exceptions import InvalidToken, TokenError

# User = get_user_model()


class NotificationConsumer(AsyncWebsocketConsumer):

    @database_sync_to_async
    def get_user_from_token(self, token_key):
        """
        Authenticate user from JWT, imported only after Django setup.
        """
        from rest_framework_simplejwt.tokens import AccessToken, UntypedToken
        from rest_framework_simplejwt.exceptions import InvalidToken, TokenError
        from django.contrib.auth import get_user_model

        User = get_user_model()
        try:
            # Validate token
            AccessToken(token_key)
            token = UntypedToken(token_key)
            user = User.objects.get(id=token["user_id"])
            return user
        except (InvalidToken, TokenError, User.DoesNotExist):
            return None

    async def connect(self):
        """
        Called when WebSocket is connecting.
        """
        from django.conf import settings  # safe to import here
        print("WebSocket connection attempt", file=sys.stderr)
        query_params = parse_qs(self.scope["query_string"].decode())
        token = query_params.get("token", [None])[0]

        if not token:
            await self.close(code=4001)
            return

        self.user = await self.get_user_from_token(token)
        username = self.user.first_name if self.user else "Anonymous"
        print(f"Authenticated user: {username}", file=sys.stderr)

        if not self.user or not self.user.is_authenticated:
            await self.close(code=4002)
            return

        self.group_name = f"user_{self.user.id}_notifications"
        print(f"Joining group: {self.group_name}", file=sys.stderr)
        await self.channel_layer.group_add(self.group_name, self.channel_name)
        print(f"layer: {self.channel_layer}", file=sys.stderr)
        await self.accept()

    async def disconnect(self, close_code):
        if hasattr(self, "group_name"):
            await self.channel_layer.group_discard(self.group_name, self.channel_name)

    async def receive(self, text_data):
        # Not handling messages from client side
        pass


    async def send_notification(self, event):
        notification_data = event["notification"]
        await self.send(text_data=json.dumps({
            "type": "notification_message",
            "notification": notification_data,
        }, default=str))

    # REQUIRED FIX
    async def notification_message(self, event):
        await self.send(text_data=json.dumps({
            "type": "notification_message",
            "notification": event.get("message")
        }))
