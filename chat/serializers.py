from rest_framework import serializers
from .models import Message
from accounts.serializers import UserSerializer # Assuming UserSerializer exists

class MessageSerializer(serializers.ModelSerializer):
    sender_info = UserSerializer(source='sender', read_only=True)
    recipient_info = UserSerializer(source='recipient', read_only=True)

    class Meta:
        model = Message
        fields = ('id', 'sender', 'sender_info', 'recipient', 'recipient_info', 'room_name', 'content', 'timestamp', 'is_read')
        read_only_fields = ('sender', 'timestamp', 'is_read', 'sender_info', 'recipient_info')
