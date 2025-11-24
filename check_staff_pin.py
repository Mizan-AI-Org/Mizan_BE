import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from django.contrib.auth import get_user_model
from scheduling.models import AssignedShift
from scheduling.task_templates import TaskTemplate
from checklists.models import ChecklistTemplate
from django.utils import timezone

User = get_user_model()

print("Checking Staff PIN's shift and templates...")
print("=" * 60)

# Find Staff PIN
staff_pin = User.objects.filter(email="staffpin@example.com").first()
if not staff_pin:
    print("Staff PIN not found, searching by role=CHEF...")
    staff_pin = User.objects.filter(role="CHEF").first()

if not staff_pin:
    print("No CHEF user found")
else:
    print(f"Found: {staff_pin.first_name} {staff_pin.last_name} - {staff_pin.email} (ID: {staff_pin.id})")
    
    today = timezone.now().date()
    print(f"Today: {today}")
    
    # Get shifts for today and future
    shifts = AssignedShift.objects.filter(staff=staff_pin, shift_date__gte=today)
    print(f"\nFound {shifts.count()} shifts for today/future:")
    
    for shift in shifts:
        print(f"\n  Shift {shift.id} on {shift.shift_date}: {shift.role}")
        print(f"    Start: {shift.start_time}, End: {shift.end_time}")
        templates = shift.task_templates.all()
        print(f"    Templates: {templates.count()}")
        
        for template in templates:
            print(f"\n      TaskTemplate: {template.name} (ID: {template.id})")
            
            # Check for ChecklistTemplate
            checklist_templates = template.checklist_templates.filter(is_active=True)
            if checklist_templates.count() > 0:
                for ct in checklist_templates:
                    print(f"        ✓ ChecklistTemplate: {ct.name} (ID: {ct.id})")
                    steps = ct.steps.count()
                    print(f"          Steps: {steps}")
            else:
                print(f"        ✗ NO ACTIVE CHECKLIST TEMPLATE!")
                # Check if there's an inactive one
                all_ct = template.checklist_templates.all()
                if all_ct.count() > 0:
                    print(f"        Found {all_ct.count()} inactive checklist template(s)")

# Also check the Restaurant Closing template directly
print("\n" + "=" * 60)
print("Checking 'Restaurant Closing' template directly:")
print("=" * 60)

closing_template = TaskTemplate.objects.filter(name__icontains="Restaurant Closing").first()
if closing_template:
    print(f"Found: {closing_template.name} (ID: {closing_template.id})")
    checklist_templates = closing_template.checklist_templates.filter(is_active=True)
    print(f"Active ChecklistTemplates: {checklist_templates.count()}")
    for ct in checklist_templates:
        print(f"  - {ct.name} (ID: {ct.id}, Steps: {ct.steps.count()})")
else:
    print("Restaurant Closing template not found")
