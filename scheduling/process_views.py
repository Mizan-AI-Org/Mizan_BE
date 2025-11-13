from rest_framework import viewsets, status
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated
from django.utils import timezone

from .process_models import Process, ProcessTask, ProcessTaskStatus
from .process_serializers import ProcessSerializer, ProcessTaskSerializer
from staff.permissions import IsManagerOrReadOnly


class ProcessViewSet(viewsets.ModelViewSet):
    queryset = Process.objects.all().order_by('-created_at')
    serializer_class = ProcessSerializer
    permission_classes = [IsAuthenticated, IsManagerOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        restaurant_id = self.request.query_params.get('restaurant')
        status_param = self.request.query_params.get('status')
        is_active = self.request.query_params.get('is_active')
        if restaurant_id:
            qs = qs.filter(restaurant_id=restaurant_id)
        if status_param:
            qs = qs.filter(status=status_param)
        if is_active is not None:
            qs = qs.filter(is_active=is_active.lower() == 'true')
        return qs

    @action(detail=True, methods=['post'])
    def archive(self, request, pk=None):
        process = self.get_object()
        process.status = 'ARCHIVED'
        process.is_active = False
        process.save()
        return Response(self.get_serializer(process).data)


class ProcessTaskViewSet(viewsets.ModelViewSet):
    queryset = ProcessTask.objects.select_related('process').all()
    serializer_class = ProcessTaskSerializer
    permission_classes = [IsAuthenticated, IsManagerOrReadOnly]

    def get_queryset(self):
        qs = super().get_queryset()
        process_id = self.request.query_params.get('process')
        status_param = self.request.query_params.get('status')
        assigned_to = self.request.query_params.get('assigned_to')
        due_start = self.request.query_params.get('due_start')
        due_end = self.request.query_params.get('due_end')

        if process_id:
            qs = qs.filter(process_id=process_id)
        if status_param:
            qs = qs.filter(status=status_param)
        if assigned_to:
            qs = qs.filter(assigned_to_id=assigned_to)
        if due_start:
            qs = qs.filter(due_date__gte=due_start)
        if due_end:
            qs = qs.filter(due_date__lte=due_end)
        return qs.order_by('due_date', 'priority')

    @action(detail=True, methods=['post'])
    def start(self, request, pk=None):
        task = self.get_object()
        task.status = ProcessTaskStatus.IN_PROGRESS
        task.started_at = timezone.now()
        task.save()
        return Response(self.get_serializer(task).data)

    @action(detail=True, methods=['post'])
    def complete(self, request, pk=None):
        task = self.get_object()
        notes = request.data.get('notes')
        task.mark_completed(notes=notes)
        return Response(self.get_serializer(task).data)