from rest_framework import viewsets, status
from rest_framework.response import Response
from .models import Schedule
from .serializers import ScheduleSerializer

class ScheduleViewSet(viewsets.ModelViewSet):
    queryset = Schedule.objects.all()
    serializer_class = ScheduleSerializer

    def get_queryset(self):
        # Optionally filter schedules by staff member, or date range
        # For now, return all schedules
        return Schedule.objects.all()

    def perform_create(self, serializer):
        serializer.save()

    def perform_update(self, serializer):
        serializer.save()

    def perform_destroy(self, instance):
        instance.delete()
