from django.core.management.base import BaseCommand
from accounts.models import Restaurant, CustomUser
from staff.models import StaffProfile
from scheduling.models import WeeklySchedule
from datetime import datetime
import random

class Command(BaseCommand):
    help = 'Seeds data for Barometre restaurant'

    def handle(self, *args, **kwargs):
        self.stdout.write("🌱 Seeding Barometre Data...")

        # 1. Get or Create Restaurant
        restaurant, created = Restaurant.objects.get_or_create(
            name="Barometre",
            defaults={
                "address": "123 Main St, Marrakech",
                "operating_hours": {
                    "Monday": {"open": "08:00", "close": "23:00"},
                    "Tuesday": {"open": "08:00", "close": "23:00"},
                    "Wednesday": {"open": "08:00", "close": "23:00"},
                    "Thursday": {"open": "08:00", "close": "23:00"},
                    "Friday": {"open": "08:00", "close": "00:00"},
                    "Saturday": {"open": "09:00", "close": "00:00"},
                    "Sunday": {"open": "09:00", "close": "23:00"}
                }
            }
        )
        if created:
            self.stdout.write(f"✅ Created Restaurant: {restaurant.name} ({restaurant.id})")
        else:
            self.stdout.write(f"ℹ️  Found Restaurant: {restaurant.name} ({restaurant.id})")

        # 2. Create Staff Members
        staff_roles = [
            ("Chef Ramsey", "chef", "kitchen"),
            ("Sous Chef Sarah", "kitchen_staff", "kitchen"),
            ("Waiter John", "server", "service"),
            ("Waiter Emily", "server", "service"),
            ("Bartender Mike", "bartender", "service"),
            ("Manager Ahmed", "manager", "management")
        ]

        for name, role, dept in staff_roles:
            first_name, last_name = name.split(" ", 1)
            email = f"{first_name.lower()}.{last_name.lower()}@barometre.ma"
            
            user, user_created = CustomUser.objects.get_or_create(
                email=email,
                defaults={
                    "first_name": first_name,
                    "last_name": last_name,
                    "role": role,
                    "restaurant": restaurant,
                    "is_active": True
                }
            )
            if user_created:
                user.set_password("password123")
                user.save()
                self.stdout.write(f"   👤 Created User: {name} ({role})")
            
            # Ensure StaffProfile
            profile, profile_created = StaffProfile.objects.get_or_create(
                user=user,
                defaults={
                    "position": dept,  # Use position instead of department
                    "hourly_rate": random.randint(50, 100)
                }
            )

        # 3. Create Weekly Schedule for Next Week (Dec 1 - Dec 7, 2025)
        week_start = datetime.strptime("2025-12-01", "%Y-%m-%d").date()
        schedule, sch_created = WeeklySchedule.objects.get_or_create(
            restaurant=restaurant,
            week_start=week_start,
            defaults={
                "status": "draft"
            }
        )
        if sch_created:
            self.stdout.write(f"📅 Created Weekly Schedule for {week_start}")
        else:
            self.stdout.write(f"ℹ️  Found Weekly Schedule for {week_start}")

        # 4. Create Checklist Templates
        from checklists.models import ChecklistTemplate, ChecklistStep
        
        templates_data = [
            ("Kitchen Opening", "kitchen", ["Check inventory", "Turn on ovens", "Sanitize surfaces"]),
            ("Kitchen Closing", "kitchen", ["Clean grill", "Store food", "Turn off gas"]),
            ("Bar Opening", "service", ["Stock ice", "Check kegs", "Cut garnishes"]),
            ("Bar Closing", "service", ["Clean taps", "Restock fridge", "Count cash"]),
            ("Server Shift", "service", ["Check tables", "Fill salt/pepper", "Polish cutlery"])
        ]

        for name, category, steps in templates_data:
            template, t_created = ChecklistTemplate.objects.get_or_create(
                restaurant=restaurant,
                name=name,
                defaults={
                    "category": category,
                    "description": f"Standard {name} checklist"
                }
            )
            if t_created:
                self.stdout.write(f"   📋 Created Template: {name}")
                for i, step_title in enumerate(steps):
                    ChecklistStep.objects.create(
                        template=template,
                        title=step_title,
                        order=i+1,
                        step_type='CHECK'
                    )

        self.stdout.write("\n✅ Seeding Complete!")
        self.stdout.write(f"👉 Restaurant ID: {restaurant.id}")
