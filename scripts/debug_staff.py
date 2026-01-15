import os
import django
import sys

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
sys.path.append(os.getcwd())
django.setup()

from accounts.models import CustomUser, Restaurant

def check_staff():
    restaurant = Restaurant.objects.first()
    print(f"Restaurant: {restaurant.name if restaurant else 'None'}")
    
    staff = CustomUser.objects.all()
    print(f"\nTotal users in DB: {staff.count()}")
    
    print("\nStaff list:")
    print(f"{'Email':<40} | {'Role':<15} | {'Active':<8} | {'Restaurant':<20}")
    print("-" * 90)
    for user in staff:
        rest_name = user.restaurant.name if user.restaurant else "None"
        print(f"{user.email:<40} | {user.role:<15} | {str(user.is_active):<8} | {rest_name:<20}")

if __name__ == "__main__":
    check_staff()
