import os
import sys
import django
import random
from datetime import datetime, timedelta

# Setup Django environment
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan_backend.settings')
django.setup()

from accounts.models import Restaurant, CustomUser
from staff.models import StaffProfile
from scheduling.models import WeeklySchedule

def seed_data():
    print("🌱 Seeding Barometre Data...")

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
        print(f"✅ Created Restaurant: {restaurant.name} ({restaurant.id})")
    else:
        print(f"ℹ️  Found Restaurant: {restaurant.name} ({restaurant.id})")

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
                "username": email,
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
            print(f"   👤 Created User: {name} ({role})")
        
        # Ensure StaffProfile
        profile, profile_created = StaffProfile.objects.get_or_create(
            user=user,
            defaults={
                "department": dept,
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
        print(f"📅 Created Weekly Schedule for {week_start}")
    else:
        print(f"ℹ️  Found Weekly Schedule for {week_start}")

    print("\n✅ Seeding Complete!")
    print(f"👉 Restaurant ID: {restaurant.id}")

if __name__ == "__main__":
    seed_data()
