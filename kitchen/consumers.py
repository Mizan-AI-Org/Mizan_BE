import json
import logging
from asgiref.sync import async_to_sync
from channels.generic.websocket import AsyncWebsocketConsumer
from channels.db import database_sync_to_async

logger = logging.getLogger(__name__)
from rest_framework_simplejwt.tokens import AccessToken
from django.contrib.auth import get_user_model
from staff.models import Order
from staff.serializers import OrderSerializer

User = get_user_model()

class KitchenConsumer(AsyncWebsocketConsumer):
    async def connect(self):
        self.user = self.scope['user']
        if not self.user.is_authenticated or self.user.role not in ['SUPER_ADMIN', 'ADMIN', 'CHEF']:
            await self.close()
            return

        self.restaurant_id = str(self.user.restaurant.id)
        self.group_name = f'kitchen_orders_{self.restaurant_id}'

        await self.channel_layer.group_add(
            self.group_name,
            self.channel_name
        )
        await self.accept()

    async def disconnect(self, close_code):
        await self.channel_layer.group_discard(
            self.group_name,
            self.channel_name
        )

    async def receive(self, text_data):
        # Kitchen clients might send messages to update order status
        data = json.loads(text_data)
        order_id = data.get('order_id')
        action = data.get('action') # e.g., 'prepare', 'complete'

        if order_id and action:
            await self.update_order_status(order_id, action)

    @database_sync_to_async
    def update_order_status(self, order_id, action):
        try:
            order = Order.objects.get(id=order_id, restaurant=self.user.restaurant)
            if action == 'prepare':
                order.status = 'PREPARING'
            elif action == 'complete':
                order.status = 'READY' # Or 'COMPLETED' depending on workflow
            order.save()
            # Broadcast the updated order to the kitchen group
            async_to_sync(self.channel_layer.group_send)(
                self.group_name,
                {
                    'type': 'send_order_update',
                    'order': OrderSerializer(order).data
                }
            )
        except Order.DoesNotExist:
            logger.warning("Kitchen consumer: order not found for id=%s", order_id)

    async def send_order_update(self, event):
        order_data = event['order']
        await self.send(text_data=json.dumps({
            'type': 'order_update',
            'order': order_data
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
        query_string = self.scope['query_string'].decode()
        token_param = dict(qc.split('=') for qc in query_string.split('&') if '=' in qc).get('token')

        if token_param:
            self.scope['user'] = await self.get_user_from_token(token_param)
        
        await super().websocket_connect(message)
