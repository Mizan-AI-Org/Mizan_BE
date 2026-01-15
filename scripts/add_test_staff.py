import os
import django
import sys

# Set up Django environment
sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from accounts.models import CustomUser, Restaurant
from django.utils.crypto import get_random_string

def add_test_staff():
    try:
        restaurants = Restaurant.objects.all()
        if not restaurants.exists():
            print("No restaurants found. Please create one first.")
            return

        roles = ['CHEF', 'WAITER', 'BARTENDER', 'CLEANER', 'CASHIER']
        names = [
            ('Ahmed', 'Hassan'),
            ('Fatima', 'Zahra'),
            ('Youssef', 'Alami'),
            ('Nadia', 'Berrada'),
            ('Omar', 'Bennis')
        ]

        for restaurant in restaurants:
            print(f"\nSeeding staff for restaurant: {restaurant.name}")
            for i in range(5):
                first_name, last_name = names[i]
                role = roles[i]
                # Unique email per restaurant
                email = f"{first_name.lower()}.{last_name.lower()}.{str(restaurant.id)[:8]}@example.com"
                
                if not CustomUser.objects.filter(email=email).exists():
                    user = CustomUser.objects.create_user(
                        email=email,
                        password='password123',
                        first_name=first_name,
                        last_name=last_name,
                        role=role,
                        restaurant=restaurant,
                        is_verified=True,
                        is_active=True
                    )
                    try:
                        user.set_pin('1234')
                    except Exception as pin_err:
                        print(f"  Could not set PIN for {email}: {pin_err}")
                    user.save()
                    print(f"  Created staff: {first_name} {last_name} ({role})")
                else:
                    print(f"  Staff with email {email} already exists.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    add_test_staff()
