import os
import sys
import django

# Add the project root to the python path
sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from django.contrib.auth import get_user_model
from scheduling.models import AssignedShift
from django.utils import timezone

User = get_user_model()

# Check for Jude
print("=" * 50)
print("Checking for JUDE:")
print("=" * 50)
try:
    jude = User.objects.filter(first_name__icontains="jude").first()
    if jude:
        print(f"Found user: {jude.first_name} {jude.last_name} - {jude.email} (ID: {jude.id})")
        
        today = timezone.now().date()
        print(f"Today: {today}")
        
        shifts = AssignedShift.objects.filter(staff=jude, shift_date__gte=today)
        print(f"Found {shifts.count()} shifts for today/future")
        
        for shift in shifts:
            print(f"\nShift {shift.id} on {shift.shift_date}: {shift.role}")
            print(f"  Start: {shift.start_time}, End: {shift.end_time}")
            templates = shift.task_templates.all()
            print(f"  Templates: {templates.count()}")
            for t in templates:
                print(f"    - {t.name} (ID: {t.id})")
    else:
        print("Jude not found")
except Exception as e:
    print(f"Error checking Jude: {e}")

# Also check Adama
print("\n" + "=" * 50)
print("Checking for ADAMA:")
print("=" * 50)
try:
    adama = User.objects.filter(email="prodetecttechnologies@gmail.com").first()
    if adama:
        print(f"Found user: {adama.first_name} {adama.last_name} - {adama.email} (ID: {adama.id})")
        
        today = timezone.now().date()
        shifts = AssignedShift.objects.filter(staff=adama, shift_date__gte=today)
        print(f"Found {shifts.count()} shifts for today/future")
        
        for shift in shifts:
            print(f"\nShift {shift.id} on {shift.shift_date}: {shift.role}")
            print(f"  Start: {shift.start_time}, End: {shift.end_time}")
            templates = shift.task_templates.all()
            print(f"  Templates: {templates.count()}")
            for t in templates:
                print(f"    - {t.name} (ID: {t.id})")
    else:
        print("Adama not found")
except Exception as e:
    print(f"Error checking Adama: {e}")
