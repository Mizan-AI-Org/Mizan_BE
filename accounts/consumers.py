import json
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async
from django.contrib.auth import get_user_model
from rest_framework_simplejwt.tokens import AccessToken

User = get_user_model()


class RestaurantSettingsConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        # Authenticate user (supports JWT via query param like other consumers)
        self.user = self.scope.get('user')
        if not self.user or not self.user.is_authenticated:
            await self.close()
            return

        # Require a restaurant association
        restaurant = getattr(self.user, 'restaurant', None)
        if not restaurant:
            await self.close()
            return

        self.restaurant_id = str(restaurant.id)
        self.group_name = f'restaurant_settings_{self.restaurant_id}'

        # Join group for this restaurant's settings
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
        # Settings updates are server-driven; ignore client messages for now
        pass

    async def settings_update(self, event):
        # Broadcast restaurant settings updates to connected clients
        payload = event.get('payload', {})
        await self.send(text_data=json.dumps({
            'type': 'settings_update',
            'payload': payload
        }))

    @database_sync_to_async
    def get_user_from_token(self, token_key):
        try:
            access_token = AccessToken(token_key)
            user = User.objects.get(id=access_token['user_id'])
            return user
        except Exception:
            return None

    async def websocket_connect(self, message):
        # Allow JWT token via query string to authenticate WS connection
        query_string = self.scope.get('query_string', b'').decode()
        token_param = dict(qc.split('=') for qc in query_string.split('&') if '=' in qc).get('token')

        if token_param:
            self.scope['user'] = await self.get_user_from_token(token_param)

        await super().websocket_connect(message)