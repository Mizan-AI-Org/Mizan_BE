from rest_framework import generics, status, permissions
from rest_framework.decorators import api_view, permission_classes, parser_classes
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination
from django.utils import timezone
from django.db import transaction
from django.db.models import Q
from django.shortcuts import get_object_or_404
from accounts.permissions import IsAdminOrManager
from rest_framework.parsers import MultiPartParser, FormParser
import sys
import logging
import json

logger = logging.getLogger(__name__)
from .models import Notification, NotificationPreference, DeviceToken, NotificationAttachment, NotificationIssue
from .serializers import (
    NotificationSerializer, 
    NotificationPreferenceSerializer,
    DeviceTokenSerializer,
    AnnouncementCreateSerializer
)
from .services import notification_service
from scheduling.audit import AuditTrailService, AuditActionType, AuditSeverity
from core.utils import build_tenant_context
from .models import WhatsAppSession
from accounts.models import CustomUser
from accounts.services import UserManagementService, sync_user_to_lua_agent
from timeclock.models import ClockEvent
from scheduling.models import ShiftTask
from django.conf import settings as dj_settings


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationListView(generics.ListAPIView):
    """List notifications for the authenticated user"""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        user = self.request.user
        # Be defensive: eagerly load related sender/recipient and attachments
        # to avoid lazy-loading surprises that can bubble up during serialization
        queryset = (
            Notification.objects
            .filter(recipient=user)
            .select_related('recipient', 'sender')
            .prefetch_related('attachments')
            .order_by('-created_at')
        )
        
        # Filter by read status
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(read_at__isnull=False)
            else:
                queryset = queryset.filter(read_at__isnull=True)
        
        # Filter by notification type
        notification_type = self.request.query_params.get('type')
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)
        
        # Filter by priority
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        
        return queryset

    def list(self, request, *args, **kwargs):
        """Ensure the endpoint never 500s; return an empty, paginated payload on error."""
        try:
            return super().list(request, *args, **kwargs)
        except Exception as e:
            # We deliberately avoid exposing internals to the client. This preserves
            # dashboard stability if a bad row or attachment causes a serialization error.
            return Response({
                'count': 0,
                'next': None,
                'previous': None,
                'results': [],
                'success': False,
                'error': 'notifications_unavailable'
            }, status=status.HTTP_200_OK)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_notification_read(request, notification_id):
    """Mark a specific notification as read"""
    try:
        notification = get_object_or_404(
            Notification, 
            id=notification_id, 
            recipient=request.user
        )
        
        if not notification.read_at:
            notification.mark_as_read()
            
        return Response({
            'success': True,
            'message': 'Notification marked as read',
            'read_at': notification.read_at
        })
        
    except Exception as e:
            return Response({
                'success': False,
                'error': str(e)
            }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([IsAdminOrManager])
@parser_classes([MultiPartParser, FormParser])
def create_announcement(request):
    """Create and send announcement to all restaurant staff"""
    print("Creating announcement...", file=sys.stderr)
    try:
        ctx = build_tenant_context(request)
        if not ctx:
            return Response({'success': False, 'error': 'Authentication required'}, status=status.HTTP_401_UNAUTHORIZED)
        serializer = AnnouncementCreateSerializer(data=request.data)
        
        if not serializer.is_valid():
            return Response({
                'success': False,
                'errors': serializer.errors
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Create notifications for all staff inside a transaction
        with transaction.atomic():
            notifications = serializer.create_notifications(sender=request.user)
            # Handle attachments if provided
            files = request.FILES.getlist('attachments')
            if files:
                for notification in notifications:
                    for f in files:
                        att = NotificationAttachment(
                            notification=notification,
                            file=f,
                            original_name=getattr(f, 'name', ''),
                            content_type=getattr(f, 'content_type', ''),
                            file_size=getattr(f, 'size', 0),
                        )
                        att.save()
        targeted = bool(
            serializer.validated_data.get('recipients_staff_ids') or 
            serializer.validated_data.get('recipients_departments')
        )

        # Handle scheduling: if schedule_for is set in the future, mark as scheduled and do not send now
        schedule_for = serializer.validated_data.get('schedule_for')
        if schedule_for and schedule_for > timezone.now():
            for notification in notifications:
                notification.delivery_status = {
                    'status': 'SCHEDULED',
                    'scheduled_for': schedule_for.isoformat(),
                }
                notification.save(update_fields=['delivery_status'])
            print("Announcement scheduled for future delivery.", file=sys.stderr)
        else:
            # Send via notification service for immediate delivery with multi-channel support
            # Channels can be provided as list in request.data['channels']
            channels = request.data.get('channels', ['app', 'whatsapp'])
            override = bool(request.data.get('override_preferences', False))
            # If override, include more channels by default
            if override and 'sms' not in channels:
                channels = list(set(channels + ['email', 'push', 'sms']))
            for notification in notifications:
               notification_service.send_custom_notification(
                recipient=notification.recipient,
                notification=notification,            # <── Use existing object
                channels=channels,
                override_preferences=override
                )
            print("Announcement sent via notification service..", file=sys.stderr)
        
        return Response({
            'success': True,
            'message': (
                f"Announcement sent to {len(notifications)} targeted recipients"
                if targeted else
                f"Announcement sent to {len(notifications)} staff members"
            ),
            'notification_count': len(notifications),
            'title': serializer.validated_data['title'],
            'targeted': targeted
        }, status=status.HTTP_201_CREATED)
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def acknowledge_announcement(request, notification_id):
    """Explicit acknowledgement endpoint; marks as read and returns status"""
    try:
        notification = get_object_or_404(
            Notification,
            id=notification_id,
            recipient=request.user,
            notification_type='ANNOUNCEMENT'
        )
        if not notification.read_at:
            notification.mark_as_read()
        return Response({
            'success': True,
            'acknowledged_at': notification.read_at,
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def report_delivery_issue(request):
    """Staff can report undelivered announcements or issues"""
    try:
        description = request.data.get('description')
        notification_id = request.data.get('notification_id')
        if not description:
            return Response({'success': False, 'error': 'description is required'}, status=status.HTTP_400_BAD_REQUEST)
        notification = None
        if notification_id:
            try:
                notification = Notification.objects.get(id=notification_id, recipient=request.user)
            except Notification.DoesNotExist:
                notification = None
        issue = NotificationIssue.objects.create(
            reporter=request.user,
            notification=notification,
            description=description
        )
        return Response({'success': True, 'issue_id': issue.id})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def health_check_notifications(request):
    """Run basic configuration health checks for notification delivery"""
    try:
        checks = {}
        # Email
        from django.conf import settings as dj_settings
        checks['email_configured'] = bool(getattr(dj_settings, 'EMAIL_BACKEND', '')) and bool(getattr(dj_settings, 'DEFAULT_FROM_EMAIL', ''))
        # Firebase
        import firebase_admin
        checks['firebase_initialized'] = bool(firebase_admin._apps)
        # WhatsApp
        checks['whatsapp_configured'] = bool(getattr(dj_settings, 'WHATSAPP_ACCESS_TOKEN', None)) and bool(getattr(dj_settings, 'WHATSAPP_PHONE_NUMBER_ID', None))
        checks['whatsapp_webhook_configured'] = bool(getattr(dj_settings, 'WHATSAPP_WEBHOOK_VERIFY_TOKEN', None))
        # SMS/Twilio
        checks['twilio_configured'] = bool(getattr(dj_settings, 'TWILIO_ACCOUNT_SID', None)) and bool(getattr(dj_settings, 'TWILIO_AUTH_TOKEN', None)) and bool(getattr(dj_settings, 'TWILIO_FROM_NUMBER', None))
        # Device tokens count
        checks['device_tokens_count'] = DeviceToken.objects.count()
        # Staff preferences sanity: count users with announcement disabled
        checks['announcement_disabled_count'] = NotificationPreference.objects.filter(announcement_notifications=False).count()
        return Response({'success': True, 'checks': checks})
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['GET'])
@permission_classes([IsAdminOrManager])
def whatsapp_activity(request):
    try:
        from django.utils import timezone as _tz
        since_param = request.query_params.get('since')
        since = _tz.now() - _tz.timedelta(days=7)
        try:
            if since_param:
                since = _tz.datetime.fromisoformat(since_param)
        except Exception:
            pass
        sessions = WhatsAppSession.objects.filter(last_interaction_at__gte=since).order_by('-last_interaction_at')[:100]
        incidents = NotificationIssue.objects.filter(created_at__gte=since).order_by('-created_at')[:100]
        from timeclock.models import ClockEvent
        clock_events = ClockEvent.objects.filter(timestamp__gte=since, device_id__iexact='whatsapp').order_by('-timestamp')[:100]
        def safe_user(u):
            if not u:
                return None
            return {'id': str(u.id), 'name': u.get_full_name(), 'phone': u.phone}
        return Response({
            'sessions': [{'phone': s.phone, 'user': safe_user(s.user), 'state': s.state, 'last_interaction_at': s.last_interaction_at} for s in sessions],
            'incidents': [{'id': i.id, 'reporter': safe_user(i.reporter), 'description': i.description, 'created_at': i.created_at} for i in incidents],
            'clock_events': [{'id': ce.id, 'staff': safe_user(ce.staff), 'event_type': ce.event_type, 'timestamp': ce.timestamp} for ce in clock_events],
        })
    except Exception as e:
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)

@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def mark_all_notifications_read(request):
    """Mark all unread notifications as read for the user"""
    try:
        unread_notifications = Notification.objects.filter(
            recipient=request.user,
            read_at__isnull=True
        )
        
        count = unread_notifications.count()
        unread_notifications.update(read_at=timezone.now())
        
        return Response({
            'success': True,
            'message': f'{count} notifications marked as read',
            'count': count
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def notification_stats(request):
    """Get notification statistics for the user"""
    try:
        user = request.user
        
        total_count = Notification.objects.filter(recipient=user).count()
        unread_count = Notification.objects.filter(
            recipient=user, 
            read_at__isnull=True
        ).count()
        
        # Count by type
        type_counts = {}
        for notification_type, _ in Notification.NOTIFICATION_TYPES:
            count = Notification.objects.filter(
                recipient=user,
                notification_type=notification_type
            ).count()
            if count > 0:
                type_counts[notification_type] = count
        
        # Count by priority
        priority_counts = {}
        for priority, _ in Notification.PRIORITY_LEVELS:
            count = Notification.objects.filter(
                recipient=user,
                priority=priority
            ).count()
            if count > 0:
                priority_counts[priority] = count
        
        return Response({
            'total_count': total_count,
            'unread_count': unread_count,
            'type_counts': type_counts,
            'priority_counts': priority_counts
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


class NotificationPreferenceView(generics.RetrieveUpdateAPIView):
    """Get and update notification preferences for the authenticated user"""
    serializer_class = NotificationPreferenceSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_object(self):
        preference, created = NotificationPreference.objects.get_or_create(
            user=self.request.user
        )
        return preference


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def register_device_token(request):
    """Register or update a device token for push notifications"""
    try:
        token = request.data.get('token')
        device_type = request.data.get('device_type', 'UNKNOWN')
        device_name = request.data.get('device_name', '')
        
        if not token:
            return Response({
                'success': False,
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        # Deactivate existing tokens for this user and device type
        DeviceToken.objects.filter(
            user=request.user,
            device_type=device_type
        ).update(is_active=False)
        
        # Create or update the token
        device_token, created = DeviceToken.objects.update_or_create(
            user=request.user,
            token=token,
            defaults={
                'device_type': device_type,
                'device_name': device_name,
                'is_active': True,
                'last_used': timezone.now()
            }
        )
        
        return Response({
            'success': True,
            'message': 'Device token registered successfully',
            'token_id': device_token.id,
            'created': created
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET', 'POST'])
@permission_classes([permissions.AllowAny])
def whatsapp_webhook(request):
    try:
        if request.method == 'GET':
            token = request.query_params.get('hub.verify_token') or request.GET.get('hub.verify_token')
            challenge = request.query_params.get('hub.challenge') or request.GET.get('hub.challenge')
            if token and token == getattr(dj_settings, 'WHATSAPP_VERIFY_TOKEN', ''):
                return Response(int(challenge))
            return Response(status=status.HTTP_403_FORBIDDEN)
        
        payload = request.data
        entries = payload.get('entry', [])
        
        def lang_for(user):
            try:
                return (user.restaurant.language or 'en').split('-')[0]
            except Exception:
                return 'en'
                
        RESP = {
            'en': {
                'help': 'Welcome to Mizan. Reply with: "clock in", "clock out", "tasks", or "report".',
                'clockin_prompt': 'Please share your live location to clock in.',
                'clockin_ok': 'Clock-in successful at {time}.',
                'clockin_failed': 'Clock-in failed. You are {distance}m away from the location.',
                'clockout_ok': 'Clock-out recorded. Duration: {duration} hours.',
                'clockout_no': 'You are not currently clocked in.',
                'link_phone': 'Please link your phone number in your profile to use this feature.',
                'tasks_none': 'No active tasks assigned to you.',
                'tasks_list_suffix': 'Reply "complete <number>" to mark a task as done.',
                'task_completed': 'Task marked as completed.',
                'task_verify_photo': 'Please send a photo as evidence to complete this task.',
                'task_verify_done': 'Task completed with photo evidence.',
                'incident_prompt': 'Please describe the incident or issue. You can send a text or voice note.',
                'incident_recorded': 'Incident recorded. Ticket #{ticket_id}. A manager will be notified.',
                'incident_failed': 'Failed to record incident. Please try again.',
                'unrecognized': 'Unrecognized command. Reply with "help" to see available options.',
            },
            'ar': {
                'help': 'مرحبًا بكم في ميزان. أجب بـ: "دخول"، "خروج"، "مهام"، أو "بلاغ".',
                'clockin_prompt': 'يرجى مشاركة موقعك المباشر لتسجيل الدخول.',
                'clockin_ok': 'تم تسجيل الدخول بنجاح في {time}.',
                'clockin_failed': 'فشل تسجيل الدخول. أنت على بعد {distance} متر من الموقع.',
                'clockout_ok': 'تم تسجيل الخروج. المدة: {duration} ساعة.',
                'clockout_no': 'لم يتم تسجيل دخولك حاليًا.',
                'link_phone': 'يرجى ربط رقم هاتفك في ملفك الشخصي لاستخدام هذه الميزة.',
                'tasks_none': 'لا توجد مهام نشطة معينة لك.',
                'tasks_list_suffix': 'أجب بـ "إتمام <رقم>" لتمييز المهمة كمكتملة.',
                'task_completed': 'تم تمييز المهمة كمكتملة.',
                'task_verify_photo': 'يرجى إرسال صورة كدليل لإكمال هذه المهمة.',
                'task_verify_done': 'اكتملت المهمة مع دليل الصور.',
                'incident_prompt': 'يرجى وصف الحادث أو المشكلة. يمكنك إرسال نص أو ملاحظة صوتية.',
                'incident_recorded': 'تم تسجيل الحادث. التذكرة رقم {ticket_id}. سيتم إخطار المدير.',
                'unrecognized': 'أمر غير معروف. أجب بـ "مساعدة" لرؤية الخيارات المتاحة.',
            },
            'fr': {
                'help': 'Bienvenue chez Mizan. Répondez par : "clock in", "clock out", "tâches", ou "rapport".',
                'clockin_prompt': 'Veuillez partager votre position en direct pour pointer.',
                'clockin_ok': 'Pointage d\'entrée réussi à {time}.',
                'clockin_failed': 'Échec du pointage. Vous êtes à {distance}m de l\'emplacement.',
                'clockout_ok': 'Pointage de sortie enregistré. Durée : {duration} heures.',
                'clockout_no': 'Vous n\'êtes pas actuellement pointé.',
                'link_phone': 'Veuillez lier votre numéro de téléphone dans votre profil pour utiliser cette fonctionnalité.',
                'tasks_none': 'Aucune tâche assignée.',
                'tasks_list_suffix': 'Répondez "terminer <nombre>" pour marquer une tâche comme terminée.',
                'task_completed': 'Tâche terminée.',
                'task_verify_photo': 'Veuillez envoyer une photo.',
                'task_verify_done': 'Tâche terminée avec photo.',
                'incident_prompt': 'Décrivez l\'incident (texte ou voix).',
                'incident_recorded': 'Incident enregistré. Ticket #{ticket_id}.',
                'unrecognized': 'Commande non reconnue. Répondez "aide".',
            },
        }
        
        def R(user, key, **kwargs):
            lang = lang_for(user)
            # fallback to English if key missing in lang
            tmpl = RESP.get(lang, RESP['en']).get(key, RESP['en'].get(key, ''))
            return tmpl.format(**kwargs)

        for entry in entries:
            changes = entry.get('changes', [])
            for change in changes:
                value = change.get('value', {})
                # ------------------------------------------------------------------
                # HANDLE STATUS UPDATES (DELIVERY RECEIPTS)
                # ------------------------------------------------------------------
                statuses = value.get('statuses', [])
                for status_obj in statuses:
                    wamid = status_obj.get('id')
                    status_str = status_obj.get('status')
                    
                    status_map = {
                        'sent': 'SENT',
                        'delivered': 'DELIVERED',
                        'read': 'READ',
                        'failed': 'FAILED'
                    }
                    mapped_status = status_map.get(status_str)
                    if mapped_status:
                        from .models import NotificationLog
                        log = NotificationLog.objects.filter(external_id=wamid).first()
                        if log:
                            log.status = mapped_status
                            if mapped_status == 'DELIVERED' or mapped_status == 'READ':
                                if not log.delivered_at:
                                    log.delivered_at = timezone.now()
                            log.save(update_fields=['status', 'delivered_at'])
                            
                            # Also update the parent notification if needed
                            notif = log.notification
                            if notif:
                                if mapped_status == 'READ' and not notif.read_at:
                                    notif.read_at = timezone.now()
                                    notif.is_read = True
                                    notif.save(update_fields=['read_at', 'is_read'])

                messages = value.get('messages', [])
                
                for msg in messages:
                    from_phone = msg.get('from')
                    msg_type = msg.get('type')
                    text_body = (msg.get('text') or {}).get('body') if msg_type == 'text' else None
                    
                    # Normalize phone
                    phone_digits = ''.join(filter(str.isdigit, str(from_phone or '')))
                    # Match last 9 digits to be safe (or 10)
                    user = CustomUser.objects.filter(phone__isnull=False).filter(phone__regex=r'\d').filter(phone__icontains=phone_digits[-9:]).first()
                    
                    session, _ = WhatsAppSession.objects.get_or_create(phone=phone_digits, defaults={'user': user})
                    if user and session.user is None:
                        session.user = user
                        session.save(update_fields=['user'])
                    
                    from accounts.utils import calculate_distance
                    
                    # ------------------------------------------------------------------
                    # 1. HANDLE INTERACTIVE (Buttons)
                    # ------------------------------------------------------------------
                    if msg_type == 'interactive':
                        interactive = msg.get('interactive', {})
                        int_type = interactive.get('type')
                        
                        if int_type == 'button_reply':
                            btn_reply = interactive.get('button_reply', {})
                            btn_id = btn_reply.get('id')
                            
                            if btn_id == 'clock_in_now':
                                if user:
                                    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                                    if last_event and last_event.event_type == 'in':
                                        notification_service.send_whatsapp_text(phone_digits, "You are already clocked in.")
                                    else:
                                        session.state = 'awaiting_clock_in_location'
                                        session.save(update_fields=['state'])
                                        notification_service.send_whatsapp_location_request(phone_digits, R(user, 'clockin_prompt'))
                                else:
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue

                            elif btn_id == 'clock_out_now':
                                if user:
                                    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                                    if last_event and last_event.event_type == 'in':
                                        duration = (timezone.now() - last_event.timestamp).total_seconds() / 3600
                                        ClockEvent.objects.create(
                                            staff=user, 
                                            event_type='out', 
                                            device_id='whatsapp',
                                            location_encrypted="PRECISE_GPS" # Placeholder
                                        )
                                        # Send summary and ask for feedback
                                        summary_msg = (
                                            f"✅ *Clock-out successful!*\n\n"
                                            f"⏱️ Duration: *{duration:.2f} hours*\n\n"
                                            "How was your shift today? Please reply with a number from 1 to 5 (5 is great!)."
                                        )
                                        notification_service.send_whatsapp_text(phone_digits, summary_msg)
                                        session.state = 'awaiting_feedback'
                                        session.context['last_session_id'] = str(last_event.id)
                                        session.save(update_fields=['state', 'context'])
                                    else:
                                        notification_service.send_whatsapp_text(phone_digits, R(user, 'clockout_no'))
                                else:
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue

                        elif int_type == 'nfm_reply':
                            nfm_reply = interactive.get('nfm_reply', {})
                            response_json_str = nfm_reply.get('response_json', '{}')
                            try:
                                flow_data = json.loads(response_json_str)
                                if flow_data.get('invite_accepted') == 'yes':
                                    # Delegate to Lua Agent for invitation acceptance
                                    flow_token = nfm_reply.get('flow_token')
                                    if flow_token:
                                        from accounts.models import UserInvitation
                                        invitation = UserInvitation.objects.filter(invitation_token=flow_token).first()
                                        if invitation:
                                            # Update first name if provided in flow
                                            entered_name = flow_data.get('name') or flow_data.get('first_name')
                                            if entered_name:
                                                invitation.first_name = entered_name
                                                invitation.save(update_fields=['first_name'])
                                            
                                            # IMMEDIATE FOLLOW-UP (Requested by user)
                                            notification_service.send_whatsapp_template(
                                                phone_digits,
                                                template_name='accepted_invite_confirmation',
                                                language_code='en_US'
                                            )
                                            
                                            # AUTOMATIC ACCEPTANCE: Create user account immediately
                                            user, error = UserManagementService.accept_invitation(
                                                token=invitation.invitation_token,
                                                first_name=invitation.first_name,
                                                last_name=invitation.last_name or "Staff"
                                            )
                                            
                                            if user:
                                                logger.info(f"User {user.email} created automatically via WhatsApp accept")
                                                # Log in for the user or just sync? 
                                                # For now, we sync with a placeholder/empty token or none
                                                # sync_user_to_lua_agent(user, access_token=None)
                                                
                                                # Delegate to Lua Agent for welcome message
                                                ok, agent_response = notification_service.send_lua_invitation_accepted(
                                                    invitation_token=invitation.invitation_token,
                                                    phone=phone_digits,
                                                    first_name=user.first_name,
                                                    flow_data=flow_data
                                                )
                                            else:
                                                logger.error(f"Failed to automatically create user via WhatsApp: {error}")
                                                ok = False
                                                notification_service.send_whatsapp_text(
                                                    phone_digits,
                                                    f"Sorry, there was an issue creating your account: {error}. Please contact support."
                                                )
                                            
                                            # Update delivery log
                                            from accounts.models import InvitationDeliveryLog
                                            log = InvitationDeliveryLog.objects.filter(invitation=invitation, channel='whatsapp').first()
                                            if log:
                                                log.status = 'ACCEPTED' if ok else 'FAILED'
                                                log.save(update_fields=['status'])
                            except Exception as e:
                                logger.error(f"Flow response error: {e}")
                            continue

                    # ══════════════════════════════════════════════════════════════════
                    # 5. HANDLE TEXT MESSAGES (Accept Invitation Fallback)
                    # ══════════════════════════════════════════════════════════════════
                    if msg_type == 'text' and text_body:
                        normalized_body = text_body.strip().lower()
                        if normalized_body in ['accept invite', 'accept invitation', 'accept']:
                            from accounts.models import UserInvitation
                            from django.db.models import Q
                            
                            print(f"DEBUG: WhatsApp Accept Text from {phone_digits}", file=sys.stderr)
                            
                            # Lookup pending invitation for this phone number
                            # (Search in extra_data where we store invitation phone)
                            # We search for the digits string itself in the JSON dump as a fallback
                            invitation = UserInvitation.objects.filter(
                                is_accepted=False,
                                expires_at__gt=timezone.now()
                            ).filter(
                                Q(extra_data__phone__icontains=phone_digits[-9:]) | 
                                Q(extra_data__phone_number__icontains=phone_digits[-9:]) |
                                Q(extra_data__icontains=phone_digits[-9:])
                            ).first()

                            if invitation:
                                print(f"DEBUG: Found invitation {invitation.id}", file=sys.stderr)
                                
                                # IMMEDIATE FOLLOW-UP
                                notification_service.send_whatsapp_template(
                                    phone_digits,
                                    template_name='accepted_invite_confirmation',
                                    language_code='en_US'
                                )
                                
                                # AUTOMATIC ACCEPTANCE
                                user, error = UserManagementService.accept_invitation(
                                    token=invitation.invitation_token,
                                    first_name=invitation.first_name,
                                    last_name=invitation.last_name or "Staff"
                                )
                                
                                if user:
                                    logger.info(f"User {user.email} created automatically via text command")
                                    # Notify Lua Agent for welcome
                                    notification_service.send_lua_invitation_accepted(
                                        invitation_token=invitation.invitation_token,
                                        phone=phone_digits,
                                        first_name=user.first_name,
                                        flow_data={'method': 'text_command'}
                                    )
                                    continue # Message handled
                                else:
                                    logger.error(f"Failed to auto-create user via text: {error}")
                                    notification_service.send_whatsapp_text(
                                        phone_digits,
                                        f"Sorry, there was an issue creating your account: {error}. Please contact support."
                                    )
                                    continue
                            else:
                                logger.warning(f"No pending invitation found for {phone_digits} matching '{text_body}'")
                                # Let it fall through to unrecognized or agent

                    # ------------------------------------------------------------------
                    # 2. HANDLE IMAGE (Verification)
                    # ------------------------------------------------------------------
                    if msg_type == 'image':
                        if session.context.get('awaiting_verification_for_task_id'):
                            task_id = session.context.get('awaiting_verification_for_task_id')
                            try:
                                from scheduling.models import ShiftTask, TaskVerificationRecord
                                task = ShiftTask.objects.get(id=task_id, assigned_to=user)
                                image_obj = msg.get('image') or {}
                                media_id = image_obj.get('id')
                                mime_type = image_obj.get('mime_type')
                                caption = image_obj.get('caption')
                                
                                record, created = TaskVerificationRecord.objects.get_or_create(
                                    task=task,
                                    submitted_by=user,
                                    defaults={'photo_evidence': []}
                                )
                                photos = list(record.photo_evidence or [])
                                photos.append({'media_id': media_id, 'mime_type': mime_type, 'caption': caption})
                                record.photo_evidence = photos
                                record.save(update_fields=['photo_evidence'])
                                
                                task.status = 'COMPLETED'
                                task.completed_at = timezone.now()
                                task.save(update_fields=['status', 'completed_at'])
                                
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'task_verify_done'))
                            except Exception:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'unrecognized'))
                            
                            session.context.pop('awaiting_verification_for_task_id', None)
                            session.state = 'idle'
                            session.save(update_fields=['context', 'state'])
                        else:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'unrecognized'))
                            continue


                    # ------------------------------------------------------------------
                    # 3. HANDLE AUDIO (Incidents)
                    # ------------------------------------------------------------------
                    if msg_type == 'audio':
                        audio = msg.get('audio') or {}
                        media_id = audio.get('id')
                        media_url = notification_service.fetch_whatsapp_media_url(media_id) if media_id else None
                        audio_bytes = notification_service.download_media_bytes(media_url) if media_url else None
                        transcript = notification_service.transcribe_audio_bytes(audio_bytes) if audio_bytes else None
                        
                        text = transcript or "Audio incident report"
                        
                        if user:
                            # Use new Incident model
                            from reporting.models import Incident
                            incident = Incident.objects.create(
                                restaurant=user.restaurant,
                                reporter=user,
                                title=f"Voice Incident from {user.first_name}",
                                description=text,
                                audio_evidence=[media_url] if media_url else [],
                                category='Safety', # Default or infer
                                priority='MEDIUM'
                            )
                            
                            # Send to Lua Agent for analysis/ticket updates if needed
                            ok, data = notification_service.send_lua_incident(
                                user,
                                text,
                                metadata={
                                    'channel': 'whatsapp',
                                    'phone': phone_digits,
                                    'media_id': media_id,
                                    'incident_id': str(incident.id)
                                }
                            )
                            
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_recorded', ticket_id=str(incident.id)[:8]))
                            
                            # Notify Manager
                            try:
                                manager = CustomUser.objects.filter(restaurant=user.restaurant, role='MANAGER').order_by('id').first()
                                if manager and getattr(manager, 'phone', None):
                                    notif_msg = f"New Incident reported by {user.get_full_name()}.\nTicket #{str(incident.id)[:8]}\nDetails: {text[:100]}..."
                                    notification_service.send_whatsapp_text(manager.phone, notif_msg)
                            except Exception:
                                pass
                                
                        else:
                             notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                        continue

                    # ------------------------------------------------------------------
                    # 4. HANDLE LOCATION (Clock In)
                    # ------------------------------------------------------------------
                    if msg_type == 'location':
                        loc = msg.get('location') or {}
                        lat = loc.get('latitude')
                        lon = loc.get('longitude')
                        
                        if user and lat and lon:
                            # Verify if location is within geofence
                            rest = user.restaurant
                            dist = calculate_distance(lat, lon, float(rest.latitude or 0), float(rest.longitude or 0))
                            radius = float(rest.radius or 100)
                            
                            if dist > radius:
                                fail_msg = (
                                    f"📍 *Location verification failed.*\n\n"
                                    f"You are {dist:.0f}m away from the restaurant. "
                                    f"Please be within {radius:.0f}m to clock in."
                                )
                                notification_service.send_whatsapp_text(phone_digits, fail_msg)
                                continue

                            # Logic: Check if already clocked in?
                            last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                            
                            if session.state == 'awaiting_clock_in_location' or (not last_event or last_event.event_type != 'in'):
                                ClockEvent.objects.create(
                                    staff=user,
                                    event_type='in',
                                    latitude=lat,
                                    longitude=lon,
                                    device_id='whatsapp',
                                    location_encrypted=f"{lat},{lon}" # Populate required field
                                )
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'clockin_ok', time=timezone.now().strftime('%H:%M')))
                                session.state = 'idle'
                                session.save(update_fields=['state'])
                            else:
                                notification_service.send_whatsapp_text(phone_digits, "You are already clocked in.")
                        else:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                        continue

                    # ------------------------------------------------------------------
                    # 5. HANDLE TEXT COMMANDS & STATES
                    # ------------------------------------------------------------------
                    body = (text_body or '').strip().lower() if text_body else ''
                    
                    if not body:
                        continue

                    # Handle Awaiting Feedback state
                    if session.state == 'awaiting_feedback' and body.isdigit():
                        rating = int(body)
                        if 1 <= rating <= 5:
                            from attendance.models import ShiftReview
                            last_session_id = session.context.get('last_session_id')
                            if last_session_id:
                                try:
                                    ShiftReview.objects.create(
                                        session_id=last_session_id,
                                        staff=user,
                                        restaurant=user.restaurant,
                                        rating=rating,
                                        comments="Feedback via WhatsApp",
                                        completed_at=timezone.now()
                                    )
                                    notification_service.send_whatsapp_text(phone_digits, "Thank you for your feedback! Have a great rest of your day! ✨")
                                except Exception as e:
                                    logger.error(f"Failed to save shift review: {e}")
                                    notification_service.send_whatsapp_text(phone_digits, "Thank you for your rating!")
                            
                            session.state = 'idle'
                            session.context.pop('last_session_id', None)
                            session.save(update_fields=['state', 'context'])
                            continue

                    if body in ['hi', 'hello', 'menu', 'help']:
                        notification_service.send_whatsapp_text(phone_digits, R(user, 'help'))
                        continue
                        
                    if body in ['clock in', 'clock-in', 'clockin']:
                        if user:
                            # Check if already clocked in
                            last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                            if last_event and last_event.event_type == 'in':
                                notification_service.send_whatsapp_text(phone_digits, "You are already clocked in.")
                            else:
                                session.state = 'awaiting_clock_in_location'
                                session.save(update_fields=['state'])
                                # Use interactive location request
                                notification_service.send_whatsapp_location_request(phone_digits, R(user, 'clockin_prompt'))
                        else:
                             notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                        continue
                        
                    if body in ['clock out', 'clock-out', 'clockout']:
                        if user:
                            last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                            if last_event and last_event.event_type == 'in':
                                # Calculate duration
                                duration = (timezone.now() - last_event.timestamp).total_seconds() / 3600
                                ClockEvent.objects.create(
                                    staff=user, 
                                    event_type='out', 
                                    device_id='whatsapp',
                                    location_encrypted="PRECISE_GPS" # Placeholder
                                )
                                summary_msg = (
                                    f"✅ *Clock-out successful!*\n\n"
                                    f"⏱️ Duration: *{duration:.2f} hours*\n\n"
                                    "How was your shift today? Please reply with a number from 1 to 5 (5 is great!)."
                                )
                                notification_service.send_whatsapp_text(phone_digits, summary_msg)
                                session.state = 'awaiting_feedback'
                                session.context['last_session_id'] = str(last_event.id)
                                session.save(update_fields=['state', 'context'])
                            else:
                                notification_service.send_whatsapp_text(phone_digits, R(user, 'clockout_no'))
                        else:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                        continue

                    if body in ['report', 'incident', 'issue']:
                        session.state = 'awaiting_incident_text'
                        session.save(update_fields=['state'])
                        notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_prompt'))
                        continue
                        
                    if session.state == 'awaiting_incident_text':
                        if user:
                            from reporting.models import Incident
                            incident = Incident.objects.create(
                                restaurant=user.restaurant,
                                reporter=user,
                                title=f"Incident from {user.first_name}",
                                description=body,
                                category='General',
                                priority='MEDIUM'
                            )
                            
                            # Notify Agent
                            notification_service.send_lua_incident(
                                user,
                                body,
                                metadata={'channel': 'whatsapp', 'phone': phone_digits, 'incident_id': str(incident.id)}
                            )
                            
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_recorded', ticket_id=str(incident.id)[:8]))
                            session.state = 'idle'
                            session.save(update_fields=['state'])
                        else:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                        continue

                    # Fallback
                    notification_service.send_whatsapp_text(phone_digits, R(user, 'unrecognized'))

        return Response({'success': True})
    except Exception as e:
        print(f"Webhook Error: {e}", file=sys.stderr)
        return Response({'success': False, 'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def unregister_device_token(request):
    """Unregister a device token"""
    try:
        token = request.data.get('token')
        
        if not token:
            return Response({
                'success': False,
                'error': 'Token is required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        DeviceToken.objects.filter(
            user=request.user,
            token=token
        ).update(is_active=False)
        
        return Response({
            'success': True,
            'message': 'Device token unregistered successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['GET'])
@permission_classes([permissions.IsAuthenticated])
def user_device_tokens(request):
    """List active device tokens for the user"""
    try:
        tokens = DeviceToken.objects.filter(
            user=request.user,
            is_active=True
        ).order_by('-last_used')
        
        serializer = DeviceTokenSerializer(tokens, many=True)
        
        return Response({
            'success': True,
            'tokens': serializer.data
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def send_test_notification(request):
    """Send a test notification to the user (for testing purposes)"""
    try:
        message = request.data.get('message', 'This is a test notification')
        channels = request.data.get('channels', ['app'])
        
        notification_service.send_custom_notification(
            recipient=request.user,
            message=message,
            notification_type='SYSTEM_ALERT',
            channels=channels
        )
        
        return Response({
            'success': True,
            'message': 'Test notification sent successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['DELETE'])
@permission_classes([permissions.IsAuthenticated])
def delete_notification(request, notification_id):
    """Delete a specific notification"""
    try:
        notification = get_object_or_404(
            Notification, 
            id=notification_id, 
            recipient=request.user
        )
        
        notification.delete()
        
        return Response({
            'success': True,
            'message': 'Notification deleted successfully'
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


@api_view(['POST'])
@permission_classes([permissions.IsAuthenticated])
def bulk_notification_actions(request):
    """Perform bulk actions on notifications"""
    try:
        action = request.data.get('action')  # 'mark_read', 'delete'
        notification_ids = request.data.get('notification_ids', [])
        
        if not action or not notification_ids:
            return Response({
                'success': False,
                'error': 'Action and notification_ids are required'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        notifications = Notification.objects.filter(
            id__in=notification_ids,
            recipient=request.user
        )
        
        if action == 'mark_read':
            count = notifications.filter(read_at__isnull=True).update(
                read_at=timezone.now()
            )
            message = f'{count} notifications marked as read'
            
        elif action == 'delete':
            count = notifications.count()
            notifications.delete()
            message = f'{count} notifications deleted'
            
        else:
            return Response({
                'success': False,
                'error': 'Invalid action'
            }, status=status.HTTP_400_BAD_REQUEST)
        
        return Response({
            'success': True,
            'message': message,
            'count': count
        })
        
    except Exception as e:
        return Response({
            'success': False,
            'error': str(e)
        }, status=status.HTTP_400_BAD_REQUEST)


class NotificationPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class NotificationListView(generics.ListAPIView):
    """List notifications for the authenticated user"""
    serializer_class = NotificationSerializer
    permission_classes = [permissions.IsAuthenticated]
    pagination_class = NotificationPagination

    def get_queryset(self):
        user = self.request.user
        queryset = Notification.objects.filter(recipient=user).order_by('-created_at')
        
        # Filter by read status
        is_read = self.request.query_params.get('is_read')
        if is_read is not None:
            if is_read.lower() == 'true':
                queryset = queryset.filter(read_at__isnull=False)
            else:
                queryset = queryset.filter(read_at__isnull=True)
        
        # Filter by notification type
        notification_type = self.request.query_params.get('type')
        if notification_type:
            queryset = queryset.filter(notification_type=notification_type)
        
        # Filter by priority
        priority = self.request.query_params.get('priority')
        if priority:
            queryset = queryset.filter(priority=priority)
        
        # Filter by date range
        date_from = self.request.query_params.get('date_from')
        date_to = self.request.query_params.get('date_to')
        if date_from:
            queryset = queryset.filter(created_at__date__gte=date_from)
        if date_to:
            queryset = queryset.filter(created_at__date__lte=date_to)
        
        return queryset
