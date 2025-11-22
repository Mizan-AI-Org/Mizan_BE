import os
import sys
import django
import json

# Add the project root to the python path
sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from django.contrib.auth import get_user_model
from scheduling.models import AssignedShift
from django.utils import timezone
from scheduling.views import AssignedShiftViewSet

User = get_user_model()

# Find Jude
jude = User.objects.filter(first_name__icontains="jude").first()
if not jude:
    print("Jude not found")
    sys.exit(1)

print(f"Testing my_shift_templates for: {jude.first_name} {jude.last_name}")
print(f"User ID: {jude.id}")
print()

# Simulate the request
class FakeRequest:
    def __init__(self, user):
        self.user = user

request = FakeRequest(jude)

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
