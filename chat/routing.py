from django.urls import re_path
from . import consumers

# Path is included at 'ws/chat/' in ASGI; keep pattern relative here
websocket_urlpatterns = [
    re_path(r'', consumers.ChatConsumer.as_asgi()),
]
