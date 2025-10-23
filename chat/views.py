from rest_framework import generics, permissions
from .models import Message
from .serializers import MessageSerializer

class MessageListAPIView(generics.ListAPIView):
    serializer_class = MessageSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        room_name = self.request.query_params.get('room_name')
        if room_name:
            return Message.objects.filter(room_name=room_name, sender__restaurant=self.request.user.restaurant).order_by('-timestamp')
        # For now, only allow fetching messages by room_name
        return Message.objects.none()
