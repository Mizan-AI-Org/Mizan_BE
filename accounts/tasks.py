from celery import shared_task
from django.conf import settings
from django.utils import timezone
from .models import UserInvitation, InvitationDeliveryLog
from notifications.services import notification_service
import sys


def normalize_phone(phone):
    """Normalize phone to digits only (no +, spaces, dashes)."""
    if not phone:
        return ""
    return ''.join(filter(str.isdigit, str(phone)))


@shared_task
def send_whatsapp_invitation_task(invitation_id, phone, first_name, restaurant_name, invite_link, support_contact):
    """Send WhatsApp invitation via Lua Agent webhook."""
    print(f"[Task] send_whatsapp_invitation_task started for {first_name} ({phone})", file=sys.stderr)
    
    # Normalize phone number: digits only, no + or spaces
    phone = normalize_phone(phone)
    print(f"[Task] Normalized phone: {phone}", file=sys.stderr)
    
    try:
        invitation = UserInvitation.objects.get(id=invitation_id)
        token = invitation.invitation_token
        role = invitation.role
        language = getattr(invitation.restaurant, 'language', 'en') if getattr(invitation, 'restaurant', None) else 'en'
    except UserInvitation.DoesNotExist:
        print(f"[Task] ERROR: Invitation {invitation_id} not found", file=sys.stderr)
        return

    print(f"[Task] Calling Lua webhook for invitation {token[:8]} via notification_service...", file=sys.stderr)
    
    try:
        ok, info = notification_service.send_lua_staff_invite(
            invitation_token=token,
            phone=phone,
            first_name=first_name,
            restaurant_name=restaurant_name,
            invite_link=invite_link,
            role=role,
            language=language,
        )
    except Exception as e:
        print(f"[Task] CRITICAL ERROR calling notification_service: {e}", file=sys.stderr)
        import traceback
        traceback.print_exc(file=sys.stderr)
        return

    print(f"[Task] Lua webhook call completed. ok={ok}, info={info}", file=sys.stderr)
    
    # Ensure info is a dictionary for logging
    if not isinstance(info, dict):
        info = {"raw_response": str(info)}

    try:
        # Update existing WhatsApp log if present (avoids duplicate logs; resend uses same)
        log = InvitationDeliveryLog.objects.filter(
            invitation=invitation, channel='whatsapp'
        ).order_by('-sent_at').first()
        if log:
            log.recipient_address = phone
            log.status = 'SENT' if ok else 'FAILED'
            log.external_id = info.get('eventId') or info.get('external_id')
            log.response_data = info
            log.save(update_fields=['recipient_address', 'status', 'external_id', 'response_data'])
        else:
            log = InvitationDeliveryLog.objects.create(
                invitation=invitation,
                channel='whatsapp',
                recipient_address=phone,
                status='SENT' if ok else 'FAILED',
                external_id=(info or {}).get('eventId'),
                response_data=info or {},
            )
        print(f"[Task] Delivery log saved: {log.status}", file=sys.stderr)
    except Exception as e:
        print(f"[Task] ERROR saving delivery log: {e}", file=sys.stderr)

@shared_task
def retry_failed_whatsapp_invites():
    qs = InvitationDeliveryLog.objects.filter(channel='whatsapp', status='FAILED')[:50]
    for log in qs:
        inv = log.invitation
        phone = log.recipient_address
        invite_link = f"{settings.FRONTEND_URL}/accept-invitation?token={inv.invitation_token}"
        language = getattr(inv.restaurant, 'language', 'en') if getattr(inv, 'restaurant', None) else 'en'
        ok, info = notification_service.send_lua_staff_invite(
            invitation_token=inv.invitation_token,
            phone=phone,
            first_name=inv.first_name,
            restaurant_name=inv.restaurant.name,
            invite_link=invite_link,
            language=language,
        )
        log.attempt_count = getattr(log, 'attempt_count', 1) + 1
        log.status = 'SENT' if ok else 'FAILED'
        log.response_data = info or {}
        log.save()
