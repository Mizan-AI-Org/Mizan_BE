from typing import Optional, Dict, Any, List
from django.utils import timezone
from django.db import transaction
from django.contrib.auth import get_user_model
from .task_templates import TaskTemplate, Task
from .audit import AuditTrailService, AuditActionType, AuditSeverity
from notifications.models import Notification


User = get_user_model()


class RecurrenceService:
    """Generate tasks from active templates based on frequency.

    Provides summary results and logs audit entries. On failures, creates
    system alerts for managers/admins in the associated restaurant.
    """

    @staticmethod
    def _manager_recipients(restaurant) -> List[User]:
        return list(User.objects.filter(restaurant=restaurant, role__in=['MANAGER', 'ADMIN']))

    @staticmethod
    def _should_generate_today(template: TaskTemplate, date) -> bool:
        # Avoid duplicates: if tasks already exist for the template & date, skip
        return not Task.objects.filter(template=template, due_date=date).exists()

    @staticmethod
    def _is_frequency_day(freq: str, date) -> bool:
        # Basic heuristics; can be expanded
        if freq == 'DAILY':
            return True
        if freq == 'WEEKLY':
            return date.weekday() == 0  # Monday
        if freq == 'MONTHLY':
            return date.day == 1
        if freq == 'QUARTERLY':
            return date.day == 1 and date.month in [1, 4, 7, 10]
        if freq == 'ANNUALLY':
            return date.day == 1 and date.month == 1
        # CUSTOM: always require explicit run via API/command
        return False

    @staticmethod
    def generate(date: Optional[Any] = None, frequency: Optional[str] = None, restaurant=None, request=None) -> Dict[str, Any]:
        today = date or timezone.now().date()
        freq_upper = frequency.upper() if frequency else None

        qs = TaskTemplate.objects.filter(is_active=True)
        if restaurant is not None:
            qs = qs.filter(restaurant=restaurant)
        if freq_upper is not None:
            qs = qs.filter(frequency=freq_upper)

        results = {
            'date': str(today),
            'frequency': freq_upper,
            'templates_considered': 0,
            'templates_skipped': 0,
            'templates_generated': 0,
            'tasks_created': 0,
            'errors': [],
            'details': [],
        }

        for template in qs.iterator():
            results['templates_considered'] += 1
            try:
                if freq_upper is None and not RecurrenceService._is_frequency_day(template.frequency, today):
                    results['templates_skipped'] += 1
                    continue
                if not RecurrenceService._should_generate_today(template, today):
                    results['templates_skipped'] += 1
                    continue

                created_count = 0
                with transaction.atomic():
                    for task_data in template.tasks:
                        task = Task.objects.create(
                            restaurant=template.restaurant,
                            title=task_data.get('title'),
                            description=task_data.get('description', ''),
                            priority=task_data.get('priority', 'MEDIUM'),
                            template=template,
                            due_date=today,
                            created_by=template.created_by,
                            is_recurring=True,
                            recurrence_pattern=template.frequency,
                        )
                        created_count += 1

                results['templates_generated'] += 1
                results['tasks_created'] += created_count
                results['details'].append({
                    'template_id': str(template.id),
                    'template_name': template.name,
                    'created_count': created_count,
                })

                # Audit success
                AuditTrailService.log_activity(
                    user=getattr(template, 'created_by', None),
                    action=AuditActionType.TEMPLATE_APPLY,
                    description=f"Generated {created_count} tasks from template '{template.name}' for {today}",
                    content_object=template,
                    severity=AuditSeverity.LOW,
                    metadata={'frequency': template.frequency, 'date': str(today)},
                    request=request,
                )

            except Exception as e:
                results['errors'].append({'template_id': str(template.id), 'name': template.name, 'error': str(e)})
                # Audit failure
                AuditTrailService.log_activity(
                    user=getattr(template, 'created_by', None),
                    action=AuditActionType.TEMPLATE_APPLY,
                    description=f"Failed generating tasks from template '{template.name}' for {today}: {e}",
                    content_object=template,
                    severity=AuditSeverity.HIGH,
                    metadata={'frequency': template.frequency, 'date': str(today)},
                    request=request,
                )
                # System alert to managers
                for recipient in RecurrenceService._manager_recipients(template.restaurant):
                    Notification.objects.create(
                        recipient=recipient,
                        sender=None,
                        title="Task Recurrence Failure",
                        message=f"Failed to generate tasks for template '{template.name}' on {today}: {e}",
                        notification_type='SYSTEM_ALERT',
                        priority='HIGH',
                        data={'template_id': str(template.id), 'date': str(today), 'frequency': template.frequency},
                    )

        return results