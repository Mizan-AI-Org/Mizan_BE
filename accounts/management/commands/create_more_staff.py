from django.core.management.base import BaseCommand
from accounts.models import CustomUser, Restaurant

class Command(BaseCommand):
    help = 'Creates 3 additional dummy staff members for testing.'

    def handle(self, *args, **options):
        self.stdout.write("Creating 3 additional staff members...")

        # Get an existing restaurant or create one if none exists
        restaurant, created = Restaurant.objects.get_or_create(
            name="Mizan AI Restaurant",
            defaults={'address': "123 Main St", 'email': "contact@mizan.ai", 'phone': "555-1234"}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created restaurant: {restaurant.name}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Using existing restaurant: {restaurant.name}'))

        roles = ['Supervisor', 'Cashier', 'Barista']
        for i, role in enumerate(roles):
            email = f'newstaff{i+1}@example.com'
            staff, created = CustomUser.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': f'NewStaff{i+1}',
                    'last_name': role,
                    'role': role,
                    'restaurant': restaurant,
                }
            )
            if created:
                staff.set_password('newpassword') # Set a default password for testing
                staff.save()
                self.stdout.write(self.style.SUCCESS(f'Created new staff: {staff.first_name} ({staff.role})'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Staff {staff.first_name} ({staff.role}) already exists.'))

        self.stdout.write(self.style.SUCCESS("Additional staff members creation complete."))
