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

print("Checking Equipment Maintenance template...")
print("=" * 60)

# Find Equipment Maintenance template
template = TaskTemplate.objects.filter(name__icontains="Equipment Maintenance").first()
if not template:
    print("Equipment Maintenance template not found")
else:
    print(f"Found: {template.name} (ID: {template.id})")
    
    # Check for ChecklistTemplate
    checklist_templates = template.checklist_templates.filter(is_active=True)
    if checklist_templates.count() > 0:
        print(f"✓ Has {checklist_templates.count()} active ChecklistTemplate(s)")
        for ct in checklist_templates:
            print(f"  - {ct.name} (ID: {ct.id}, Steps: {ct.steps.count()})")
    else:
        print("✗ NO ACTIVE CHECKLIST TEMPLATE")
        
        # Create one
        admin = User.objects.filter(role='ADMIN').first() or User.objects.first()
        
        ct = ChecklistTemplate.objects.create(
            name=template.name,
            description=template.description or f"Checklist for {template.name}",
            category=template.template_type or 'MAINTENANCE',
            task_template=template,
            restaurant=template.restaurant,
            created_by=admin,
            is_active=True,
        )
        print(f"\n✓ Created ChecklistTemplate: {ct.name} (ID: {ct.id})")
        
        # Create steps from tasks
        if template.tasks and isinstance(template.tasks, list):
            for i, task in enumerate(template.tasks, start=1):
                step = ChecklistStep.objects.create(
                    template=ct,
                    title=task.get('title', f'Step {i}'),
                    description=task.get('description', ''),
                    order=i,
                    is_required=True,
                )
                print(f"  ✓ Created step {i}: {step.title}")
        else:
            # Create default steps
            default_steps = [
                {"title": "Inspect equipment", "description": "Check all equipment for damage or wear"},
                {"title": "Clean equipment", "description": "Clean and sanitize all equipment"},
                {"title": "Test equipment", "description": "Test equipment functionality"},
                {"title": "Document findings", "description": "Record any issues or maintenance performed"},
            ]
            for i, step_data in enumerate(default_steps, start=1):
                step = ChecklistStep.objects.create(
                    template=ct,
                    title=step_data['title'],
                    description=step_data['description'],
                    order=i,
                    is_required=True,
                )
                print(f"  ✓ Created step {i}: {step.title}")
