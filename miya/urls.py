from django.urls import path

from . import views
from . import views_mastra

urlpatterns = [
    path("config/", views.miya_config, name="miya-config"),
    path("chat/", views.miya_chat, name="miya-chat"),
    path("voice-chat/", views.miya_voice_chat, name="miya-voice-chat"),
    path("voice/", views.miya_voice, name="miya-voice"),
    path("instructions/", views.miya_instructions, name="miya-instructions"),
    path("mastra/execute-tool/", views_mastra.mastra_execute_tool, name="miya-mastra-execute-tool"),
    path("mastra/tools-catalog/", views_mastra.mastra_tools_catalog, name="miya-mastra-tools-catalog"),
]
