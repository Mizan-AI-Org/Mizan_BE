from django.core.management.base import BaseCommand
from accounts.models import UserInvitation
from django.utils import timezone

class Command(BaseCommand):
    help = 'Removes all pending WhatsApp invites from the system'

    def handle(self, *args, **options):
        self.stdout.write('Starting cleanup of pending invitations...')
        
        # Identify pending invites
        # We target invites that are not accepted and not expired (or we can just wipe all non-accepted ones as requested "Remove all pending")
        # The user request said "Remove all pending WhatsApp invites", but usually "pending" implies not accepted.
        # I will target all invitations that are NOT accepted.
        
        pending_invites = UserInvitation.objects.filter(is_accepted=False)
        count = pending_invites.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('No pending invitations found.'))
            return

        self.stdout.write(f'Found {count} pending invitations.')
        
        # Log them before deleting (simple print for now, or could write to a file)
        for invite in pending_invites:
            self.stdout.write(f'Deleting invite: {invite.email} (Token: {invite.invitation_token})')

        # Delete
        deleted_count, _ = pending_invites.delete()
        
        # Verify
        remaining = UserInvitation.objects.filter(is_accepted=False).count()
        
        if remaining == 0:
            self.stdout.write(self.style.SUCCESS(f'Successfully deleted {deleted_count} invitations. System is clean.'))
        else:
            self.stdout.write(self.style.ERROR(f'Failed to delete all invitations. {remaining} remaining.'))
