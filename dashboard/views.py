from rest_framework import generics, permissions, status
from rest_framework.exceptions import ValidationError
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.db.models import Q
from django.utils import timezone
from django.utils.dateparse import parse_date
from .models import DailyKPI, Alert, Task, StaffCapturedOrder
from .serializers import (
    DailyKPISerializer,
    AlertSerializer,
    TaskSerializer,
    StaffCapturedOrderSerializer,
    StaffCapturedOrderPartialUpdateSerializer,
)
from accounts.permissions import IsAdminOrSuperAdmin, IsAdminOrManager
from scheduling.models import AssignedShift

class DailyKPIListAPIView(generics.ListAPIView):
    serializer_class = DailyKPISerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return DailyKPI.objects.filter(restaurant=self.request.user.restaurant).order_by('-date')


class StaffCapturedOrderListCreateAPIView(generics.ListCreateAPIView):
    """List and create staff-captured orders (Miya or manual) for the current restaurant."""

    serializer_class = StaffCapturedOrderSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = None

    def get_queryset(self):
        user = self.request.user
        restaurant = getattr(user, "restaurant", None)
        if not restaurant:
            return StaffCapturedOrder.objects.none()
        qs = StaffCapturedOrder.objects.filter(restaurant=restaurant).select_related("recorded_by")

        date_from_str = self.request.query_params.get("date_from")
        date_to_str = self.request.query_params.get("date_to")
        if date_from_str and date_to_str:
            d0 = parse_date(date_from_str.strip())
            d1 = parse_date(date_to_str.strip())
            if not d0 or not d1:
                raise ValidationError(
                    {"date_from": "Invalid date. Use YYYY-MM-DD.", "date_to": "Invalid date. Use YYYY-MM-DD."}
                )
            if d0 > d1:
                raise ValidationError({"date_to": "End date must be on or after start date."})
            return qs.filter(created_at__date__gte=d0, created_at__date__lte=d1).order_by("-created_at")

        date_str = self.request.query_params.get("date")
        if date_str:
            d = parse_date(date_str.strip())
            if not d:
                raise ValidationError({"date": "Invalid date. Use YYYY-MM-DD."})
            return qs.filter(created_at__date=d).order_by("-created_at")

        today = timezone.localdate()
        if self.request.query_params.get("active") in ("1", "true", "yes"):
            # Today’s orders (any status) plus older rows still open (not fulfilled / cancelled).
            qs = qs.filter(
                Q(created_at__date=today)
                | Q(fulfillment_status__in=["NEW", "IN_PROGRESS"])
            )
        elif self.request.query_params.get("today") in ("1", "true", "yes"):
            qs = qs.filter(created_at__date=today)
        return qs.order_by("-created_at")

    def perform_create(self, serializer):
        user = self.request.user
        restaurant = getattr(user, "restaurant", None)
        if not restaurant:
            from rest_framework.exceptions import ValidationError
            raise ValidationError({"detail": "No restaurant context for this user."})
        serializer.save(restaurant=restaurant, recorded_by=user)


class StaffCapturedOrderRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    """GET one order; PATCH fields and/or status; DELETE (managers only)."""

    permission_classes = [permissions.IsAuthenticated]
    lookup_field = "pk"

    def get_queryset(self):
        user = self.request.user
        restaurant = getattr(user, "restaurant", None)
        if not restaurant:
            return StaffCapturedOrder.objects.none()
        return StaffCapturedOrder.objects.filter(restaurant=restaurant).select_related("recorded_by")

    def get_permissions(self):
        if self.request.method == "DELETE":
            return [permissions.IsAuthenticated(), IsAdminOrManager()]
        return [permissions.IsAuthenticated()]

    def get_serializer_class(self):
        if self.request.method in ("PATCH", "PUT"):
            return StaffCapturedOrderPartialUpdateSerializer
        return StaffCapturedOrderSerializer


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
