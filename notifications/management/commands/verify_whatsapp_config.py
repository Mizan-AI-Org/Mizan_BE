from django.core.management.base import BaseCommand

from core.whatsapp_config import get_whatsapp_access_token, probe_whatsapp_credentials


class Command(BaseCommand):
    help = "Verify WHATSAPP_ACCESS_TOKEN and WHATSAPP_PHONE_NUMBER_ID against Meta Graph API"

    def handle(self, *args, **options):
        token = get_whatsapp_access_token()
        self.stdout.write(f"token_present={bool(token)} token_length={len(token)}")

        result = probe_whatsapp_credentials()
        for key, value in result.items():
            self.stdout.write(f"{key}: {value}")

        if result.get("ok"):
            self.stdout.write(self.style.SUCCESS("WhatsApp credentials look valid."))
        else:
            self.stdout.write(
                self.style.ERROR(
                    "WhatsApp credentials failed verification. "
                    "Generate a new System User token in Meta Business Manager, "
                    "update WHATSAPP_ACCESS_TOKEN in production, and redeploy."
                )
            )
