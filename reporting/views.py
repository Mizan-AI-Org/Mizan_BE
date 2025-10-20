from rest_framework import generics, status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from django.shortcuts import get_object_or_404
from django.db.models import Sum, Count, F
from django.utils import timezone
from datetime import timedelta

from .models import Report
from .serializers import ReportSerializer

from accounts.permissions import IsAdminOrManager  # Assuming you have this permission
from staff.models import Order, OrderItem, Product
from timeclock.models import ClockEvent
from scheduling.models import AssignedShift

class ReportListAPIView(generics.ListAPIView):
    serializer_class = ReportSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return Report.objects.filter(restaurant=self.request.user.restaurant).order_by('-generated_at')

class ReportGenerateAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def post(self, request):
        report_type = request.data.get('report_type')
        start_date_str = request.data.get('start_date')
        end_date_str = request.data.get('end_date')

        if not all([report_type, start_date_str, end_date_str]):
            return Response({'error': 'report_type, start_date, and end_date are required.'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            start_date = timezone.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = timezone.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError:
            return Response({'error': 'Invalid date format. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        report_data = {}

        if report_type == 'SALES_SUMMARY':
            orders = Order.objects.filter(
                restaurant=request.user.restaurant,
                created_at__date__range=[start_date, end_date],
                status__in=['COMPLETED']
            )
            total_sales = orders.aggregate(total_amount=Sum('total_amount'))['total_amount'] or 0.00
            total_orders = orders.count()
            average_order_value = total_sales / total_orders if total_orders > 0 else 0.00

            # Top selling products
            top_products = OrderItem.objects.filter(order__in=orders) \
                                .values('product__name') \
                                .annotate(total_quantity=Sum('quantity')) \
                                .order_by('-total_quantity')[:5]

            report_data = {
                'total_sales': float(total_sales),
                'total_orders': total_orders,
                'average_order_value': float(average_order_value),
                'top_products': list(top_products)
            }
        elif report_type == 'ATTENDANCE_OVERVIEW':
            # Get all staff for the restaurant
            staff_members = request.user.restaurant.customuser_set.filter(is_staff=True, is_active=True)
            attendance_records = []

            for staff in staff_members:
                clock_events = ClockEvent.objects.filter(
                    staff=staff,
                    timestamp__date__range=[start_date, end_date]
                ).order_by('timestamp')

                total_hours_worked = 0
                current_session_start = None

                for event in clock_events:
                    if event.event_type == 'in':
                        current_session_start = event.timestamp
                    elif event.event_type == 'out' and current_session_start:
                        duration = event.timestamp - current_session_start
                        total_hours_worked += duration.total_seconds() / 3600
                        current_session_start = None
                
                attendance_records.append({
                    'staff_name': f'{staff.first_name} {staff.last_name}',
                    'staff_role': staff.role,
                    'total_hours_worked': round(total_hours_worked, 2),
                })
            report_data = {
                'attendance_summary': attendance_records
            }
        elif report_type == 'INVENTORY_STATUS':
            # This would typically require an Inventory model, 
            # but for now, let's mock it or use product list as a proxy
            products = Product.objects.filter(restaurant=request.user.restaurant, is_active=True).values('name', 'base_price', 'category__name')
            report_data = {
                'product_list': list(products),
                'note': 'Full inventory management requires dedicated models.'
            }
        elif report_type == 'SHIFT_PERFORMANCE':
            # Requires more complex logic, possibly comparing scheduled vs actual hours,
            # or sales generated per shift/staff. For now, a basic overview.
            shifts = AssignedShift.objects.filter(
                schedule__restaurant=request.user.restaurant,
                shift_date__range=[start_date, end_date]
            ).select_related('staff')

            shift_performance_data = []
            for shift in shifts:
                shift_performance_data.append({
                    'staff_name': f'{shift.staff.first_name} {shift.staff.last_name}',
                    'shift_date': shift.shift_date,
                    'start_time': shift.start_time,
                    'end_time': shift.end_time,
                    'role': shift.role,
                    'scheduled_hours': shift.actual_hours, # Assuming actual_hours property calculates scheduled duration
                    # You could add more performance metrics here (e.g., sales per shift, clock-in accuracy)
                })
            report_data = {
                'shift_performance_summary': shift_performance_data
            }
        else:
            return Response({'error': 'Invalid report type.'}, status=status.HTTP_400_BAD_REQUEST)
        
        # Save the generated report
        report = Report.objects.create(
            restaurant=request.user.restaurant,
            report_type=report_type,
            data=report_data,
            generated_by=request.user
        )

        return Response(ReportSerializer(report).data, status=status.HTTP_201_CREATED)

class ReportDetailAPIView(generics.RetrieveAPIView):
    serializer_class = ReportSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]
    queryset = Report.objects.all()
    lookup_field = 'pk'

    def get_queryset(self):
        return Report.objects.filter(restaurant=self.request.user.restaurant)