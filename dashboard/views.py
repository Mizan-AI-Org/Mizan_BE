from rest_framework import generics, permissions
from .models import DailyKPI, Alert, Task
from .serializers import DailyKPISerializer, AlertSerializer, TaskSerializer
from accounts.permissions import IsAdminOrSuperAdmin, IsAdminOrManager # Corrected imports

class DailyKPIListAPIView(generics.ListAPIView):
    serializer_class = DailyKPISerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return DailyKPI.objects.filter(restaurant=self.request.user.restaurant).order_by('-date')

class AlertListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = AlertSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return Alert.objects.filter(restaurant=self.request.user.restaurant, is_resolved=False).order_by('-created_at')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class AlertRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AlertSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Alert.objects.filter(restaurant=self.request.user.restaurant)

class TaskListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return Task.objects.filter(restaurant=self.request.user.restaurant).order_by('due_date', 'priority')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class TaskRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TaskSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return Task.objects.filter(restaurant=self.request.user.restaurant)
