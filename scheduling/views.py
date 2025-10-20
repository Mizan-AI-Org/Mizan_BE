from rest_framework import status, permissions
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from django.utils import timezone
from django.shortcuts import get_object_or_404
from datetime import timedelta
from channels.layers import get_channel_layer
from asgiref.sync import async_to_sync
from .models import ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift
from .serializers import (
    ScheduleTemplateSerializer, TemplateShiftSerializer, 
    WeeklyScheduleSerializer, AssignedShiftSerializer, 
    ShiftSwapRequestSerializer, ShiftSwapRequestCreateSerializer
)
from accounts.models import CustomUser
from rest_framework import generics
from rest_framework.views import APIView
from notifications.models import Notification
from notifications.serializers import NotificationSerializer
from .models import ShiftSwapRequest
from rest_framework import serializers

class IsAdminOrManager(permissions.BasePermission):
    """Custom permission to only allow admins or managers to edit/view schedules."""
    def has_permission(self, request, view):
        return request.user and request.user.is_authenticated and request.user.role in ['ADMIN', 'SUPER_ADMIN', 'MANAGER']

class ScheduleTemplateListCreateView(generics.ListCreateAPIView):
    queryset = ScheduleTemplate.objects.all()
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant)

    def perform_create(self, serializer):
        serializer.save(restaurant=self.request.user.restaurant)

class ScheduleTemplateDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = ScheduleTemplate.objects.all()
    serializer_class = ScheduleTemplateSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return ScheduleTemplate.objects.filter(restaurant=self.request.user.restaurant)

class WeeklyScheduleGenerateView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def post(self, request, *args, **kwargs):
        template_id = request.data.get('template_id')
        week_start_str = request.data.get('week_start')

        if not template_id or not week_start_str:
            return Response({'error': 'template_id and week_start are required'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            template = ScheduleTemplate.objects.get(id=template_id, restaurant=request.user.restaurant)
            week_start = timezone.datetime.strptime(week_start_str, '%Y-%m-%d').date()
        except ScheduleTemplate.DoesNotExist:
            return Response({'error': 'Schedule template not found'}, status=status.HTTP_404_NOT_FOUND)
        except ValueError:
            return Response({'error': 'Invalid date format for week_start. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

        # Ensure week_start is a Monday
        if week_start.weekday() != 0:
            return Response({'error': 'Week start date must be a Monday.'}, status=status.HTTP_400_BAD_REQUEST)

        week_end = week_start + timedelta(days=6)

        # Check if a schedule already exists for this week
        if WeeklySchedule.objects.filter(restaurant=request.user.restaurant, week_start=week_start).exists():
            return Response({'error': 'Schedule already exists for this week.'}, status=status.HTTP_400_BAD_REQUEST)

        weekly_schedule = WeeklySchedule.objects.create(
            restaurant=request.user.restaurant,
            week_start=week_start,
            week_end=week_end,
            is_published=False
        )

        # Generate assigned shifts from template shifts
        for template_shift in template.shifts.all():
            # Find staff for this role (simplified: pick any active staff for now)
            eligible_staff = CustomUser.objects.filter(
                restaurant=request.user.restaurant,
                role=template_shift.role,
                is_active=True
            )
            # TODO: Implement more sophisticated staff assignment logic (e.g., availability, preferences)
            if eligible_staff.exists():
                for i in range(template_shift.required_staff):
                    # Assign to the first available staff for simplicity
                    staff_member = eligible_staff.first()
                    
                    # Calculate shift_date
                    shift_date = week_start + timedelta(days=template_shift.day_of_week)

                    AssignedShift.objects.create(
                        schedule=weekly_schedule,
                        staff=staff_member,
                        shift_date=shift_date,
                        start_time=template_shift.start_time,
                        end_time=template_shift.end_time,
                        role=template_shift.role,
                    )
                    
                    # Send notification to assigned staff
                    message = f"You have been assigned a new shift: {shift_date} {template_shift.start_time}-{template_shift.end_time} ({template_shift.role})"
                    notification = Notification.objects.create(
                        recipient=staff_member,
                        message=message,
                        notification_type='SHIFT_UPDATE'
                    )
                    channel_layer = get_channel_layer()
                    async_to_sync(channel_layer.group_send)(
                        f'user_{staff_member.id}_notifications',
                        {
                            'type': 'send_notification',
                            'notification': NotificationSerializer(notification).data
                        }
                    )

        serializer = WeeklyScheduleSerializer(weekly_schedule)
        return Response(serializer.data, status=status.HTTP_201_CREATED)

class WeeklyScheduleDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = WeeklySchedule.objects.all()
    serializer_class = WeeklyScheduleSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return WeeklySchedule.objects.filter(restaurant=self.request.user.restaurant)

class AssignedShiftDetailView(generics.RetrieveUpdateDestroyAPIView):
    queryset = AssignedShift.objects.all()
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return AssignedShift.objects.filter(schedule__restaurant=self.request.user.restaurant)

class MyAssignedShiftsView(generics.ListAPIView):
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        # Get current user's schedule for the next 7 days
        today = timezone.now().date()
        next_week = today + timedelta(days=7)
        return AssignedShift.objects.filter(
            staff=self.request.user,
            shift_date__gte=today,
            shift_date__lte=next_week
        ).order_by('shift_date', 'start_time')

class ShiftDetailAPIView(generics.RetrieveUpdateDestroyAPIView):
    queryset = AssignedShift.objects.all()
    serializer_class = AssignedShiftSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return AssignedShift.objects.filter(schedule__restaurant=self.request.user.restaurant)

    def perform_update(self, serializer):
        # Get the old shift data before update for comparison
        old_shift = self.get_object()
        updated_shift = serializer.save()

        # Check if the staff or shift details have changed
        if (old_shift.staff != updated_shift.staff or
            old_shift.shift_date != updated_shift.shift_date or
            old_shift.start_time != updated_shift.start_time or
            old_shift.end_time != updated_shift.end_time or
            old_shift.role != updated_shift.role):
            
            message = f"Your shift on {updated_shift.shift_date} ({updated_shift.start_time}-{updated_shift.end_time}) has been updated."
            notification = Notification.objects.create(
                recipient=updated_shift.staff,
                message=message,
                notification_type='SHIFT_UPDATE'
            )
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'user_{updated_shift.staff.id}_notifications',
                {
                    'type': 'send_notification',
                    'notification': NotificationSerializer(notification).data
                }
            )

@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def weekly_schedule_view(request):
    week_start_str = request.GET.get('week_start')
    if not week_start_str:
        return Response({'error': 'week_start parameter is required'}, status=status.HTTP_400_BAD_REQUEST)
    
    try:
        week_start = timezone.datetime.strptime(week_start_str, '%Y-%m-%d').date()
    except ValueError:
        return Response({'error': 'Invalid date format for week_start. Use YYYY-MM-DD.'}, status=status.HTTP_400_BAD_REQUEST)

    restaurant = request.user.restaurant
    
    weekly_schedule = WeeklySchedule.objects.filter(
        restaurant=restaurant,
        week_start=week_start
    ).first()

    if not weekly_schedule:
        return Response({'message': 'No schedule found for this week.', 'schedule': None}, status=status.HTTP_200_OK)
    
    serializer = WeeklyScheduleSerializer(weekly_schedule)
    return Response(serializer.data, status=status.HTTP_200_OK)

class ShiftSwapRequestCreateView(generics.CreateAPIView):
    queryset = ShiftSwapRequest.objects.all()
    serializer_class = ShiftSwapRequestCreateSerializer
    permission_classes = [permissions.IsAuthenticated]

    def perform_create(self, serializer):
        shift_to_swap = serializer.validated_data['shift_to_swap']
        if shift_to_swap.staff != self.request.user:
            raise serializers.ValidationError("You can only request to swap your own shifts.")

        swap_request = serializer.save(requester=self.request.user)
        
        # Notify the receiver (if specified) or management
        message = f"New shift swap request for {swap_request.shift_to_swap.shift_date} from {self.request.user.first_name}."
        recipient = swap_request.receiver if swap_request.receiver else None # For now, notify the receiver or send to all managers

        if recipient:
            notification = Notification.objects.create(
                recipient=recipient,
                message=message,
                notification_type='BREAK_REQUEST' # Using BREAK_REQUEST for now, should be SHIFT_SWAP
            )
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'user_{recipient.id}_notifications',
                {
                    'type': 'send_notification',
                    'notification': NotificationSerializer(notification).data
                }
            )
        # TODO: Notify managers if no specific receiver

class MyShiftSwapRequestsView(generics.ListAPIView):
    serializer_class = ShiftSwapRequestSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        return ShiftSwapRequest.objects.filter(requester=self.request.user).order_by('-created_at')

class ManagerShiftSwapRequestsView(generics.ListAPIView):
    serializer_class = ShiftSwapRequestSerializer
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def get_queryset(self):
        return ShiftSwapRequest.objects.filter(
            shift_to_swap__schedule__restaurant=self.request.user.restaurant,
            status='PENDING'
        ).order_by('-created_at')

class ShiftSwapRequestActionView(APIView):
    permission_classes = [permissions.IsAuthenticated, IsAdminOrManager]

    def post(self, request, pk, action):
        swap_request = get_object_or_404(
            ShiftSwapRequest,
            pk=pk,
            shift_to_swap__schedule__restaurant=request.user.restaurant
        )

        if action == 'approve':
            if swap_request.status != 'PENDING':
                return Response({'error': 'Only pending requests can be approved.'}, status=status.HTTP_400_BAD_REQUEST)
            
            # Perform the swap: assign shift to receiver, or make it an open shift
            original_staff = swap_request.shift_to_swap.staff
            if swap_request.receiver:
                swap_request.shift_to_swap.staff = swap_request.receiver
                swap_request.shift_to_swap.save()
                swap_request.status = 'APPROVED'
                swap_request.save()
                
                # Notify both original staff and new staff
                message_requester = f"Your shift swap request for {swap_request.shift_to_swap.shift_date} has been APPROVED. {swap_request.receiver.first_name} will take your shift."
                notification_requester = Notification.objects.create(
                    recipient=original_staff,
                    message=message_requester,
                    notification_type='SHIFT_UPDATE'
                )
                channel_layer = get_channel_layer()
                async_to_sync(channel_layer.group_send)(
                    f'user_{original_staff.id}_notifications',
                    {
                        'type': 'send_notification',
                        'notification': NotificationSerializer(notification_requester).data
                    }
                )

                message_receiver = f"You have been assigned a new shift on {swap_request.shift_to_swap.shift_date} as per your swap request."
                notification_receiver = Notification.objects.create(
                    recipient=swap_request.receiver,
                    message=message_receiver,
                    notification_type='SHIFT_UPDATE'
                )
                async_to_sync(channel_layer.group_send)(
                    f'user_{swap_request.receiver.id}_notifications',
                    {
                        'type': 'send_notification',
                        'notification': NotificationSerializer(notification_receiver).data
                    }
                )

            else: # Open request, make the shift available
                # TODO: Implement logic for open shifts
                return Response({'error': 'Open shift swap not yet implemented.'}, status=status.HTTP_501_NOT_IMPLEMENTED)

            return Response(ShiftSwapRequestSerializer(swap_request).data)

        elif action == 'reject':
            if swap_request.status != 'PENDING':
                return Response({'error': 'Only pending requests can be rejected.'}, status=status.HTTP_400_BAD_REQUEST)

            swap_request.status = 'REJECTED'
            swap_request.save()

            # Notify requester
            message = f"Your shift swap request for {swap_request.shift_to_swap.shift_date} has been REJECTED."
            notification = Notification.objects.create(
                recipient=swap_request.requester,
                message=message,
                notification_type='SHIFT_UPDATE'
            )
            channel_layer = get_channel_layer()
            async_to_sync(channel_layer.group_send)(
                f'user_{swap_request.requester.id}_notifications',
                {
                    'type': 'send_notification',
                    'notification': NotificationSerializer(notification).data
                }
            )
            return Response(ShiftSwapRequestSerializer(swap_request).data)
        
        return Response({'error': 'Invalid action'}, status=status.HTTP_400_BAD_REQUEST)