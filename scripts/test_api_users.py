import os
import django
import sys
import json

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
sys.path.append(os.getcwd())
django.setup()

from accounts.models import CustomUser
from rest_framework.test import APIRequestFactory, force_authenticate
from accounts.views_invitations import UserManagementViewSet

def test_user_list():
    factory = APIRequestFactory()
    # Let's pick admin@heymizan.ai (Mizan AI Bistro)
    try:
        user = CustomUser.objects.get(email='admin@heymizan.ai')
        print(f"Testing for user: {user.email} (Restaurant: {user.restaurant.name})")
        
        view = UserManagementViewSet.as_view({'get': 'list'})
        request = factory.get('/api/users/?is_active=true')
        force_authenticate(request, user=user)
        response = view(request)
        
        print(f"Status Code: {response.status_code}")
        data = response.data
        if isinstance(data, dict):
            print(f"Count: {data.get('count')}")
            print(f"Results Count: {len(data.get('results', []))}")
            print("Results:")
            for u in data.get('results', []):
                print(f"  - {u['email']} ({u['role']})")
        else:
            print(f"Response is not a dict: {type(data)}")
            print(data)
            
    except CustomUser.DoesNotExist:
        print("User admin@heymizan.ai not found")

if __name__ == "__main__":
    test_user_list()
