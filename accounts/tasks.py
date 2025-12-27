from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .models import UserInvitation, InvitationDeliveryLog
from notifications.services import notification_service

@shared_task
def send_whatsapp_invitation_task(invitation_id, phone, first_name, restaurant_name, invite_link, support_contact):
    ok, info = notification_service.send_whatsapp_invitation(
        phone=phone,
        first_name=first_name,
        restaurant_name=restaurant_name,
        invite_link=invite_link,
        support_contact=support_contact,
        invitation_token=UserInvitation.objects.get(id=invitation_id).invitation_token
    )
    try:
        invitation = UserInvitation.objects.get(id=invitation_id)
        log = InvitationDeliveryLog(
            invitation=invitation,
            channel='whatsapp',
            recipient_address=phone,
            status='SENT' if ok else 'FAILED',
            external_id=(info or {}).get('wamid'),
            response_data=info or {},
        )
        log.save()
    except UserInvitation.DoesNotExist:
        pass

@shared_task
def retry_failed_whatsapp_invites():
    qs = InvitationDeliveryLog.objects.filter(channel='whatsapp', status='FAILED')[:50]
    for log in qs:
        inv = log.invitation
        phone = log.recipient_address
        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={inv.invitation_token}"
        ok, info = notification_service.send_whatsapp_invitation(
            phone=phone,
            first_name=inv.first_name,
            restaurant_name=inv.restaurant.name,
            invite_link=invite_link,
            support_contact=getattr(settings, 'SUPPORT_CONTACT', ''),
            invitation_token=inv.invitation_token
        )
        log.attempt_count = getattr(log, 'attempt_count', 1) + 1
        log.status = 'SENT' if ok else 'FAILED'
        log.response_data = info or {}
        log.save()
