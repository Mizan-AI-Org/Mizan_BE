import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from scheduling.task_templates import TaskTemplate
from checklists.models import ChecklistTemplate, ChecklistStep
from django.contrib.auth import get_user_model

User = get_user_model()

print("Creating ChecklistTemplates for TaskTemplates...")
print("=" * 60)

# Get the two templates assigned to Jude
template_ids = [
    '6dabfba0-4140-4c13-b8dd-089d8a8177c0',  # Bar Setup
    '7492677a-a683-465f-bb3f-2288f5160b8b',  # Cash Deposit
]

# Get a user to set as created_by (use the first admin)
admin = User.objects.filter(role='ADMIN').first() or User.objects.first()

for template_id in template_ids:
    try:
        task_template = TaskTemplate.objects.get(id=template_id)
        print(f"\nProcessing: {task_template.name}")
        
        # Check if ChecklistTemplate already exists
        existing = ChecklistTemplate.objects.filter(task_template=task_template, is_active=True).first()
        if existing:
            print(f"  ✓ ChecklistTemplate already exists: {existing.name} (ID: {existing.id})")
            continue
        
        # Create ChecklistTemplate from TaskTemplate
        checklist_template = ChecklistTemplate.objects.create(
            name=task_template.name,
            description=task_template.description or f"Checklist for {task_template.name}",
            category=task_template.template_type or 'CUSTOM',
            task_template=task_template,
            restaurant=task_template.restaurant,
            created_by=admin,
            is_active=True,
        )
        print(f"  ✓ Created ChecklistTemplate: {checklist_template.name} (ID: {checklist_template.id})")
        
        # Create ChecklistSteps from TaskTemplate tasks
        if task_template.tasks and isinstance(task_template.tasks, list):
            for i, task in enumerate(task_template.tasks, start=1):
                step = ChecklistStep.objects.create(
                    template=checklist_template,
                    title=task.get('title', f'Step {i}'),
                    description=task.get('description', ''),
                    order=i,
                    is_required=True,
                )
                print(f"    - Created step {i}: {step.title}")
        else:
            # Create a default step if no tasks
            ChecklistStep.objects.create(
                template=checklist_template,
                title=f"Complete {task_template.name}",
                description="Follow the standard procedure",
                order=1,
                is_required=True,
            )
            print(f"    - Created default step")
            
    except TaskTemplate.DoesNotExist:
        print(f"\n✗ TaskTemplate {template_id} not found")
    except Exception as e:
        print(f"\n✗ Error processing {template_id}: {e}")
        import traceback
        traceback.print_exc()

print("\n" + "=" * 60)
print("Done!")
