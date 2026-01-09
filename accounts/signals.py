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

        if delay > 0:
            from .tasks import send_whatsapp_invitation_task
            send_whatsapp_invitation_task.apply_async(
                args=[str(instance.id), phone, first_name, restaurant_name, invite_link, getattr(settings, 'SUPPORT_CONTACT', '')],
                countdown=delay
            )
            InvitationDeliveryLog.objects.create(
                invitation=instance,
                channel='whatsapp',
                recipient_address=phone,
                status='PENDING'
            )
        else:
            # Delegate to Lua Agent
            ok, info = notification_service.send_lua_staff_invite(
                invitation_token=instance.invitation_token,
                phone=phone,
                first_name=first_name,
                restaurant_name=restaurant_name,
                invite_link=invite_link
            )
            
            logger.info(f"Lua Agent Response for {instance.email}: ok={ok}, info={info}")

            log = InvitationDeliveryLog(
                invitation=instance,
                channel='whatsapp',
                recipient_address=phone,
                status='SENT' if ok else 'FAILED',
                external_id=(info or {}).get('eventId'), 
                response_data=info or {},
            )
            if not ok:
                log.error_message = str(info) if info else "Unknown error from agent"
            log.save()
            
    except Exception as e:
        logger.error(f"Error in auto_send_whatsapp_invite: {str(e)}", exc_info=True)
