from django.core.management.base import BaseCommand
from django.conf import settings
import firebase_admin
from notifications.models import DeviceToken, NotificationPreference


class Command(BaseCommand):
    help = "Run health checks for the notification system configuration"

    def handle(self, *args, **options):
        checks = {
            'email_configured': bool(getattr(settings, 'EMAIL_BACKEND', '')) and bool(getattr(settings, 'DEFAULT_FROM_EMAIL', '')),
            'firebase_initialized': bool(firebase_admin._apps),
            'whatsapp_configured': bool(getattr(settings, 'WHATSAPP_ACCESS_TOKEN', None)) and bool(getattr(settings, 'WHATSAPP_PHONE_NUMBER_ID', None)),
            'twilio_configured': bool(getattr(settings, 'TWILIO_ACCOUNT_SID', None)) and bool(getattr(settings, 'TWILIO_AUTH_TOKEN', None)) and bool(getattr(settings, 'TWILIO_FROM_NUMBER', None)),
            'device_tokens_count': DeviceToken.objects.count(),
            'announcement_disabled_count': NotificationPreference.objects.filter(announcement_notifications=False).count(),
        }

        for key, val in checks.items():
            self.stdout.write(f"{key}: {val}")

        self.stdout.write(self.style.SUCCESS("Notification health check completed."))