import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from scheduling.task_templates import TaskTemplate
from checklists.models import ChecklistTemplate

print("Checking TaskTemplate -> ChecklistTemplate relationships:")
print("=" * 60)

# Get the two templates assigned to Jude
template_ids = [
    '6dabfba0-4140-4c13-b8dd-089d8a8177c0',  # Bar Setup
    '7492677a-a683-465f-bb3f-2288f5160b8b',  # Cash Deposit
]

for template_id in template_ids:
    try:
        task_template = TaskTemplate.objects.get(id=template_id)
        print(f"\nTaskTemplate: {task_template.name} (ID: {task_template.id})")
        
        # Check for associated ChecklistTemplates
        checklist_templates = task_template.checklist_templates.filter(is_active=True)
        print(f"  Active ChecklistTemplates: {checklist_templates.count()}")
        
        for ct in checklist_templates:
            print(f"    - {ct.name} (ID: {ct.id})")
            
        if checklist_templates.count() == 0:
            print("  ⚠️  NO ACTIVE CHECKLIST TEMPLATE FOUND!")
            # Check if there are any inactive ones
            all_ct = task_template.checklist_templates.all()
            if all_ct.count() > 0:
                print(f"  Found {all_ct.count()} inactive checklist template(s):")
                for ct in all_ct:
                    print(f"    - {ct.name} (ID: {ct.id}, is_active={ct.is_active})")
    except TaskTemplate.DoesNotExist:
        print(f"\nTaskTemplate {template_id} not found")
    except Exception as e:
        print(f"\nError checking {template_id}: {e}")
