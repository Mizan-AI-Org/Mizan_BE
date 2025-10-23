from django.shortcuts import render
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.decorators import api_view, permission_classes
from .models import Notification, DeviceToken
from .serializers import NotificationSerializer, DeviceTokenSerializer
from rest_framework.views import APIView

# Create your views here.

class NotificationList(generics.ListAPIView):
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return Notification.objects.filter(recipient=self.request.user).order_by('-created_at')

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_notification_as_read(request, notification_id):
    try:
        notification = Notification.objects.get(id=notification_id, recipient=request.user)
        notification.is_read = True
        notification.save()
        return Response(NotificationSerializer(notification).data)
    except Notification.DoesNotExist:
        return Response({'error': 'Notification not found'}, status=status.HTTP_404_NOT_FOUND)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_all_notifications_as_read(request):
    Notification.objects.filter(recipient=request.user, is_read=False).update(is_read=True)
    return Response({'message': 'All notifications marked as read'})

class DeviceTokenRegisterAPIView(generics.CreateAPIView):
    serializer_class = DeviceTokenSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        # Ensure that a token is unique per user
        DeviceToken.objects.filter(user=self.request.user, token=serializer.validated_data['token']).delete()
        serializer.save(user=self.request.user)

class DeviceTokenUnregisterAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        token = request.data.get('token')
        if not token:
            return Response({'error': 'Token is required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Delete the specific token for the user
        deleted_count, _ = DeviceToken.objects.filter(user=request.user, token=token).delete()

        if deleted_count > 0:
            return Response({'message': 'Device token unregistered successfully.'}, status=status.HTTP_200_OK)
        else:
            return Response({'error': 'Device token not found for this user.'}, status=status.HTTP_404_NOT_FOUND)
