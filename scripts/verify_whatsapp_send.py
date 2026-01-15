import os
import django
import sys

# Setup Django environment
sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from django.conf import settings
from django.contrib.auth import get_user_model
from accounts.models import Restaurant, UserInvitation, InvitationDeliveryLog
from accounts.tasks import send_whatsapp_invitation_task
from accounts.views_invitations import InvitationViewSet
from rest_framework.test import APIRequestFactory, force_authenticate
from datetime import timedelta
from django.utils import timezone

# Force eager execution for Celery tasks to test them synchronously
settings.CELERY_TASK_ALWAYS_EAGER = True

def verify_whatsapp_send():
    print("Starting verification...")
    
    # 1. Setup Data
    User = get_user_model()
    email = "test_owner@example.com"
    password = "password123"
    
    # Create or get owner
    user, created = User.objects.get_or_create(email=email, defaults={
        'first_name': 'Test',
        'last_name': 'Owner',
        'is_active': True
    })
    if created:
        user.set_password(password)
        user.save()
        
    # Create or get restaurant
    restaurant, _ = Restaurant.objects.get_or_create(
        name="Test Restaurant", 
        defaults={'email': 'test_restaurant@example.com'}
    )
    user.restaurant = restaurant
    user.role = 'OWNER'
    user.save()
    
    # 2. Simulate Request to Create Invitation
    factory = APIRequestFactory()
    view = InvitationViewSet.as_view({'post': 'create'})
    
    invite_data = {
        'phone_number': '1234567890',
        'first_name': 'Invitee',
        'last_name': 'User',
        'send_whatsapp': True,
        'role': 'WAITER' # Assuming 'WAITER' is a valid role string or ID. If ID needed, might fail.
        # Let's check roles. Usually roles are strings in this system based on previous reads.
    }
    
    # Need to ensure role exists if it's a foreign key, but serializer might handle string lookup.
    # Looking at serializer code (not shown fully but implied), let's assume string is fine or we need to fetch a role.
    # Let's try with a string first.
    
    request = factory.post('/api/invitations/', invite_data, format='json')
    force_authenticate(request, user=user)
    
    print("Sending invitation request...")
    try:
        response = view(request)
        print(f"Response Status: {response.status_code}")
        print(f"Response Data: {response.data}")
        
        if response.status_code != 201:
            print("FAILED: API did not return 201 Created")
            return False
            
        # 3. Verify Task Execution / Log Creation
        # Since we set EAGER=True, the task should have run and created a log.
        
        # Get the invitation ID from response
        invitation_id = response.data.get('id')
        if not invitation_id:
             # Try to find it by phone
             inv = UserInvitation.objects.filter(restaurant=restaurant, first_name='Invitee').last()
             if inv: invitation_id = inv.id
        
        if not invitation_id:
            print("FAILED: Could not find created invitation")
            return False
            
        print(f"Checking logs for invitation {invitation_id}...")
        log = InvitationDeliveryLog.objects.filter(invitation_id=invitation_id, channel='whatsapp').first()
        
        if log:
            print(f"SUCCESS: Found WhatsApp delivery log! Status: {log.status}")
            return True
        else:
            print("FAILED: No WhatsApp delivery log found.")
            return False

    except Exception as e:
        print(f"EXCEPTION: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = verify_whatsapp_send()
    if success:
        print("\nVERIFICATION PASSED ✅")
        sys.exit(0)
    else:
        print("\nVERIFICATION FAILED ❌")
        sys.exit(1)
