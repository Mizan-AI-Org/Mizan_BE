from django.db.models.signals import post_save
from django.dispatch import receiver
from django.conf import settings
from .models import UserInvitation, InvitationDeliveryLog
from notifications.services import notification_service
import logging
import sys

logger = logging.getLogger(__name__)


def normalize_phone(phone):
    """
    Normalize phone number to digits only (no +, spaces, or dashes).
    Format: 2203736808 (country code + local number, no +)
    """
    if not phone:
        return ""
    return ''.join(filter(str.isdigit, str(phone)))


@receiver(post_save, sender=UserInvitation)
def auto_send_whatsapp_invite(sender, instance: UserInvitation, created, **kwargs):
    """Automatically send WhatsApp invitation when a new UserInvitation is created."""
    if not created:
        return
    
    try:
        auto_whatsapp = getattr(settings, 'AUTO_WHATSAPP_INVITES', True)
        # logger.info(f"[Signal] AUTO_WHATSAPP_INVITES = {auto_whatsapp}")

        
        if not auto_whatsapp:
            logger.info(f"Skipping WhatsApp invite for {instance.email}: AUTO_WHATSAPP_INVITES is False")
            return

        extra_data = instance.extra_data or {}
        
        # Try all possible keys for phone
        raw_phone = (
            extra_data.get('phone') or 
            extra_data.get('phone_number') or 
            extra_data.get('whatsapp') or 
            getattr(instance, 'phone', None)
        )
        
        # Normalize phone: digits only, no + or spaces (e.g., "2203736808")
        phone = normalize_phone(raw_phone)
        
        # logger.info(f"[Signal] Raw phone: {raw_phone} -> Normalized: {phone}")


        if not phone:
            logger.warning(f"No phone number found for invitation {instance.id} (email: {instance.email})")
            return

        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={instance.invitation_token}"
        first_name = instance.first_name or "Staff"
        restaurant_name = instance.restaurant.name if instance.restaurant else "Mizan AI"

        # logger.info(f"[Signal] Triggering WhatsApp invite for {first_name} to {phone}")

        logger.info(f"[Signal] Triggering WhatsApp invite for {instance.email} to {phone}")

        from .tasks import send_whatsapp_invitation_task
        result = send_whatsapp_invitation_task.delay(
            invitation_id=str(instance.id),
            phone=phone,
            first_name=first_name,
            restaurant_name=restaurant_name,
            invite_link=invite_link,
            support_contact=getattr(settings, 'SUPPORT_CONTACT', '')
        )
        
        # logger.info(f"[Signal] Task queued with ID: {result.id}")

        
        InvitationDeliveryLog.objects.create(
            invitation=instance,
            channel='whatsapp',
            recipient_address=phone,
            status='PENDING'
        )
            
    except Exception as e:
        # logger.error(f"[Signal] ERROR: {str(e)}")

        logger.error(f"Error in auto_send_whatsapp_invite: {str(e)}", exc_info=True)
