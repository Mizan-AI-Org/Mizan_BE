"""
Revoke all pending invites for a restaurant so you can do a fresh invite.

- Deletes StaffActivationRecord with status NOT_ACTIVATED (ONE-TAP pending).
- Deletes UserInvitation with is_accepted=False (old email/token invites).

Usage:
  python manage.py revoke_pending_invites "Mizan AI Bistro"
"""
from django.core.management.base import BaseCommand
from django.db import transaction
from accounts.models import Restaurant, StaffActivationRecord, UserInvitation


class Command(BaseCommand):
    help = 'Revoke all pending invites (activation records + old invitations) for a restaurant by name.'

    def add_arguments(self, parser):
        parser.add_argument(
            'restaurant_name',
            type=str,
            help='Exact restaurant name, e.g. "Mizan AI Bistro"',
        )

    def handle(self, *args, **options):
        name = options['restaurant_name'].strip()
        if not name:
            self.stdout.write(self.style.ERROR('Restaurant name is required.'))
            return

        try:
            restaurant = Restaurant.objects.get(name=name)
        except Restaurant.DoesNotExist:
            self.stdout.write(self.style.ERROR(f'Restaurant not found: "{name}"'))
            return

        with transaction.atomic():
            # ONE-TAP: pending activation records (not yet activated)
            pending_activation = StaffActivationRecord.objects.filter(
                restaurant=restaurant,
                status=StaffActivationRecord.STATUS_NOT_ACTIVATED,
            )
            activation_count = pending_activation.count()
            pending_activation.delete()

            # Old flow: token/email invitations not yet accepted
            pending_invitations = UserInvitation.objects.filter(
                restaurant=restaurant,
                is_accepted=False,
            )
            invitation_count = pending_invitations.count()
            pending_invitations.delete()

        self.stdout.write(
            self.style.SUCCESS(
                f'Revoked for "{restaurant.name}": '
                f'{activation_count} pending activation record(s), '
                f'{invitation_count} pending invitation(s). You can do a fresh invite now.'
            )
        )
