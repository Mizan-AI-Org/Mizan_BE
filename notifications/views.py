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
import logging
import json

logger = logging.getLogger(__name__)
from .models import Notification, NotificationPreference, DeviceToken, NotificationAttachment, NotificationIssue, WhatsAppMessageProcessed
from .serializers import (
    NotificationSerializer, 
    NotificationPreferenceSerializer,
    DeviceTokenSerializer,
    AnnouncementCreateSerializer
)
from .services import notification_service
from .utils import infer_incident_type, infer_severity, extract_occurred_at
from scheduling.audit import AuditTrailService, AuditActionType, AuditSeverity
from core.utils import build_tenant_context
from .models import WhatsAppSession
from accounts.models import CustomUser
from accounts.services import UserManagementService, try_activate_staff_on_inbound_message
from timeclock.models import ClockEvent
from scheduling.models import ShiftTask, AssignedShift, ShiftChecklistProgress
from django.conf import settings as dj_settings
from core.i18n import whatsapp_language_code


def _sync_checklist_progress_create(shift, staff, phone_digits, task_ids):
    """Create ShiftChecklistProgress when starting a WhatsApp checklist."""
    try:
        ShiftChecklistProgress.objects.update_or_create(
            shift=shift,
            staff=staff,
            defaults={
                'channel': 'whatsapp',
                'phone': phone_digits,
                'task_ids': task_ids,
                'current_task_id': task_ids[0] if task_ids else '',
                'responses': {},
                'status': 'IN_PROGRESS',
            }
        )
    except Exception as e:
        logger.warning("ShiftChecklistProgress create failed: %s", e)


def _sync_checklist_progress_update(shift_id, staff, checklist_dict):
    """Update ShiftChecklistProgress when checklist state changes."""
    if not shift_id or not staff:
        return
    try:
        shift_obj = AssignedShift.objects.filter(id=shift_id).first()
        if not shift_obj:
            return
        prog = ShiftChecklistProgress.objects.filter(
            shift=shift_obj, staff=staff, status='IN_PROGRESS'
        ).first()
        if prog:
            prog.task_ids = checklist_dict.get('tasks', prog.task_ids)
            prog.current_task_id = checklist_dict.get('current_task_id', '')
            prog.responses = checklist_dict.get('responses', {})
            prog.save(update_fields=['task_ids', 'current_task_id', 'responses', 'updated_at'])
    except Exception as e:
        logger.warning("ShiftChecklistProgress update failed: %s", e)


def _sync_checklist_progress_complete(shift_id, staff):
    """Mark ShiftChecklistProgress as completed when checklist is done."""
    if not shift_id or not staff:
        return
    try:
        shift_obj = AssignedShift.objects.filter(id=shift_id).first()
        if not shift_obj:
            return
        ShiftChecklistProgress.objects.filter(
            shift=shift_obj, staff=staff, status='IN_PROGRESS'
        ).update(status='COMPLETED', completed_at=timezone.now())
    except Exception as e:
        logger.warning("ShiftChecklistProgress complete failed: %s", e)


def _sync_checklist_progress_cancel(shift_id, staff):
    """Mark ShiftChecklistProgress as cancelled (shift ended, etc)."""
    if not shift_id or not staff:
        return
    try:
        shift_obj = AssignedShift.objects.filter(id=shift_id).first()
        if not shift_obj:
            return
        ShiftChecklistProgress.objects.filter(
            shift=shift_obj, staff=staff, status='IN_PROGRESS'
        ).update(status='CANCELLED', completed_at=timezone.now())
    except Exception as e:
        logger.warning("ShiftChecklistProgress cancel failed: %s", e)


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
            logger.info("Announcement scheduled for future delivery.")
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
            logger.info("Announcement sent via notification service.")
        
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
                'incident_prompt': 'Please describe the incident. Include: type (Safety/Maintenance/HR/Service/Other), what happened, and when it occurred. You can send text or a voice note.',
                'incident_clarify_audio': 'Thanks — I couldn’t clearly understand that voice note. Please resend it, or reply with: incident type, a brief description, and the time it occurred.',
                'incident_clarify_missing': 'Thanks — before I log this, please clarify: {missing}.',
                'incident_recorded': '✅ Incident report received and logged.\n\nTicket: #{ticket_id}\nType: {incident_type}\nTime: {occurred_at}\n\nYour report has been received and shared with management.',
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
                'incident_clarify_audio': 'شكراً — لم أفهم الملاحظة الصوتية بوضوح. يرجى إعادة إرسالها أو الرد بالنص: نوع الحادث، وصف موجز، ووقت الحدوث.',
                'incident_clarify_missing': 'شكراً — قبل التسجيل، يرجى توضيح: {missing}.',
                'incident_recorded': 'تم تسجيل الحادث. التذكرة رقم {ticket_id}. سيتم إخطار المدير.',
                'incident_failed': 'فشل تسجيل الحادث. يرجى المحاولة مرة أخرى.',
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
                'incident_clarify_audio': 'Merci — je n\'ai pas bien compris le message vocal. Veuillez le renvoyer ou répondre par texte : type d\'incident, description brève, et heure.',
                'incident_clarify_missing': 'Merci — avant d\'enregistrer, veuillez préciser : {missing}.',
                'incident_recorded': 'Incident enregistré. Ticket #{ticket_id}.',
                'incident_failed': 'Échec de l\'enregistrement. Veuillez réessayer.',
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
                    wamid = msg.get('id')
                    if wamid:
                        if WhatsAppMessageProcessed.objects.filter(wamid=wamid).exists():
                            continue  # Idempotency: already processed
                        WhatsAppMessageProcessed.objects.get_or_create(
                            wamid=wamid,
                            defaults={'channel': 'whatsapp', 'processed_at': timezone.now()}
                        )
                    from_phone = msg.get('from')
                    msg_type = msg.get('type')
                    text_body = (msg.get('text') or {}).get('body') if msg_type == 'text' else None
                    
                    # Normalize phone
                    phone_digits = ''.join(filter(str.isdigit, str(from_phone or '')))
                    # ONE-TAP activation: on first inbound message, match NOT_ACTIVATED staff by phone and activate
                    activated_user = try_activate_staff_on_inbound_message(phone_digits)
                    if activated_user:
                        session, _ = WhatsAppSession.objects.update_or_create(
                            phone=phone_digits,
                            defaults={'user': activated_user, 'state': 'idle'}
                        )
                        # Handoff done; Miya sends welcome. Skip further processing for this message.
                        continue
                    # Resolve user: prefer session's user (restaurant-scoped); else match by phone
                    session = WhatsAppSession.objects.filter(phone=phone_digits).first()
                    user = session.user if (session and session.user_id) else None
                    if not user:
                        qs = CustomUser.objects.filter(phone__isnull=False).filter(phone__regex=r'\d')
                        if session and session.user_id and getattr(session.user, 'restaurant_id', None):
                            qs = qs.filter(restaurant_id=session.user.restaurant_id)
                        user = qs.filter(phone__icontains=phone_digits[-9:]).first()
                    if not session:
                        session = WhatsAppSession.objects.create(phone=phone_digits, user=user)
                    elif user and not session.user_id:
                        session.user = user
                        session.save(update_fields=['user'])
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
                                        # Use clock_in_location_request template with "Send Location" button
                                        notification_service.send_whatsapp_template(
                                            phone=phone_digits,
                                            template_name='clock_in_location_request',
                                            language_code='en_US',
                                            components=[]
                                        )
                                else:
                                    notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                                continue


                            elif btn_id == 'clock_out_now':
                                if user:
                                    last_event = ClockEvent.objects.filter(staff=user).order_by('-timestamp').first()
                                    if last_event and last_event.event_type == 'in':
                                        duration = (timezone.now() - last_event.timestamp).total_seconds() / 3600
                                        restaurant = user.restaurant
                                        notes = "WhatsApp clock-out without location - unverified"
                                        lat, lon, within_geofence = None, None, False
                                        if restaurant and restaurant.latitude and restaurant.longitude and restaurant.radius:
                                            loc_msg = msg.get('location') or (msg.get('interactive', {}).get('location') if msg.get('type') == 'interactive' else None)
                                            if loc_msg:
                                                lat = loc_msg.get('latitude')
                                                lon = loc_msg.get('longitude')
                                            if lat is not None and lon is not None:
                                                from accounts.utils import calculate_distance
                                                dist = calculate_distance(
                                                    float(restaurant.latitude), float(restaurant.longitude),
                                                    float(lat), float(lon)
                                                )
                                                radius = float(restaurant.radius or 100)
                                                within_geofence = dist <= radius
                                                notes = f"WhatsApp clock-out | distance={dist:.0f}m, geofence={'OK' if within_geofence else 'OUTSIDE'}"
                                        ClockEvent.objects.create(
                                            staff=user,
                                            event_type='out',
                                            device_id='whatsapp',
                                            latitude=lat,
                                            longitude=lon,
                                            notes=notes,
                                            location_encrypted='PRECISE_GPS' if within_geofence else 'UNVERIFIED',
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

                            # =====================================================
                            # HANDLE CHECKLIST BUTTON RESPONSES (Yes/No/N/A)
                            # =====================================================
                            elif btn_id in ['yes', 'no', 'n_a', 'Yes', 'No', 'N/A'] and session.state == 'in_checklist':
                                from scheduling.models import ShiftTask
                                checklist = session.context.get('checklist', {})
                                tasks = checklist.get('tasks', [])
                                responses = checklist.get('responses', {})
                                
                                current_task_id = checklist.get('current_task_id')
                                if not current_task_id:
                                    current_index = int(checklist.get('current_index', 0) or 0)
                                    if 0 <= current_index < len(tasks):
                                        current_task_id = tasks[current_index]
                                
                                # Fallback if still no current_task_id
                                if not current_task_id and tasks:
                                    current_task_id = tasks[0]
                                    
                                if current_task_id:
                                    # Record this response
                                    response_value = btn_id.lower().replace('/', '_')  # 'N/A' -> 'n_a'
                                    responses[current_task_id] = response_value
                                    checklist['responses'] = responses
                                    session.context['checklist'] = checklist
                                    _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)
                                    
                                    # Update ShiftTask status based on response
                                    try:
                                        task = ShiftTask.objects.get(id=current_task_id)
                                        # Stop checklist if shift ended
                                        try:
                                            from scheduling.models import AssignedShift
                                            sft = AssignedShift.objects.filter(id=checklist.get('shift_id')).first()
                                            if sft and sft.end_time and timezone.now() > sft.end_time:
                                                notification_service.send_whatsapp_text(phone_digits, "⏱️ Shift ended. Checklist paused.")
                                                _sync_checklist_progress_cancel(checklist.get('shift_id'), user)
                                                session.context.pop('checklist', None)
                                                session.state = 'idle'
                                                session.save(update_fields=['state', 'context'])
                                                continue
                                        except Exception:
                                            pass

                                        if response_value == 'yes':
                                            # Photo verification required: request photo instead of completing
                                            if getattr(task, 'verification_required', False) and str(getattr(task, 'verification_type', 'NONE')).upper() == 'PHOTO':
                                                session.context['awaiting_verification_for_task_id'] = str(task.id)
                                                session.state = 'awaiting_task_photo'
                                                checklist['current_task_id'] = current_task_id
                                                checklist['responses'] = responses
                                                session.context['checklist'] = checklist
                                                session.save(update_fields=['state', 'context'])
                                                msg = (
                                                    f"📸 Please send a photo to complete:\n\n"
                                                    f"*{task.title}*\n{task.description or ''}"
                                                )
                                                notification_service.send_whatsapp_text(phone_digits, msg)
                                                continue
                                            task.status = 'COMPLETED'
                                            task.completed_at = timezone.now()
                                            task.save(update_fields=['status', 'completed_at'])
                                        elif response_value == 'n_a':
                                            task.status = 'CANCELLED'
                                            task.notes = (task.notes or '') + f"\nN/A ({timezone.now().strftime('%H:%M')})"
                                            task.save(update_fields=['status', 'notes'])
                                        elif response_value == 'no':
                                            task.status = 'IN_PROGRESS'
                                            task.started_at = task.started_at or timezone.now()
                                            task.notes = (task.notes or '') + f"\nNot complete ({timezone.now().strftime('%H:%M')})"
                                            task.save(update_fields=['status', 'started_at', 'notes'])
                                            checklist['pending_task_id'] = current_task_id
                                            checklist['responses'] = responses
                                            session.context['checklist'] = checklist
                                            session.state = 'checklist_followup'
                                            session.save(update_fields=['state', 'context'])
                                            follow_msg = (
                                                f"Got it — *{task.title}* isn’t complete yet.\n\n"
                                                "What would you like to do?"
                                            )
                                            follow_buttons = [
                                                {"id": "need_help", "title": "❓ Need help"},
                                                {"id": "delay", "title": "⏳ Delay"},
                                                {"id": "skip", "title": "⏭️ Skip"}
                                            ]
                                            notification_service.send_whatsapp_buttons(phone_digits, follow_msg, follow_buttons)
                                            continue
                                    except ShiftTask.DoesNotExist:
                                        pass

                                    # Advance to next pending task
                                    session.save(update_fields=['context'])

                                    pending_tasks = list(ShiftTask.objects.filter(id__in=tasks).exclude(status__in=['COMPLETED', 'CANCELLED']))
                                    if not pending_tasks:
                                        completed = sum(1 for r in responses.values() if r == 'yes')
                                        total = len(tasks)
                                        completion_msg = (
                                            f"🎉 *Checklist Complete!*\n\n"
                                            f"✅ {completed}/{total} items confirmed\n\n"
                                            "Great job! Have a productive shift."
                                        )
                                        notification_service.send_whatsapp_text(phone_digits, completion_msg)
                                        _sync_checklist_progress_complete(checklist.get('shift_id'), user)
                                        session.context.pop('checklist', None)
                                        session.state = 'idle'
                                        session.save(update_fields=['state', 'context'])
                                        continue

                                    pending_ids = {str(t.id) for t in pending_tasks}
                                    next_task_id = None
                                    for tid in tasks:
                                        if str(tid) in pending_ids:
                                            next_task_id = str(tid)
                                            break
                                    next_task_id = next_task_id or str(pending_tasks[0].id)
                                    checklist['current_task_id'] = next_task_id
                                    session.context['checklist'] = checklist
                                    _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)
                                    session.save(update_fields=['context'])

                                    next_task = ShiftTask.objects.filter(id=next_task_id).first()
                                    if next_task:
                                        idx = (tasks.index(next_task_id) + 1) if next_task_id in tasks else 1
                                        if getattr(next_task, 'verification_required', False) and str(getattr(next_task, 'verification_type', 'NONE')).upper() == 'PHOTO':
                                            msg = (
                                                f"📋 *Task {idx}/{len(tasks)}*\n\n"
                                                f"*{next_task.title}*\n"
                                                f"{next_task.description or ''}\n\n"
                                                f"📸 Please complete this task and send a photo as evidence."
                                            )
                                            session.context['awaiting_verification_for_task_id'] = str(next_task.id)
                                            session.state = 'awaiting_task_photo'
                                            session.save(update_fields=['state', 'context'])
                                            notification_service.send_whatsapp_text(phone_digits, msg)
                                        else:
                                            task_msg = (
                                                f"📋 *Task {idx}/{len(tasks)}*\n\n"
                                                f"*{next_task.title}*\n"
                                                f"{next_task.description or ''}\n\n"
                                                "Is this complete?"
                                            )
                                            buttons = [
                                                {"id": "yes", "title": "✅ Yes"},
                                                {"id": "no", "title": "❌ No"},
                                                {"id": "n_a", "title": "➖ N/A"}
                                            ]
                                            notification_service.send_whatsapp_buttons(phone_digits, task_msg, buttons)
                                continue

                            elif session.state == 'checklist_followup' and btn_id in ['need_help', 'delay', 'skip']:
                                from scheduling.models import ShiftTask
                                checklist = session.context.get('checklist', {})
                                pending_task_id = checklist.get('pending_task_id')
                                task = ShiftTask.objects.filter(id=pending_task_id).first() if pending_task_id else None
                                if not task:
                                    session.state = 'in_checklist'
                                    session.save(update_fields=['state'])
                                    continue
                                if btn_id == 'need_help':
                                    session.state = 'checklist_help_text'
                                    session.save(update_fields=['state'])
                                    notification_service.send_whatsapp_text(phone_digits, f"Tell me what you need help with for:\n\n*{task.title}*")
                                    continue
                                if btn_id == 'delay':
                                    session.state = 'checklist_delay_eta'
                                    session.save(update_fields=['state'])
                                    eta_msg = "When do you expect to complete it?"
                                    eta_buttons = [
                                        {"id": "eta_10m", "title": "10 min"},
                                        {"id": "eta_30m", "title": "30 min"},
                                        {"id": "eta_1h", "title": "1 hour"},
                                        {"id": "eta_later", "title": "Later"}
                                    ]
                                    notification_service.send_whatsapp_buttons(phone_digits, eta_msg, eta_buttons)
                                    continue
                                if btn_id == 'skip':
                                    task.status = 'CANCELLED'
                                    task.notes = (task.notes or '') + f"\nSkipped by staff ({timezone.now().strftime('%H:%M')})"
                                    task.save(update_fields=['status', 'notes'])
                                    checklist.pop('pending_task_id', None)
                                    session.context['checklist'] = checklist
                                    session.state = 'in_checklist'
                                    session.save(update_fields=['state', 'context'])
                                    notification_service.send_whatsapp_text(phone_digits, "Okay — skipping that item. Moving on.")
                                    continue

                            elif session.state == 'checklist_delay_eta' and btn_id in ['eta_10m', 'eta_30m', 'eta_1h', 'eta_later']:
                                from scheduling.models import ShiftTask
                                checklist = session.context.get('checklist', {})
                                pending_task_id = checklist.get('pending_task_id')
                                task = ShiftTask.objects.filter(id=pending_task_id).first() if pending_task_id else None
                                if task:
                                    mapping = {'eta_10m': '10 minutes', 'eta_30m': '30 minutes', 'eta_1h': '1 hour', 'eta_later': 'later'}
                                    eta_txt = mapping.get(btn_id, 'later')
                                    task.notes = (task.notes or '') + f"\nDelayed (ETA: {eta_txt}) at {timezone.now().strftime('%H:%M')}"
                                    task.save(update_fields=['notes'])
                                checklist.pop('pending_task_id', None)
                                session.context['checklist'] = checklist
                                session.state = 'in_checklist'
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(phone_digits, "Thanks — marked as delayed. Continuing.")
                                continue


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
                            # If we're in a shift checklist, resume it automatically
                            checklist = session.context.get('checklist')
                            if checklist:
                                session.state = 'in_checklist'
                                session.save(update_fields=['context', 'state'])
                                try:
                                    from scheduling.models import ShiftTask
                                    task_ids = checklist.get('tasks', [])
                                    responses = checklist.get('responses', {})
                                    pending = list(ShiftTask.objects.filter(id__in=task_ids).exclude(status__in=['COMPLETED', 'CANCELLED']))
                                    if pending:
                                        # pick next in original order
                                        next_id = None
                                        pending_ids = {str(t.id) for t in pending}
                                        for tid in task_ids:
                                            if str(tid) in pending_ids:
                                                next_id = str(tid)
                                                break
                                        next_id = next_id or str(pending[0].id)
                                        checklist['current_task_id'] = next_id
                                        checklist['responses'] = responses
                                        session.context['checklist'] = checklist
                                        session.save(update_fields=['context'])
                                        nxt = ShiftTask.objects.filter(id=next_id).first()
                                        if nxt:
                                            idx = (task_ids.index(next_id) + 1) if next_id in task_ids else 1
                                            if getattr(nxt, 'verification_required', False) and str(getattr(nxt, 'verification_type', 'NONE')).upper() == 'PHOTO':
                                                msg = (
                                                    f"📋 *Task {idx}/{len(task_ids)}*\n\n"
                                                    f"*{nxt.title}*\n"
                                                    f"{nxt.description or ''}\n\n"
                                                    f"📸 Please complete this task and send a photo as evidence."
                                                )
                                                session.context['awaiting_verification_for_task_id'] = str(nxt.id)
                                                session.state = 'awaiting_task_photo'
                                                session.save(update_fields=['state', 'context'])
                                                notification_service.send_whatsapp_text(phone_digits, msg)
                                            else:
                                                task_msg = (
                                                    f"📋 *Task {idx}/{len(task_ids)}*\n\n"
                                                    f"*{nxt.title}*\n"
                                                    f"{nxt.description or ''}\n\n"
                                                    "Is this complete?"
                                                )
                                                buttons = [
                                                    {"id": "yes", "title": "✅ Yes"},
                                                    {"id": "no", "title": "❌ No"},
                                                    {"id": "n_a", "title": "➖ N/A"}
                                                ]
                                                notification_service.send_whatsapp_buttons(phone_digits, task_msg, buttons)
                                except Exception:
                                    pass
                            else:
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
                        media_url, mime_type = notification_service.fetch_whatsapp_media_url(media_id) if media_id else (None, None)
                        audio_bytes = notification_service.download_media_bytes(media_url) if media_url else None
                        transcript = notification_service.transcribe_audio_bytes(audio_bytes, input_mime_type=mime_type) if audio_bytes else None

                        if not user:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                            continue

                        # If transcription failed / unclear, ask for clarification BEFORE creating a ticket
                        if not transcript or len((transcript or '').strip()) < 8:
                            session.state = 'awaiting_incident_clarification'
                            session.context['pending_incident'] = {
                                'source': 'voice',
                                'audio_url': media_url,
                                'media_id': media_id,
                                'transcript': transcript,
                            }
                            session.save(update_fields=['state', 'context'])
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_clarify_audio'))
                            continue

                        # Extract structured incident details (no ticket if critical details are missing)
                        from staff.models_task import SafetyConcernReport
                        from scheduling.models import AssignedShift

                        def _infer_shift(u, when_dt):
                            try:
                                qs = AssignedShift.objects.filter(
                                    staff=u,
                                    shift_date=when_dt.date(),
                                    status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                )
                                # Prefer shifts that overlap the occurred time, else first shift that day.
                                overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                return overlap or qs.order_by('start_time').first()
                            except Exception:
                                return None

                        now = timezone.now()
                        incident_type = infer_incident_type(transcript)
                        occurred_at = extract_occurred_at(transcript, now)

                        missing = []
                        if not incident_type:
                            missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                        if not occurred_at:
                            missing.append("time of occurrence (e.g., today 3pm)")

                        if missing:
                            # Only require clarification if we couldn't infer an incident type.
                            if not incident_type:
                                session.state = 'awaiting_incident_clarification'
                                session.context['pending_incident'] = {
                                    'source': 'voice',
                                    'audio_url': media_url,
                                    'media_id': media_id,
                                    'transcript': transcript,
                                }
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                )
                                continue
                            # If only time is missing, default to "now" so the incident is still recorded.
                            occurred_at = occurred_at or now

                        shift_obj = _infer_shift(user, occurred_at) if occurred_at else None
                        severity = infer_severity(transcript)

                        ticket = SafetyConcernReport.objects.create(
                            restaurant=user.restaurant,
                            reporter=user,
                            is_anonymous=False,
                            incident_type=incident_type,
                            title=f"{incident_type} incident reported via voice",
                            description=transcript.strip(),
                            severity=severity,
                            status='REPORTED',
                            occurred_at=occurred_at,
                            shift=shift_obj,
                            audio_evidence=[media_url] if media_url else [],
                        )

                        # Send to Lua Agent for analysis/context if needed
                        notification_service.send_lua_incident(
                            user,
                            transcript,
                            metadata={
                                'channel': 'whatsapp',
                                'phone': phone_digits,
                                'media_id': media_id,
                                'ticket_id': str(ticket.id),
                                'incident_type': incident_type,
                            }
                        )

                        occurred_str = occurred_at.strftime('%Y-%m-%d %H:%M') if occurred_at else '—'
                        notification_service.send_whatsapp_text(
                            phone_digits,
                            R(user, 'incident_recorded', ticket_id=str(ticket.id)[:8], incident_type=incident_type, occurred_at=occurred_str)
                        )

                        # Notify Manager (best-effort)
                        try:
                            manager = CustomUser.objects.filter(restaurant=user.restaurant, role__in=['MANAGER', 'ADMIN']).order_by('id').first()
                            if manager and getattr(manager, 'phone', None):
                                notif_msg = (
                                    f"New Incident reported by {user.get_full_name()}.\n"
                                    f"Ticket #{str(ticket.id)[:8]}\n"
                                    f"Type: {incident_type}\n"
                                    f"Time: {occurred_str}\n"
                                    f"Details: {transcript[:150]}..."
                                )
                                notification_service.send_whatsapp_text(manager.phone, notif_msg)
                        except Exception:
                            pass
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
                                
                                # =====================================================
                                # INITIATE CONVERSATIONAL CHECKLIST AFTER CLOCK-IN
                                # =====================================================
                                from scheduling.models import AssignedShift, ShiftTask
                                
                                # Find active shift for this user (started today, user is staff)
                                now_today = timezone.now()
                                active_qs = AssignedShift.objects.filter(
                                    staff=user,
                                    shift_date=now_today.date(),
                                    status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS']
                                )
                                # Prefer shift overlapping now; fallback to earliest today
                                active_shift = active_qs.filter(start_time__lte=now_today, end_time__gte=now_today).first() or active_qs.order_by('start_time').first()
                                
                                if active_shift:
                                    def ensure_shift_tasks_from_templates(shift_obj):
                                        """
                                        If the shift has no ShiftTask rows yet, generate them from assigned task_templates.
                                        This enables a step-by-step conversational checklist immediately after clock-in.
                                        """
                                        try:
                                            if ShiftTask.objects.filter(shift=shift_obj).exists():
                                                return
                                        except Exception:
                                            return
                                        try:
                                            templates = list(shift_obj.task_templates.all())
                                        except Exception:
                                            templates = []
                                        for tpl in templates:
                                            steps = []
                                            try:
                                                if getattr(tpl, 'sop_steps', None):
                                                    steps = list(tpl.sop_steps or [])
                                                elif getattr(tpl, 'tasks', None):
                                                    steps = list(tpl.tasks or [])
                                            except Exception:
                                                steps = []
                                            if not steps:
                                                steps = [{"title": getattr(tpl, 'name', 'Task'), "description": getattr(tpl, 'description', '') or ''}]
                                            for step in steps:
                                                if isinstance(step, str):
                                                    title = step.strip()[:255] or getattr(tpl, 'name', 'Task')
                                                    desc = ''
                                                elif isinstance(step, dict):
                                                    title = (step.get('title') or step.get('name') or step.get('task') or getattr(tpl, 'name', 'Task'))[:255]
                                                    desc = (step.get('description') or step.get('details') or '').strip()
                                                else:
                                                    title = getattr(tpl, 'name', 'Task')
                                                    desc = ''
                                                v_req = bool(step.get('verification_required', False)) or bool(getattr(tpl, 'verification_required', False))
                                                v_type = step.get('verification_type') or getattr(tpl, 'verification_type', 'NONE') or 'NONE'
                                                v_inst = step.get('verification_instructions') or getattr(tpl, 'verification_instructions', None)
                                                v_cl = step.get('verification_checklist') or getattr(tpl, 'verification_checklist', []) or []
                                                
                                                ShiftTask.objects.create(
                                                    shift=shift_obj,
                                                    title=title,
                                                    description=desc,
                                                    status='TODO',
                                                    assigned_to=user,
                                                    verification_required=v_req,
                                                    verification_type=v_type,
                                                    verification_instructions=v_inst,
                                                    verification_checklist=v_cl,
                                                )
                                    
                                    ensure_shift_tasks_from_templates(active_shift)

                                    # Get all pending tasks for this shift (TODO + IN_PROGRESS)
                                    tasks_qs = ShiftTask.objects.filter(shift=active_shift).exclude(status__in=['COMPLETED', 'CANCELLED'])
                                    priority_order = {'URGENT': 0, 'HIGH': 1, 'MEDIUM': 2, 'LOW': 3}
                                    tasks = sorted(list(tasks_qs), key=lambda t: (priority_order.get((t.priority or 'MEDIUM').upper(), 2), t.created_at))
                                    task_ids = [str(t.id) for t in tasks]
                                    
                                    if task_ids:
                                        # Store checklist state in session context
                                        session.context['checklist'] = {
                                            'shift_id': str(active_shift.id),
                                            'tasks': task_ids,
                                            'current_task_id': task_ids[0],
                                            'responses': {},
                                            'started_at': timezone.now().isoformat()
                                        }
                                        _sync_checklist_progress_create(active_shift, user, phone_digits, task_ids)
                                        session.state = 'in_checklist'
                                        session.save(update_fields=['state', 'context'])

                                        # If shift ended already, don't start
                                        if active_shift.end_time and timezone.now() > active_shift.end_time:
                                            notification_service.send_whatsapp_text(phone_digits, "⏱️ This shift has already ended. No checklist to run.")
                                            _sync_checklist_progress_cancel(str(active_shift.id), user)
                                            session.context.pop('checklist', None)
                                            session.state = 'idle'
                                            session.save(update_fields=['state', 'context'])
                                        else:
                                            first_task = tasks[0]
                                            # Photo verification tasks: request photo
                                            if getattr(first_task, 'verification_required', False) and str(getattr(first_task, 'verification_type', 'NONE')).upper() == 'PHOTO':
                                                msg = (
                                                    f"📋 *Task 1/{len(task_ids)}*\n\n"
                                                    f"*{first_task.title}*\n"
                                                    f"{first_task.description or ''}\n\n"
                                                    f"📸 Please complete this task and send a photo as evidence."
                                                )
                                                session.context['awaiting_verification_for_task_id'] = str(first_task.id)
                                                session.state = 'awaiting_task_photo'
                                                session.save(update_fields=['state', 'context'])
                                                notification_service.send_whatsapp_text(phone_digits, msg)
                                            else:
                                                task_msg = (
                                                    f"📋 *Task 1/{len(task_ids)}*\n\n"
                                                    f"*{first_task.title}*\n"
                                                    f"{first_task.description or ''}\n\n"
                                                    "Is this complete?"
                                                )
                                                buttons = [
                                                    {"id": "yes", "title": "✅ Yes"},
                                                    {"id": "no", "title": "❌ No"},
                                                    {"id": "n_a", "title": "➖ N/A"}
                                                ]
                                                notification_service.send_whatsapp_buttons(phone_digits, task_msg, buttons)
                                    else:
                                        # No tasks for this shift
                                        session.state = 'idle'
                                        session.save(update_fields=['state'])
                                else:
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
                    raw_body = (text_body or '').strip() if text_body else ''
                    body = raw_body.lower() if raw_body else ''
                    
                    if not body:
                        continue

                    # Checklist help free-text (after user taps "Need help")
                    if session.state == 'checklist_help_text':
                        try:
                            from scheduling.models import ShiftTask
                            checklist = session.context.get('checklist', {})
                            pending_task_id = checklist.get('pending_task_id')
                            task = ShiftTask.objects.filter(id=pending_task_id).first() if pending_task_id else None
                            if task:
                                task.notes = (task.notes or '') + f"\nHelp requested: {raw_body} ({timezone.now().strftime('%H:%M')})"
                                task.save(update_fields=['notes'])
                            checklist.pop('pending_task_id', None)
                            session.context['checklist'] = checklist
                            session.state = 'in_checklist'
                            session.save(update_fields=['state', 'context'])
                            notification_service.send_whatsapp_text(phone_digits, "Thanks — noted. Continuing with the next task.")

                            # Send next pending task immediately
                            task_ids = checklist.get('tasks', [])
                            pending = list(ShiftTask.objects.filter(id__in=task_ids).exclude(status__in=['COMPLETED', 'CANCELLED']))
                            if not pending:
                                _sync_checklist_progress_complete(checklist.get('shift_id'), user)
                                session.context.pop('checklist', None)
                                session.state = 'idle'
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(phone_digits, "🎉 Checklist complete!")
                            else:
                                pending_ids = {str(t.id) for t in pending}
                                next_id = None
                                for tid in task_ids:
                                    if str(tid) in pending_ids:
                                        next_id = str(tid)
                                        break
                                next_id = next_id or str(pending[0].id)
                                checklist['current_task_id'] = next_id
                                session.context['checklist'] = checklist
                                _sync_checklist_progress_update(checklist.get('shift_id'), user, checklist)
                                session.save(update_fields=['context'])
                                nxt = ShiftTask.objects.filter(id=next_id).first()
                                if nxt:
                                    idx = (task_ids.index(next_id) + 1) if next_id in task_ids else 1
                                    if getattr(nxt, 'verification_required', False) and str(getattr(nxt, 'verification_type', 'NONE')).upper() == 'PHOTO':
                                        msg = (
                                            f"📋 *Task {idx}/{len(task_ids)}*\n\n"
                                            f"*{nxt.title}*\n"
                                            f"{nxt.description or ''}\n\n"
                                            f"📸 Please complete this task and send a photo as evidence."
                                        )
                                        session.context['awaiting_verification_for_task_id'] = str(nxt.id)
                                        session.state = 'awaiting_task_photo'
                                        session.save(update_fields=['state', 'context'])
                                        notification_service.send_whatsapp_text(phone_digits, msg)
                                    else:
                                        task_msg = (
                                            f"📋 *Task {idx}/{len(task_ids)}*\n\n"
                                            f"*{nxt.title}*\n"
                                            f"{nxt.description or ''}\n\n"
                                            "Is this complete?"
                                        )
                                        buttons = [
                                            {"id": "yes", "title": "✅ Yes"},
                                            {"id": "no", "title": "❌ No"},
                                            {"id": "n_a", "title": "➖ N/A"}
                                        ]
                                        notification_service.send_whatsapp_buttons(phone_digits, task_msg, buttons)
                        except Exception:
                            # Fall back without breaking the chat
                            session.state = 'in_checklist'
                            session.save(update_fields=['state'])
                        continue

                    # Handle clarification flow for incidents (voice or incomplete report)
                    if session.state == 'awaiting_incident_clarification':
                        if not user:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                            continue

                        pending = session.context.get('pending_incident') or {}
                        base_text = (pending.get('transcript') or '').strip()
                        combined_text = (base_text + ("\n\nClarification: " + raw_body if raw_body else "")).strip()

                        from staff.models_task import SafetyConcernReport
                        from scheduling.models import AssignedShift

                        def _infer_shift(u, when_dt):
                            try:
                                qs = AssignedShift.objects.filter(
                                    staff=u,
                                    shift_date=when_dt.date(),
                                    status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                )
                                overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                return overlap or qs.order_by('start_time').first()
                            except Exception:
                                return None

                        now = timezone.now()
                        incident_type = infer_incident_type(combined_text)
                        occurred_at = extract_occurred_at(combined_text, now)

                        missing = []
                        if not incident_type:
                            missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                        if not occurred_at:
                            missing.append("time of occurrence (e.g., today 3pm)")

                        if missing:
                            # If we still don't know what kind of incident this is, keep clarifying.
                            if not incident_type:
                                session.context['pending_incident'] = {**pending, 'transcript': combined_text}
                                session.save(update_fields=['context'])
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                )
                                continue
                            # Otherwise, default missing time to "now" so we still log the ticket.
                            occurred_at = occurred_at or now

                        shift_obj = _infer_shift(user, occurred_at) if occurred_at else None
                        severity = infer_severity(combined_text)

                        ticket = SafetyConcernReport.objects.create(
                            restaurant=user.restaurant,
                            reporter=user,
                            is_anonymous=False,
                            incident_type=incident_type,
                            title=f"{incident_type} incident reported",
                            description=combined_text.strip(),
                            severity=severity,
                            status='REPORTED',
                            occurred_at=occurred_at,
                            shift=shift_obj,
                            audio_evidence=[pending.get('audio_url')] if pending.get('audio_url') else [],
                        )

                        notification_service.send_lua_incident(
                            user,
                            combined_text,
                            metadata={
                                'channel': 'whatsapp',
                                'phone': phone_digits,
                                'ticket_id': str(ticket.id),
                                'incident_type': incident_type,
                            }
                        )

                        occurred_str = occurred_at.strftime('%Y-%m-%d %H:%M') if occurred_at else '—'
                        notification_service.send_whatsapp_text(
                            phone_digits,
                            R(user, 'incident_recorded', ticket_id=str(ticket.id)[:8], incident_type=incident_type, occurred_at=occurred_str)
                        )

                        # reset session state
                        session.state = 'idle'
                        session.context.pop('pending_incident', None)
                        session.save(update_fields=['state', 'context'])
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

                    body_clean = (body or '').strip()
                    incident_triggers = {'report', 'incident', 'issue', 'rapport', 'signalement', 'بلاغ'}
                    if body_clean.lower() in incident_triggers or body_clean in incident_triggers:
                        session.state = 'awaiting_incident_text'
                        session.save(update_fields=['state'])
                        notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_prompt'))
                        continue
                        
                    if session.state == 'awaiting_incident_text':
                        if user:
                            # Use the same structured extraction + clarification rules as voice
                            from staff.models_task import SafetyConcernReport
                            from scheduling.models import AssignedShift

                            def _infer_shift(u, when_dt):
                                try:
                                    qs = AssignedShift.objects.filter(
                                        staff=u,
                                        shift_date=when_dt.date(),
                                        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                    )
                                    overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                    return overlap or qs.order_by('start_time').first()
                                except Exception:
                                    return None

                            now = timezone.now()
                            incident_type = infer_incident_type(raw_body)
                            occurred_at = extract_occurred_at(raw_body, now)

                            missing = []
                            if not incident_type:
                                missing.append("incident type (Safety/Maintenance/HR/Service/Other)")
                            if not occurred_at:
                                missing.append("time of occurrence (e.g., today 3pm)")

                        if missing:
                            # If we couldn't infer any incident type, ask for clarification.
                            if not incident_type:
                                session.state = 'awaiting_incident_clarification'
                                session.context['pending_incident'] = {'source': 'text', 'transcript': raw_body}
                                session.save(update_fields=['state', 'context'])
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, 'incident_clarify_missing', missing=", ".join(missing))
                                )
                                continue
                            # If we only lack a precise time, default to "now" and still record the report.
                            occurred_at = occurred_at or now

                            shift_obj = _infer_shift(user, occurred_at) if occurred_at else None
                            severity = infer_severity(raw_body)

                            ticket = SafetyConcernReport.objects.create(
                                restaurant=user.restaurant,
                                reporter=user,
                                is_anonymous=False,
                                incident_type=incident_type,
                                title=f"{incident_type} incident reported",
                                description=raw_body.strip(),
                                severity=severity,
                                status='REPORTED',
                                occurred_at=occurred_at,
                                shift=shift_obj,
                            )

                            notification_service.send_lua_incident(
                                user,
                                raw_body,
                                metadata={'channel': 'whatsapp', 'phone': phone_digits, 'ticket_id': str(ticket.id), 'incident_type': incident_type}
                            )

                            occurred_str = occurred_at.strftime('%Y-%m-%d %H:%M') if occurred_at else '—'
                            notification_service.send_whatsapp_text(
                                phone_digits,
                                R(user, 'incident_recorded', ticket_id=str(ticket.id)[:8], incident_type=incident_type, occurred_at=occurred_str)
                            )
                            session.state = 'idle'
                            session.context.pop('pending_incident', None)
                            session.save(update_fields=['state', 'context'])
                        else:
                            notification_service.send_whatsapp_text(phone_digits, R(user, 'link_phone'))
                        continue

                    # Fallback: if the message looks like an incident description, log it directly.
                    if user:
                        from staff.models_task import SafetyConcernReport
                        from scheduling.models import AssignedShift

                        incident_type = infer_incident_type(raw_body)
                        if incident_type:
                            now = timezone.now()
                            occurred_at = now

                            def _infer_shift_text(u, when_dt):
                                try:
                                    qs = AssignedShift.objects.filter(
                                        staff=u,
                                        shift_date=when_dt.date(),
                                        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                                    )
                                    overlap = qs.filter(start_time__lte=when_dt, end_time__gte=when_dt).first()
                                    return overlap or qs.order_by('start_time').first()
                                except Exception:
                                    return None

                            shift_obj = _infer_shift_text(user, occurred_at)
                            severity = infer_severity(raw_body)

                            try:
                                ticket = SafetyConcernReport.objects.create(
                                    restaurant=user.restaurant,
                                    reporter=user,
                                    is_anonymous=False,
                                    incident_type=incident_type,
                                    title=f"{incident_type} incident reported",
                                    description=raw_body.strip(),
                                    severity=severity,
                                    status='REPORTED',
                                    occurred_at=occurred_at,
                                    shift=shift_obj,
                                )

                                notification_service.send_lua_incident(
                                    user,
                                    raw_body,
                                    metadata={
                                        'channel': 'whatsapp',
                                        'phone': phone_digits,
                                        'ticket_id': str(ticket.id),
                                        'incident_type': incident_type,
                                    }
                                )

                                occurred_str = occurred_at.strftime('%Y-%m-%d %H:%M')
                                notification_service.send_whatsapp_text(
                                    phone_digits,
                                    R(user, 'incident_recorded', ticket_id=str(ticket.id)[:8], incident_type=incident_type, occurred_at=occurred_str)
                                )
                                session.state = 'idle'
                                session.context.pop('pending_incident', None)
                                session.save(update_fields=['state', 'context'])
                                return Response({'success': True})
                            except Exception:
                                # Fall through to generic unrecognized response if anything fails
                                pass

                    # Final fallback when no flows matched
                    notification_service.send_whatsapp_text(phone_digits, R(user, 'incident_failed' if 'chair' in raw_body.lower() or 'broken' in raw_body.lower() else 'unrecognized'))

        return Response({'success': True})
    except Exception as e:
        logger.error("Webhook error: %s", e, exc_info=True)
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
