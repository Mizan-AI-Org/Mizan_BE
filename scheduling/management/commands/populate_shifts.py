from django.core.management.base import BaseCommand
from django.utils import timezone
from datetime import timedelta, datetime
import random

from accounts.models import CustomUser, Restaurant
from scheduling.models import WeeklySchedule, AssignedShift

class Command(BaseCommand):
    help = 'Populates the database with dummy shifts and related data for testing.'

    def handle(self, *args, **options):
        self.stdout.write("Populating dummy data...")

        # Determine the current week's start and end dates
        today = timezone.localdate()
        start_of_week = today - timedelta(days=today.weekday()) # Monday
        end_of_week = start_of_week + timedelta(days=6) # Sunday

        # Clear existing shifts and weekly schedules for the current week to prevent duplicates
        self.stdout.write(f"Clearing existing shifts and schedules for the week of {start_of_week}...")
        WeeklySchedule.objects.filter(week_start=start_of_week).delete()
        # Note: AssignedShift objects linked to these WeeklySchedule objects will be cascade-deleted
        self.stdout.write(self.style.SUCCESS("Cleared existing data."))

        # 1. Get or create a restaurant
        restaurant, created = Restaurant.objects.get_or_create(
            name="Mizan AI Restaurant",
            defaults={'address': "123 Main St", 'email': "contact@mizan.ai", 'phone': "555-1234"}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created restaurant: {restaurant.name}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Using existing restaurant: {restaurant.name}'))

        # 2. Get or create some staff members (CustomUsers)
        staff_roles = ['Chef', 'Waiter', 'Manager', 'Cleaner']
        staff_members = []
        for i, role in enumerate(staff_roles):
            username_prefix = f'staff{i+1}'
            email = f'{username_prefix}@example.com'
            staff, created = CustomUser.objects.get_or_create(
                email=email,
                defaults={
                    'first_name': f'Staff{i+1}',
                    'last_name': role,
                    'role': role, # Assuming CustomUser has a role field
                    'restaurant': restaurant,
                }
            )
            if created:
                staff.set_password('password') # Set a default password for testing
                staff.save()
                self.stdout.write(self.style.SUCCESS(f'Created staff: {staff.first_name} ({staff.role})'))
            else:
                self.stdout.write(self.style.SUCCESS(f'Using existing staff: {staff.first_name} ({staff.role})'))
            staff_members.append(staff)
        
        if not staff_members:
            self.stdout.write(self.style.ERROR("No staff members found or created. Aborting shift creation."))
            return

        # 3. Create a WeeklySchedule for the current week
        today = timezone.localdate()
        start_of_week = today - timedelta(days=today.weekday()) # Monday
        end_of_week = start_of_week + timedelta(days=6) # Sunday

        weekly_schedule, created = WeeklySchedule.objects.get_or_create(
            restaurant=restaurant,
            week_start=start_of_week,
            defaults={'week_end': end_of_week, 'is_published': True}
        )
        if created:
            self.stdout.write(self.style.SUCCESS(f'Created weekly schedule for {start_of_week}'))
        else:
            self.stdout.write(self.style.SUCCESS(f'Using existing weekly schedule for {start_of_week}'))

        # 4. Create dummy AssignedShifts
        self.stdout.write("Creating dummy assigned shifts...")
        for i in range(7): # For each day of the week
            shift_date = start_of_week + timedelta(days=i)
            assigned_staff_today = set() # Track staff assigned a shift on this day

            # Create 2-4 shifts per day, ensuring unique staff per shift_date
            num_shifts_today = random.randint(2, min(4, len(staff_members)))
            
            for _ in range(num_shifts_today):
                available_staff = [s for s in staff_members if s.id not in assigned_staff_today]
                if not available_staff:
                    self.stdout.write(f'  - No more unique staff available for {shift_date}. Skipping further shift creation for this day.')
                    break
                
                staff = random.choice(available_staff)
                assigned_staff_today.add(staff.id)
                
                start_hour = random.randint(7, 18) # Shifts start between 7 AM and 6 PM
                start_time = datetime.strptime(f'{start_hour:02d}:00:00', '%H:%M:%S').time()
                end_time = datetime.strptime(f'{start_hour + random.randint(2, 4):02d}:00:00', '%H:%M:%S').time() # 2-4 hour shifts

                # Ensure end_time is after start_time and within 24 hours
                if end_time <= start_time: 
                    end_time = (datetime.combine(timezone.localdate(), start_time) + timedelta(hours=random.randint(2,4))).time() # Default 2-4 hours if invalid

                AssignedShift.objects.create(
                    schedule=weekly_schedule,
                    staff=staff,
                    shift_date=shift_date,
                    start_time=start_time,
                    end_time=end_time,
                    role=staff.role,
                    notes=f'Shift for {staff.first_name} on {shift_date.strftime('%Y-%m-%d')}'
                )
                self.stdout.write(f'  - Created shift for {staff.first_name} ({staff.role}) on {shift_date} from {start_time} to {end_time}')

        self.stdout.write(self.style.SUCCESS("Dummy data population complete."))
