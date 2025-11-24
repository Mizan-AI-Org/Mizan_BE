import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from django.contrib.auth import get_user_model
from scheduling.models import AssignedShift
from django.utils import timezone
from scheduling.views import AssignedShiftViewSet
import json

User = get_user_model()

print("Checking Jude's current shift...")
print("=" * 60)

# Find Jude
jude = User.objects.filter(first_name__icontains="jude").first()
if not jude:
    print("Jude not found")
    sys.exit(1)

print(f"User: {jude.first_name} {jude.last_name} - {jude.email}")
print(f"User ID: {jude.id}")

today = timezone.now().date()
print(f"Today (server time): {today}")
print(f"Current datetime: {timezone.now()}")

# Get all shifts for today and future
shifts = AssignedShift.objects.filter(staff=jude, shift_date__gte=today).order_by('shift_date', 'start_time')
print(f"\nFound {shifts.count()} shifts from today onwards:")

for shift in shifts:
    print(f"\n  Shift {shift.id}")
    print(f"    Date: {shift.shift_date}")
    print(f"    Time: {shift.start_time} - {shift.end_time}")
    print(f"    Role: {shift.role}")
    templates = shift.task_templates.all()
    print(f"    Templates: {templates.count()}")
    for t in templates:
        print(f"      - {t.name} (ID: {t.id})")
        # Check for ChecklistTemplate
        ct = t.checklist_templates.filter(is_active=True).first()
        if ct:
            print(f"        ✓ ChecklistTemplate: {ct.name} (ID: {ct.id})")
        else:
            print(f"        ✗ NO CHECKLIST TEMPLATE")

# Test the API endpoint
print("\n" + "=" * 60)
print("Testing my_shift_templates API endpoint:")
print("=" * 60)

class FakeRequest:
    def __init__(self, user):
        self.user = user

request = FakeRequest(jude)
viewset = AssignedShiftViewSet()
viewset.request = request

try:
    response = viewset.my_shift_templates(request)
    print(f"Response status: {response.status_code}")
    print(f"Number of templates returned: {len(response.data)}")
    print(f"\nResponse data:")
    print(json.dumps(response.data, indent=2, default=str))
except Exception as e:
    print(f"Error: {e}")
    import traceback
    traceback.print_exc()
