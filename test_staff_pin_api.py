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

# Find Staff PIN
staff_pin = User.objects.filter(email="staffpin@example.com").first()
if not staff_pin:
    print("Staff PIN not found")
    sys.exit(1)

print(f"Testing my_shift_templates for: {staff_pin.first_name} {staff_pin.last_name}")
print(f"User ID: {staff_pin.id}")
print()

# Simulate the request
class FakeRequest:
    def __init__(self, user):
        self.user = user

request = FakeRequest(staff_pin)

# Call the view method directly
viewset = AssignedShiftViewSet()
viewset.request = request

try:
    response = viewset.my_shift_templates(request)
    print(f"Response status: {response.status_code}")
    print(f"Response data:")
    print(json.dumps(response.data, indent=2, default=str))
except Exception as e:
    print(f"Error calling my_shift_templates: {e}")
    import traceback
    traceback.print_exc()
