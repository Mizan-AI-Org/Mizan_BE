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
    
    # Create or get test user
    user, user_created = CustomUser.objects.get_or_create(
        email="test@example.com",
        defaults={
            'first_name': "Test",
            'last_name': "User",
            'role': "SUPER_ADMIN",
            'restaurant': restaurant,
            'is_verified': True
        }
    )
    
    user.set_password("test123")
    user.save()
    
    print(f"✅ User {'created' if user_created else 'updated'} successfully!")
    
    # Verify authentication locally
    from django.contrib.auth import authenticate
    user_auth = authenticate(email="test@example.com", password="test123")
    if user_auth:
        print("✅ Local authentication successful!")
    else:
        print("❌ Local authentication failed!")
        
except Exception as e:
    print(f"❌ Error: {e}")