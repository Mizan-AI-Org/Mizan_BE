from django.urls import path

from . import views

urlpatterns = [
    path("config/", views.miya_config, name="miya-config"),
    path("chat/", views.miya_chat, name="miya-chat"),
    path("voice/", views.miya_voice, name="miya-voice"),
    path("instructions/", views.miya_instructions, name="miya-instructions"),
]
