from rest_framework import generics, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, F
from django.utils import timezone
from datetime import timedelta

from menu.models import MenuItem
from timeclock.models import ClockEvent
from scheduling.models import AssignedShift
from reporting.models import DailySalesReport, AttendanceReport, InventoryReport, Incident, LaborBudget, LaborPolicy
from reporting.serializers import (
    DailySalesReportSerializer,
    AttendanceReportSerializer,
    InventoryReportSerializer,
    IncidentSerializer,
    LaborBudgetSerializer,
    LaborPolicySerializer,
)
from rest_framework.permissions import IsAuthenticated
from rest_framework.decorators import api_view, permission_classes
from accounts.permissions import IsAdminOrSuperAdmin
from datetime import datetime

class DailySalesReportListAPIView(generics.ListAPIView):
    serializer_class = DailySalesReportSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return DailySalesReport.objects.filter(restaurant=self.request.user.restaurant).order_by('-date')

class DailySalesReportRetrieveAPIView(generics.RetrieveAPIView):
    serializer_class = DailySalesReportSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return DailySalesReport.objects.filter(restaurant=self.request.user.restaurant)

class AttendanceReportListAPIView(generics.ListAPIView):
    serializer_class = AttendanceReportSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return AttendanceReport.objects.filter(restaurant=self.request.user.restaurant).order_by('-date')

class AttendanceReportRetrieveAPIView(generics.RetrieveAPIView):
    serializer_class = AttendanceReportSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return AttendanceReport.objects.filter(restaurant=self.request.user.restaurant)

class InventoryReportListAPIView(generics.ListAPIView):
    serializer_class = InventoryReportSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return InventoryReport.objects.filter(restaurant=self.request.user.restaurant).order_by('-date')

class InventoryReportRetrieveAPIView(generics.RetrieveAPIView):
    serializer_class = InventoryReportSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]
    lookup_field = 'pk'

    def get_queryset(self):
        return InventoryReport.objects.filter(restaurant=self.request.user.restaurant)

class IncidentListAPIView(generics.ListAPIView):
    serializer_class = IncidentSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        return Incident.objects.filter(restaurant=self.request.user.restaurant).order_by('-created_at')

class IncidentCreateAPIView(generics.CreateAPIView):
    serializer_class = IncidentSerializer
    permission_classes = [IsAuthenticated]

    def perform_create(self, serializer):
        serializer.save(
            restaurant=self.request.user.restaurant,
            reporter=self.request.user
        )


# ----- Labor: planned vs actual, compliance, certifications, sales recommendation -----

@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrSuperAdmin])
def labor_planned_vs_actual(request):
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    if not start_date or not end_date:
        return Response({'error': 'start_date and end_date required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
    from reporting.services_labor import planned_vs_actual_hours
    data = planned_vs_actual_hours(request.user.restaurant, start_date, end_date)
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrSuperAdmin])
def labor_compliance(request):
    start_date = request.query_params.get('start_date')
    end_date = request.query_params.get('end_date')
    if not start_date or not end_date:
        return Response({'error': 'start_date and end_date required (YYYY-MM-DD)'}, status=status.HTTP_400_BAD_REQUEST)
    try:
        start_date = datetime.strptime(start_date, '%Y-%m-%d').date()
        end_date = datetime.strptime(end_date, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format'}, status=status.HTTP_400_BAD_REQUEST)
    from reporting.services_labor import overtime_and_compliance
    data = overtime_and_compliance(request.user.restaurant, start_date, end_date)
    return Response(data)


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrSuperAdmin])
def labor_certifications_expiring(request):
    within_days = request.query_params.get('within_days', '30')
    try:
        within_days = int(within_days)
    except ValueError:
        within_days = 30
    from reporting.services_labor import certifications_expiring
    data = certifications_expiring(request.user.restaurant, within_days=within_days)
    return Response({'certifications_expiring': data})


@api_view(['GET'])
@permission_classes([IsAuthenticated, IsAdminOrSuperAdmin])
def labor_sales_recommendation(request):
    week_start = request.query_params.get('week_start')
    if week_start:
        try:
            week_start = datetime.strptime(week_start, '%Y-%m-%d').date()
        except ValueError:
            week_start = None
    from reporting.services_labor import sales_labor_recommendation
    data = sales_labor_recommendation(request.user.restaurant, week_start=week_start)
    return Response(data)


class LaborBudgetListCreateAPIView(generics.ListCreateAPIView):
    serializer_class = LaborBudgetSerializer
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get_queryset(self):
        return LaborBudget.objects.filter(restaurant=self.request.user.restaurant).order_by('-period_end')

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)


class LaborPolicyAPIView(APIView):
    permission_classes = [IsAuthenticated, IsAdminOrSuperAdmin]

    def get(self, request):
        policy, _ = LaborPolicy.objects.get_or_create(restaurant=request.user.restaurant)
        return Response(LaborPolicySerializer(policy).data)

    def patch(self, request):
        policy, _ = LaborPolicy.objects.get_or_create(restaurant=request.user.restaurant)
        s = LaborPolicySerializer(policy, data=request.data, partial=True)
        s.is_valid(raise_exception=True)
        s.save()
        return Response(s.data)