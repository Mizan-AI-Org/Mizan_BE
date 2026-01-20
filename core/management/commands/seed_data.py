import random
import uuid
from datetime import datetime, timedelta
from django.core.management.base import BaseCommand
from django.utils import timezone
from django.contrib.auth import get_user_model
from django.db import transaction

# Model imports
from accounts.models import Restaurant, CustomUser
from staff.models import StaffProfile, Schedule
from scheduling.models import TaskCategory
from scheduling.task_templates import TaskTemplate

User = get_user_model()

class Command(BaseCommand):
    help = 'Seeds the database with realistic test data for 5 staff members and fills the frontend UI'

    def handle(self, *args, **kwargs):
        self.stdout.write('Starting seeding process...')
        
        try:
            with transaction.atomic():
                # 1. Ensure a Restaurant exists
                restaurant, created = Restaurant.objects.get_or_create(
                    email="contact@mizandining.com",
                    defaults={
                        'name': "Mizan Fine Dining",
                        'address': "123 Gourmet Way, Food City",
                        'phone': "+1234567890",
                        'timezone': "UTC",
                    }
                )
                if created:
                    self.stdout.write(f'Created restaurant: {restaurant.name}')

                # 2. Create/Update Admin User
                admin_email = "admin@heymizan.ai"
                admin_user, created = User.objects.get_or_create(
                    email=admin_email,
                    defaults={
                        'first_name': "Admin",
                        'last_name': "User",
                        'role': "MANAGER",
                        'restaurant': restaurant,
                        'is_staff': True,
                        'is_superuser': True,
                        'is_active': True,
                    }
                )
                if created:
                    admin_user.set_password('password123')
                    admin_user.save()
                    self.stdout.write(f'Created admin user: {admin_email}')

                # 3. Create 5 Staff Members
                staff_data = [
                    ("Adama", "Diop", "WAITER", "adama@heymizan.ai", "1234"),
                    ("Sarah", "Connor", "CHEF", "sarah@heymizan.ai", "2234"),
                    ("John", "Smith", "WAITER", "john@heymizan.ai", "3234"),
                    ("Emily", "Chen", "KITCHEN_STAFF", "emily@heymizan.ai", "4234"),
                    ("Michael", "Wong", "CLEANER", "michael@heymizan.ai", "5234"),
                ]

                staff_users = []
                for first, last, role, email, pin in staff_data:
                    user, created = User.objects.get_or_create(
                        email=email,
                        defaults={
                            'first_name': first,
                            'last_name': last,
                            'role': role,
                            'restaurant': restaurant,
                            'is_active': True,
                        }
                    )
                    if created:
                        user.set_pin(pin)
                        user.save()
                    
                    # Ensure StaffProfile exists
                    profile, p_created = StaffProfile.objects.get_or_create(
                        user=user,
                        defaults={
                            'position': role,
                            'hourly_rate': random.randint(15, 30),
                        }
                    )
                    staff_users.append(user)
                    self.stdout.write(f'{"Created" if created else "Ensured"} staff: {first} {last} ({role})')

                # 4. Create Task Templates
                self.stdout.write('Creating Task Templates...')
                templates_to_create = [
                    {
                        'name': "Morning Opening Checklist",
                        'type': 'OPENING',
                        'tasks': [
                            {"id": str(uuid.uuid4()), "title": "Check kitchen hygiene", "completed": False},
                            {"id": str(uuid.uuid4()), "title": "Verify inventory levels", "completed": False},
                            {"id": str(uuid.uuid4()), "title": "Pre-service briefing", "completed": False}
                        ]
                    },
                    {
                        'name': "Closing Standards Audit",
                        'type': 'CLOSING',
                        'tasks': [
                            {"id": str(uuid.uuid4()), "title": "Secure all exits", "completed": False},
                            {"id": str(uuid.uuid4()), "title": "Cold storage temperature check", "completed": False},
                            {"id": str(uuid.uuid4()), "title": "Sanitize high-touch surfaces", "completed": False}
                        ]
                    }
                ]

                created_templates = []
                for t_data in templates_to_create:
                    template, created = TaskTemplate.objects.get_or_create(
                        name=t_data['name'],
                        restaurant=restaurant,
                        defaults={
                            'template_type': t_data['type'],
                            'tasks': t_data['tasks'],
                            'created_by': admin_user
                        }
                    )
                    created_templates.append(template)
                    self.stdout.write(f'{"Created" if created else "Ensured"} template: {t_data["name"]}')

                # 5. Create Schedules (Shifts) for the current week
                self.stdout.write('Creating Schedules...')
                now = timezone.now()
                start_of_week = now - timedelta(days=now.weekday())
                
                # Create shifts for the next 7 days
                for i in range(7):
                    day = start_of_week + timedelta(days=i)
                    for user in staff_users:
                        # Assign a shift with 80% probability
                        if random.random() < 0.8:
                            # Random start time between 8 AM and 4 PM
                            start_hour = random.randint(8, 16)
                            start_dt = timezone.make_aware(datetime.combine(day, datetime.min.time().replace(hour=start_hour)))
                            end_dt = start_dt + timedelta(hours=8)
                            
                            # Create schedule
                            schedule, created = Schedule.objects.get_or_create(
                                staff=user,
                                restaurant=restaurant,
                                start_time=start_dt,
                                defaults={
                                    'end_time': end_dt,
                                    'title': f"{user.first_name}'s {user.role} Shift",
                                    'status': 'SCHEDULED',
                                    'color': '#3498db' if user.role == 'WAITER' else '#e67e22' if user.role == 'CHEF' else '#2ecc71',
                                    'created_by': admin_user
                                }
                            )
                            if created:
                                # Randomly assign a task template to some shifts
                                if random.random() < 0.5:
                                    template = random.choice(created_templates)
                                    # Note: Shift model has a JSON field 'tasks' or similar in some versions, 
                                    # but based on my earlier view, it just has a JSONField(default=list) for 'tasks'.
                                    # And 'AssignedShift' had ManyToMany to 'TaskTemplate'.
                                    # Since we are using staff.Schedule, let's just populate the JSON tasks if needed.
                                    schedule.tasks = template.tasks
                                    schedule.save()
                
                self.stdout.write('Seeding completed successfully!')

        except Exception as e:
            self.stderr.write(f'Error during seeding: {str(e)}')
            import traceback
            self.stderr.write(traceback.format_exc())

