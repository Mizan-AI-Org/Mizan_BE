from rest_framework import generics, permissions, status
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from .models import DailyKPI, Alert, Task
from .serializers import DailyKPISerializer, AlertSerializer, TaskSerializer
from accounts.permissions import IsAdminOrSuperAdmin, IsAdminOrManager
from scheduling.models import AssignedShift

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


@api_view(["POST"])
@permission_classes([permissions.IsAuthenticated, IsAdminOrManager])
def mark_shift_no_show(request):
    """
    Mark an assigned shift as no-show. Used from Critical issues & attendance dashboard.
    Body: { "shift_id": "<uuid>" }
    """
    shift_id = request.data.get("shift_id")
    if not shift_id:
        return Response({"error": "shift_id is required"}, status=status.HTTP_400_BAD_REQUEST)
    restaurant = getattr(request.user, "restaurant", None)
    if not restaurant:
        return Response({"error": "No restaurant"}, status=status.HTTP_403_FORBIDDEN)
    shift = AssignedShift.objects.filter(
        id=shift_id,
        schedule__restaurant=restaurant,
    ).select_related("staff").first()
    if not shift:
        return Response({"error": "Shift not found"}, status=status.HTTP_404_NOT_FOUND)
    if shift.status == "NO_SHOW":
        return Response({"success": True, "message": "Already marked as no-show"}, status=status.HTTP_200_OK)
    shift.status = "NO_SHOW"
    shift.save(update_fields=["status"])
    return Response({
        "success": True,
        "message": "Shift marked as no-show",
        "shift_id": str(shift.id),
    }, status=status.HTTP_200_OK)
