# mizan/asgi.py
import os
from django.core.asgi import get_asgi_application
from channels.routing import ProtocolTypeRouter, URLRouter
from channels.auth import AuthMiddlewareStack

os.environ.setdefault("DJANGO_SETTINGS_MODULE", "mizan.settings")

# Initialize Django before importing routing files
django_asgi_app = get_asgi_application()

import notifications.routing
# import kitchen.routing
# import chat.routing
# import accounts.routing

application = ProtocolTypeRouter({
    "http": django_asgi_app,
    "websocket": AuthMiddlewareStack(
        URLRouter(
            notifications.routing.websocket_urlpatterns
            # + kitchen.routing.websocket_urlpatterns
            # + chat.routing.websocket_urlpatterns
            # + accounts.routing.websocket_urlpatterns
        )
    ),
})
