import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

import notifications.routing
import kitchen.routing
import chat.routing
import accounts.routing

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')

application = ProtocolTypeRouter({
    "http": get_asgi_application(),
    "websocket": AuthMiddlewareStack(
        URLRouter(
            notifications.routing.websocket_urlpatterns
            + kitchen.routing.websocket_urlpatterns
            + chat.routing.websocket_urlpatterns
            + accounts.routing.websocket_urlpatterns
        )
    ),
})
