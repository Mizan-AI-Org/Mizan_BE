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

print("Creating ChecklistTemplate for Restaurant Closing...")
print("=" * 60)

# Get the Restaurant Closing template
template_id = '210aee92-ef3c-464b-a8ed-e7b043638b41'

try:
    task_template = TaskTemplate.objects.get(id=template_id)
    print(f"Found TaskTemplate: {task_template.name}")
    
    # Check if ChecklistTemplate already exists
    existing = ChecklistTemplate.objects.filter(task_template=task_template, is_active=True).first()
    if existing:
        print(f"✓ ChecklistTemplate already exists: {existing.name} (ID: {existing.id})")
    else:
        # Get admin user
        admin = User.objects.filter(role='ADMIN').first() or User.objects.first()
        
        # Create ChecklistTemplate
        checklist_template = ChecklistTemplate.objects.create(
            name=task_template.name,
            description=task_template.description or f"Checklist for {task_template.name}",
            category=task_template.template_type or 'CLOSING',
            task_template=task_template,
            restaurant=task_template.restaurant,
            created_by=admin,
            is_active=True,
        )
        print(f"✓ Created ChecklistTemplate: {checklist_template.name} (ID: {checklist_template.id})")
        
        # Create steps from TaskTemplate tasks
        if task_template.tasks and isinstance(task_template.tasks, list):
            for i, task in enumerate(task_template.tasks, start=1):
                step = ChecklistStep.objects.create(
                    template=checklist_template,
                    title=task.get('title', f'Step {i}'),
                    description=task.get('description', ''),
                    order=i,
                    is_required=True,
                )
                print(f"  ✓ Created step {i}: {step.title}")
        else:
            # Create default steps for closing
            steps_data = [
                {"title": "Complete all closing tasks", "description": "Ensure all tasks are completed"},
                {"title": "Verify cleanliness", "description": "Check that all areas are clean"},
                {"title": "Secure the premises", "description": "Lock all doors and windows"},
            ]
            for i, step_data in enumerate(steps_data, start=1):
                step = ChecklistStep.objects.create(
                    template=checklist_template,
                    title=step_data['title'],
                    description=step_data['description'],
                    order=i,
                    is_required=True,
                )
                print(f"  ✓ Created step {i}: {step.title}")
                
except TaskTemplate.DoesNotExist:
    print(f"✗ TaskTemplate {template_id} not found")
except Exception as e:
    print(f"✗ Error: {e}")
    import traceback
    traceback.print_exc()

print("\n" + "=" * 60)
print("Done!")
