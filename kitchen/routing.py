from django.urls import re_path
from . import consumers

# Path is included at 'ws/kitchen/' in ASGI; keep pattern relative here
websocket_urlpatterns = [
    re_path(r'', consumers.KitchenConsumer.as_asgi()),
]
