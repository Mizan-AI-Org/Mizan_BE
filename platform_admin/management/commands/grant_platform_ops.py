from django.core.management.base import BaseCommand
from django.contrib.auth import get_user_model


class Command(BaseCommand):
    help = (
        "Grant SPA Platform Admin access (is_platform_operator + is_staff). "
        "Detaches restaurant so the account is a dedicated Mizan operator — "
        "restaurant SUPER_ADMIN alone must never get /admin."
    )

    def add_arguments(self, parser):
        parser.add_argument("email", type=str)
        parser.add_argument(
            "--superuser",
            action="store_true",
            help="Also set is_superuser (can create other operators).",
        )
        parser.add_argument(
            "--keep-restaurant",
            action="store_true",
            help="Do not clear restaurant FK (discouraged).",
        )
        parser.add_argument(
            "--revoke",
            action="store_true",
            help="Remove platform operator access.",
        )

    def handle(self, *args, **options):
        User = get_user_model()
        email = options["email"].strip().lower()
        try:
            user = User.objects.get(email__iexact=email)
        except User.DoesNotExist:
            self.stderr.write(self.style.ERROR(f"No user with email {email}"))
            return

        if options["revoke"]:
            user.is_platform_operator = False
            user.save(update_fields=["is_platform_operator"])
            self.stdout.write(self.style.SUCCESS(f"Revoked platform ops from {email}"))
            return

        user.is_staff = True
        user.is_platform_operator = True
        if options["superuser"]:
            user.is_superuser = True
        if not options["keep_restaurant"] and user.restaurant_id:
            self.stdout.write(
                self.style.WARNING(
                    f"Clearing restaurant link ({user.restaurant_id}) so this is a dedicated ops account."
                )
            )
            user.restaurant = None
        user.save()
        self.stdout.write(
            self.style.SUCCESS(
                f"Granted platform ops to {email} "
                f"(is_platform_operator=True, is_staff=True, is_superuser={user.is_superuser}). "
                f"Open SPA /admin after signing in."
            )
        )
