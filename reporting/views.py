from rest_framework import generics, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, F
from django.utils import timezone
from datetime import timedelta

from menu.models import MenuItem
from pos.models import Order, OrderItem
from timeclock.models import ClockEvent
from scheduling.models import AssignedShift
from reporting.models import DailySalesReport, AttendanceReport, InventoryReport
from reporting.serializers import DailySalesReportSerializer, AttendanceReportSerializer, InventoryReportSerializer
from rest_framework.permissions import IsAuthenticated
from accounts.permissions import IsAdminOrSuperAdmin

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