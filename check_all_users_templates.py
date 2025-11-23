import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from django.contrib.auth import get_user_model
from scheduling.views import AssignedShiftViewSet
import json

User = get_user_model()

# Find the user who's seeing Equipment Maintenance (looks like "AI" from the screenshot)
# Let's check all users to find who has this template
users = User.objects.filter(role__in=['STAFF', 'CASHIER', 'CLEANER', 'CHEF'])

for user in users:
    print(f"\nChecking user: {user.first_name} {user.last_name} ({user.email})")
    
    class FakeRequest:
        def __init__(self, user):
            self.user = user
    
    request = FakeRequest(user)
    viewset = AssignedShiftViewSet()
    viewset.request = request
    
    try:
        response = viewset.my_shift_templates(request)
        if response.data:
            print(f"  Templates: {len(response.data)}")
            for template in response.data:
                print(f"    - {template['name']}")
                print(f"      checklist_template_id: {template.get('checklist_template_id', 'MISSING!')}")
    except Exception as e:
        print(f"  Error: {e}")
