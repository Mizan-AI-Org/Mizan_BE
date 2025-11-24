import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from scheduling.models import AssignedShift

# Get the Nov 22 shift
shift_id = '7691572a-23b0-4cb5-b51e-a1dea99e50e7'

try:
    shift = AssignedShift.objects.get(id=shift_id)
    print(f"Shift: {shift.id}")
    print(f"Date: {shift.shift_date}")
    print(f"Time: {shift.start_time} - {shift.end_time}")
    print(f"Staff: {shift.staff.first_name} {shift.staff.last_name}")
    print(f"Role: {shift.role}")
    print(f"\nAssigned templates: {shift.task_templates.count()}")
    
    if shift.task_templates.count() == 0:
        print("\n⚠️  This shift has NO templates assigned!")
        print("The manager needs to edit this shift and assign templates to it.")
except AssignedShift.DoesNotExist:
    print(f"Shift {shift_id} not found")
