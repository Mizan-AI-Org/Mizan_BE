import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack
from django.urls import path, include

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter([
            # Your WebSocket URL routing goes here
            path('ws/notifications/', include('notifications.routing')),
            path('ws/kitchen/', include('kitchen.routing')),
            path('ws/chat/', include('chat.routing')),
            # Restaurant settings updates (real-time broadcasting)
            path('ws/settings/', include('accounts.routing')),
        ])
    ),
})