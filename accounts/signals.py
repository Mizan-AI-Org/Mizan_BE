from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import UserInvitation, InvitationDeliveryLog
from notifications.services import notification_service

@receiver(post_save, sender=UserInvitation)
def auto_send_whatsapp_invite(sender, instance: UserInvitation, created, **kwargs):
    if not created:
        return
    try:
        if not getattr(settings, 'AUTO_WHATSAPP_INVITES', False):
            return
        phone = instance.first_name and (instance.__dict__.get('phone') or (instance.__dict__.get('extra_data') or {}).get('phone'))
        phone = phone or (instance.__dict__.get('extra_data') or {}).get('phone_number')
        if not phone:
            return
        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={instance.invitation_token}"
        delay = int(getattr(settings, 'WHATSAPP_INVITE_DELAY_SECONDS', 0))
        if delay > 0:
            from .tasks import send_whatsapp_invitation_task
            send_whatsapp_invitation_task.apply_async(
                args=[str(instance.id), phone, instance.first_name, instance.restaurant.name, invite_link, getattr(settings, 'SUPPORT_CONTACT', '')],
                countdown=delay
            )
            InvitationDeliveryLog.objects.create(
                invitation=instance,
                channel='whatsapp',
                recipient_address=phone,
                status='PENDING'
            )
        else:
            ok, info = notification_service.send_whatsapp_invitation(
                phone=phone,
                first_name=instance.first_name,
                restaurant_name=instance.restaurant.name,
                invite_link=invite_link,
                support_contact=getattr(settings, 'SUPPORT_CONTACT', ''),
                invitation_token=instance.invitation_token
            )
            log = InvitationDeliveryLog(
                invitation=instance,
                channel='whatsapp',
                recipient_address=phone,
                status='SENT' if ok else 'FAILED',
                external_id=(info or {}).get('wamid'),
                response_data=info or {},
            )
            log.save()
    except Exception:
        pass
