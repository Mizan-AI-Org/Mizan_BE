from django.urls import path
from .consumers import RestaurantSettingsConsumer

websocket_urlpatterns = [
    # Final WS path: /ws/settings/restaurant/
    path('restaurant/', RestaurantSettingsConsumer.as_asgi()),
]