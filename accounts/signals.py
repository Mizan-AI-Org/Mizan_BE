from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import UserInvitation, InvitationDeliveryLog
from notifications.services import notification_service

@receiver(post_save, sender=UserInvitation)
def auto_send_whatsapp_invite(sender, instance: UserInvitation, created, **kwargs):
    if not created:
        return
        
    import logging
    logger = logging.getLogger(__name__)
    
    try:
        if not getattr(settings, 'AUTO_WHATSAPP_INVITES', False):
            logger.info(f"Skipping WhatsApp invite for {instance.email}: AUTO_WHATSAPP_INVITES is False")
            return

        extra_data = instance.extra_data or {}
        # Try all possible keys for phone
        phone = extra_data.get('phone') or extra_data.get('phone_number') or extra_data.get('whatsapp') or getattr(instance, 'phone', None)
        
        # Strip whitespace and check if it's there
        if phone:
            phone = str(phone).strip()

        if not phone:
            logger.warning(f"No phone number found for invitation {instance.id} (email: {instance.email})")
            return

        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={instance.invitation_token}"
        delay = int(getattr(settings, 'WHATSAPP_INVITE_DELAY_SECONDS', 0))
        
        first_name = instance.first_name or "Staff" # Fallback for template
        restaurant_name = instance.restaurant.name if instance.restaurant else "Mizan AI"

        logger.info(f"Triggering WhatsApp invite for {instance.email} to {phone} (delay: {delay}s)")

        from .tasks import send_whatsapp_invitation_task
        send_whatsapp_invitation_task.delay(
            invitation_id=str(instance.id),
            phone=phone,
            first_name=first_name,
            restaurant_name=restaurant_name,
            invite_link=invite_link,
            support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
        )
        InvitationDeliveryLog.objects.create(
            invitation=instance,
            channel='whatsapp',
            recipient_address=phone,
            status='PENDING'
        )
            
    except Exception as e:
        logger.error(f"Error in auto_send_whatsapp_invite: {str(e)}", exc_info=True)
