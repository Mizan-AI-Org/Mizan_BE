import os
import sys
import django

sys.path.append('/Users/macbookpro/code/Mizan_AI/mizan-backend')
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'mizan.settings')
django.setup()

from scheduling.task_templates import TaskTemplate
from checklists.models import ChecklistTemplate, ChecklistStep

print("Adding steps to ChecklistTemplates...")
print("=" * 60)

# Get the ChecklistTemplates we just created
checklist_templates = ChecklistTemplate.objects.filter(
    task_template__id__in=[
        '6dabfba0-4140-4c13-b8dd-089d8a8177c0',  # Bar Setup
        '7492677a-a683-465f-bb3f-2288f5160b8b',  # Cash Deposit
    ]
)

for ct in checklist_templates:
    print(f"\nProcessing: {ct.name} (ID: {ct.id})")
    
    # Check if steps already exist
    existing_steps = ct.steps.count()
    if existing_steps > 0:
        print(f"  ✓ Already has {existing_steps} steps")
        continue
    
    # Get the TaskTemplate
    task_template = ct.task_template
    if not task_template:
        print(f"  ✗ No task_template linked")
        continue
    
    # Create steps from TaskTemplate tasks
    if task_template.tasks and isinstance(task_template.tasks, list):
        for i, task in enumerate(task_template.tasks, start=1):
            step = ChecklistStep.objects.create(
                template=ct,
                title=task.get('title', f'Step {i}'),
                description=task.get('description', ''),
                order=i,
                is_required=True,
            )
            print(f"    ✓ Created step {i}: {step.title}")
    else:
        # Create a default step
        step = ChecklistStep.objects.create(
            template=ct,
            title=f"Complete {ct.name}",
            description="Follow the standard procedure",
            order=1,
            is_required=True,
        )
        print(f"    ✓ Created default step: {step.title}")

print("\n" + "=" * 60)
print("Done!")
