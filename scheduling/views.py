from rest_framework import generics, permissions, status
from rest_framework.response import Response
from django.utils import timezone
from django.db.models import Q

from .models import ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift, ShiftSwapRequest
from .serializers import (
    ScheduleTemplateSerializer,
    TemplateShiftSerializer,
    WeeklyScheduleSerializer,
    AssignedShiftSerializer,
    ShiftSwapRequestSerializer,
)
from accounts.views import IsAdmin, IsSuperAdmin, IsManagerOrAdmin


class ScheduleTemplateListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant).order_by('name')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class ScheduleTemplateRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant)

class TemplateShiftListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = TemplateShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        template_id = self.kwargs.get('template_pk')
        return TemplateShift.objects.filter(template__id=template_id, template__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        template_id = self.kwargs.get('template_pk')
        template = ScheduleTemplate.objects.get(id=template_id, restaurant=self.request.user.restaurant)
        serializer.save(template=template)

class TemplateShiftRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = TemplateShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        template_id = self.kwargs.get('template_pk')
        return TemplateShift.objects.filter(template__id=template_id, template__restaurant=self.request.user.restaurant)

class WeeklyScheduleListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant).order_by('-week_start')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class WeeklyScheduleRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant)

class AssignedShiftListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]

    def get_queryset(self):
        schedule_id = self.kwargs.get('schedule_pk')
        return AssignedShift.objects.filter(schedule__id=schedule_id, schedule__restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        schedule_id = self.kwargs.get('schedule_pk')
        schedule = WeeklySchedule.objects.get(id=schedule_id, restaurant=self.request.user.restaurant)
        serializer.save(schedule=schedule, restaurant=self.request.user.restaurant)

class AssignedShiftRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsManagerOrAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        schedule_id = self.kwargs.get('schedule_pk')
        return AssignedShift.objects.filter(schedule__id=schedule_id, schedule__restaurant=self.request.user.restaurant)

class ShiftSwapRequestListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = ShiftSwapRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        user = self.request.user
        return ShiftSwapRequest.objects.filter(
            Q(requester=user) | Q(receiver=user) | Q(receiver__isnull=True, shift_to_swap__schedule__restaurant=user.restaurant)
        ).order_by('-created_at')

    def perform_create(self, serializer):
        shift_to_swap = serializer.validated_data['shift_to_swap']
        if shift_to_swap.staff != self.request.user:
            raise permissions.ValidationError("You can only request to swap your own shifts.")
        serializer.save(requester=self.request.user)

class ShiftSwapRequestRetrieveUpdateDestroyAPIView(generics.RetrieveUpdateDestroyAPIView):
    serializer_class = ShiftSwapRequestSerializer
    permission_classes = [permissions.IsAuthenticated]
    lookup_field = 'pk'

    def get_queryset(self):
        user = self.request.user
        # Users can retrieve/update/delete their own requests or requests where they are the receiver
        return ShiftSwapRequest.objects.filter(Q(requester=user) | Q(receiver=user), shift_to_swap__schedule__restaurant=user.restaurant)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        if instance.status != 'PENDING':
            return Response({'detail': 'Cannot update a non-pending swap request.'}, status=status.HTTP_400_BAD_REQUEST)

        # Only requester can cancel, only receiver or admin can approve/reject
        new_status = request.data.get('status')

        if new_status == 'CANCELLED':
            if instance.requester != request.user:
                return Response({'detail': 'Only the requester can cancel this request.'}, status=status.HTTP_403_FORBIDDEN)
        elif new_status in ['APPROVED', 'REJECTED']:
            if instance.receiver != request.user and not (request.user.role == 'ADMIN' or request.user.role == 'SUPER_ADMIN'):
                return Response({'detail': 'Only the receiver or an admin can approve/reject this request.'}, status=status.HTTP_403_FORBIDDEN)

            if new_status == 'APPROVED':
                # Logic to swap shifts:
                # 1. Update original shift to be assigned to the receiver
                original_shift = instance.shift_to_swap
                original_shift.staff = instance.receiver
                original_shift.save()

                # 2. If receiver also offered a shift, update that one too
                # This is a simplified model assuming a direct swap, more complex logic for 'open' requests needed
                # For now, if receiver is set, we assume they take the shift.

                # 3. Mark all other pending requests for this shift as CANCELLED
                ShiftSwapRequest.objects.filter(
                    shift_to_swap=original_shift,
                    status='PENDING'
                ).exclude(pk=instance.pk).update(status='CANCELLED')

        serializer = self.get_serializer(instance, data=request.data, partial=True)
        serializer.is_valid(raise_exception=True)
        self.perform_update(serializer)

        return Response(serializer.data)