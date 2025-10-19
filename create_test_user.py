import os
import django

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from accounts.models import CustomUser, Restaurant

# Test if we can create a user
try:
    # Check if restaurant exists
    restaurant, created = Restaurant.objects.get_or_create(
        name="Test Restaurant",
        defaults={
            'address': 'Test Address',
            'phone': '+1234567890',
            'email': 'test@restaurant.com'
        }
    )
    
    # Create a test user
    user = CustomUser.objects.create_user(
        email="test@example.com",
        password="test123",
        first_name="Test",
        last_name="User",
        role="SUPER_ADMIN",
        restaurant=restaurant
    )
    
    print("✅ User created successfully!")
    print(f"Email: test@example.com")
    print(f"Password: test123")
    
except Exception as e:
    print(f"❌ Error: {e}")