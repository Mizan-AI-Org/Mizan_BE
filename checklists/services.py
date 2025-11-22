"""
Business logic services for the checklist system
"""
from django.utils import timezone
from django.db import transaction
from django.core.exceptions import ValidationError
from typing import Dict, List, Any, Optional
import json
from datetime import datetime, timedelta

from .models import (
    ChecklistExecution, ChecklistStepResponse, ChecklistEvidence,
    ChecklistAction, ChecklistTemplate, ChecklistStep
)


class ChecklistValidationService:
    """Service for validating checklist data and business rules"""
    
    def validate_execution_completion(self, execution: ChecklistExecution) -> Dict[str, Any]:
        """
        Validate if a checklist execution can be completed
        
        Returns:
            Dict with 'is_valid' boolean and 'errors' list
        """
        errors = []
        
        # Check if all required steps are completed
        required_steps = execution.template.steps.filter(is_required=True)
        completed_responses = execution.step_responses.filter(
            step__in=required_steps,
            is_completed=True
        )
        
        if completed_responses.count() < required_steps.count():
            missing_steps = required_steps.exclude(
                id__in=completed_responses.values_list('step_id', flat=True)
            )
            errors.append({
                'type': 'missing_required_steps',
                'message': 'Required steps not completed',
                'steps': [step.title for step in missing_steps]
            })
        
        # Check if all photo requirements are met
        photo_required_steps = execution.template.steps.filter(requires_photo=True)
        for step in photo_required_steps:
            response = execution.step_responses.filter(step=step).first()
            if not response or not response.evidence.filter(evidence_type='PHOTO').exists():
                errors.append({
                    'type': 'missing_photo_evidence',
                    'message': f'Photo required for step: {step.title}',
                    'step_id': step.id
                })
        
        # Check if all signature requirements are met
        signature_required_steps = execution.template.steps.filter(requires_signature=True)
        for step in signature_required_steps:
            response = execution.step_responses.filter(step=step).first()
            if not response or not response.signature_data:
                errors.append({
                    'type': 'missing_signature',
                    'message': f'Signature required for step: {step.title}',
                    'step_id': step.id
                })
        
        # Check measurement validations
        measurement_steps = execution.template.steps.filter(
            step_type='MEASUREMENT'
        ).exclude(measurement_type='')
        
        for step in measurement_steps:
            response = execution.step_responses.filter(step=step).first()
            if response and response.measurement_value is not None:
                validation_result = self._validate_measurement(step, response.measurement_value)
                if not validation_result['is_valid']:
                    errors.extend(validation_result['errors'])
        
        # Check supervisor approval requirement
        if execution.template.requires_supervisor_approval and not execution.approved_by:
            errors.append({
                'type': 'supervisor_approval_required',
                'message': 'Supervisor approval required before completion'
            })
        
        return {
            'is_valid': len(errors) == 0,
            'errors': errors
        }
    
    def _validate_measurement(self, step: ChecklistStep, value: float) -> Dict[str, Any]:
        """Validate measurement values against step constraints"""
        errors = []
        
        if step.min_value is not None and value < step.min_value:
            errors.append({
                'type': 'measurement_below_minimum',
                'message': f'Value {value} is below minimum {step.min_value}',
                'step_id': step.id
            })
        
        if step.max_value is not None and value > step.max_value:
            errors.append({
                'type': 'measurement_above_maximum',
                'message': f'Value {value} is above maximum {step.max_value}',
                'step_id': step.id
            })
        
        # Check custom validation rules
        if step.validation_rules:
            try:
                rules = json.loads(step.validation_rules)
                for rule in rules:
                    if not self._evaluate_validation_rule(rule, value):
                        errors.append({
                            'type': 'custom_validation_failed',
                            'message': rule.get('message', 'Custom validation failed'),
                            'step_id': step.id
                        })
            except (json.JSONDecodeError, KeyError):
                pass  # Skip invalid validation rules
        
        return {
            'is_valid': len(errors) == 0,
            'errors': errors
        }
    
    def _evaluate_validation_rule(self, rule: Dict, value: float) -> bool:
        """Evaluate a custom validation rule"""
        rule_type = rule.get('type')
        
        if rule_type == 'range':
            min_val = rule.get('min')
            max_val = rule.get('max')
            return (min_val is None or value >= min_val) and (max_val is None or value <= max_val)
        
        elif rule_type == 'equals':
            return value == rule.get('value')
        
        elif rule_type == 'not_equals':
            return value != rule.get('value')
        
        return True  # Unknown rule types pass by default
    
    def validate_step_response(self, step: ChecklistStep, response_data: Dict) -> Dict[str, Any]:
        """Validate a step response before saving"""
        errors = []
        
        # Check required fields
        if step.is_required and not response_data.get('is_completed'):
            errors.append({
                'type': 'required_step_incomplete',
                'message': 'This step is required and must be completed'
            })
        
        # Check photo requirement
        if step.requires_photo and not response_data.get('has_photo_evidence'):
            errors.append({
                'type': 'photo_required',
                'message': 'Photo evidence is required for this step'
            })
        
        # Check note requirement
        if step.requires_note and not response_data.get('notes'):
            errors.append({
                'type': 'note_required',
                'message': 'Notes are required for this step'
            })
        
        # Check signature requirement
        if step.requires_signature and not response_data.get('signature_data'):
            errors.append({
                'type': 'signature_required',
                'message': 'Digital signature is required for this step'
            })
        
        return {
            'is_valid': len(errors) == 0,
            'errors': errors
        }


class ChecklistSyncService:
    """Service for handling offline synchronization of checklist data"""
    
    def sync_execution_data(self, execution: ChecklistExecution, sync_data: Dict) -> Dict[str, Any]:
        """
        Synchronize offline checklist execution data
        
        Args:
            execution: The checklist execution to sync
            sync_data: Dictionary containing offline changes
            
        Returns:
            Dictionary with sync results and conflicts
        """
        synced_items = []
        conflicts = []
        
        with transaction.atomic():
            # Update execution metadata
            if 'execution_updates' in sync_data:
                execution_updates = sync_data['execution_updates']
                conflict = self._check_execution_conflict(execution, execution_updates)
                
                if conflict:
                    conflicts.append(conflict)
                else:
                    self._apply_execution_updates(execution, execution_updates)
                    synced_items.append({
                        'type': 'execution',
                        'id': execution.id,
                        'action': 'updated'
                    })
            
            # Sync step responses
            if 'step_responses' in sync_data:
                for response_data in sync_data['step_responses']:
                    result = self._sync_step_response(execution, response_data)
                    synced_items.extend(result['synced_items'])
                    conflicts.extend(result['conflicts'])
            
            # Sync evidence files
            if 'evidence' in sync_data:
                for evidence_data in sync_data['evidence']:
                    result = self._sync_evidence(execution, evidence_data)
                    synced_items.extend(result['synced_items'])
                    conflicts.extend(result['conflicts'])
            
            # Sync actions
            if 'actions' in sync_data:
                for action_data in sync_data['actions']:
                    result = self._sync_action(execution, action_data)
                    synced_items.extend(result['synced_items'])
                    conflicts.extend(result['conflicts'])
            
            # Update sync version
            execution.sync_version += 1
            execution.last_synced_at = timezone.now()
            execution.save()
        
        return {
            'synced_items': synced_items,
            'conflicts': conflicts
        }
    
    def _check_execution_conflict(self, execution: ChecklistExecution, updates: Dict) -> Optional[Dict]:
        """Check for conflicts in execution updates"""
        client_version = updates.get('sync_version', 0)
        
        if client_version < execution.sync_version:
            return {
                'type': 'execution_conflict',
                'message': 'Execution has been modified by another user',
                'server_version': execution.sync_version,
                'client_version': client_version
            }
        
        return None
    
    def _apply_execution_updates(self, execution: ChecklistExecution, updates: Dict):
        """Apply updates to execution"""
        allowed_fields = [
            'progress_percentage', 'completion_notes', 'status',
            'started_at', 'completed_at'
        ]
        
        for field in allowed_fields:
            if field in updates:
                if field in ['started_at', 'completed_at'] and updates[field]:
                    # Parse datetime strings
                    setattr(execution, field, datetime.fromisoformat(updates[field]))
                else:
                    setattr(execution, field, updates[field])
        
        execution.save()
    
    def _sync_step_response(self, execution: ChecklistExecution, response_data: Dict) -> Dict[str, Any]:
        """Sync a single step response"""
        synced_items = []
        conflicts = []
        
        step_id = response_data.get('step_id')
        if not step_id:
            return {'synced_items': synced_items, 'conflicts': conflicts}
        
        try:
            step = execution.template.steps.get(id=step_id)
            response, created = ChecklistStepResponse.objects.get_or_create(
                execution=execution,
                step=step,
                defaults={
                    'is_completed': False,
                    'text_response': '',
                    'notes': '',
                    'completed_at': None
                }
            )
            
            # Apply updates from client payload
            incoming_status = str(response_data.get('status', '')).upper()
            if incoming_status in {'COMPLETED', 'SKIPPED', 'FAILED'}:
                response.status = incoming_status
                response.is_completed = incoming_status in {'COMPLETED', 'SKIPPED'}

            if 'is_completed' in response_data:
                try:
                    response.is_completed = bool(response_data['is_completed'])
                except Exception:
                    pass

            if 'response' in response_data:
                val = str(response_data['response']).upper()
                response.text_response = val
                if val in {'YES', 'NO'}:
                    response.boolean_response = (val == 'YES')

            if 'text_response' in response_data:
                response.text_response = str(response_data['text_response'])

            if 'measurement_value' in response_data:
                response.measurement_value = response_data['measurement_value']

            if 'notes' in response_data:
                response.notes = response_data['notes']

            if 'signature_data' in response_data:
                response.signature_data = response_data['signature_data']

            ts = response_data.get('responded_at') or response_data.get('completed_at')
            if ts:
                try:
                    response.completed_at = datetime.fromisoformat(str(ts))
                    if not response.started_at:
                        response.started_at = response.completed_at
                except Exception:
                    pass

            for dt_field in ['started_at', 'completed_at']:
                if dt_field in response_data and response_data[dt_field]:
                    try:
                        setattr(response, dt_field, datetime.fromisoformat(str(response_data[dt_field])))
                    except Exception:
                        pass

            response.save()
            
            synced_items.append({
                'type': 'step_response',
                'id': response.id,
                'step_id': step_id,
                'action': 'created' if created else 'updated'
            })
            
        except ChecklistStep.DoesNotExist:
            conflicts.append({
                'type': 'step_not_found',
                'step_id': step_id,
                'message': 'Step not found in template'
            })
        
        return {'synced_items': synced_items, 'conflicts': conflicts}
    
    def _sync_evidence(self, execution: ChecklistExecution, evidence_data: Dict) -> Dict[str, Any]:
        """Sync evidence files"""
        synced_items = []
        conflicts = []
        
        # For now, we'll create evidence records
        # In a real implementation, you'd handle file uploads
        step_response_id = evidence_data.get('step_response_id')
        if step_response_id:
            try:
                step_response = execution.step_responses.get(id=step_response_id)
                
                evidence = ChecklistEvidence.objects.create(
                    step_response=step_response,
                    evidence_type=evidence_data.get('evidence_type', 'PHOTO'),
                    filename=evidence_data.get('filename', ''),
                    file_size=evidence_data.get('file_size', 0),
                    mime_type=evidence_data.get('mime_type', ''),
                    visibility=evidence_data.get('visibility', 'TEAM'),
                    file_path=evidence_data.get('file_path', ''),
                    metadata=evidence_data.get('metadata', {})
                )
                
                synced_items.append({
                    'type': 'evidence',
                    'id': evidence.id,
                    'action': 'created'
                })
                
            except ChecklistStepResponse.DoesNotExist:
                conflicts.append({
                    'type': 'step_response_not_found',
                    'step_response_id': step_response_id,
                    'message': 'Step response not found'
                })
        
        return {'synced_items': synced_items, 'conflicts': conflicts}
    
    def _sync_action(self, execution: ChecklistExecution, action_data: Dict) -> Dict[str, Any]:
        """Sync checklist actions"""
        synced_items = []
        conflicts = []
        
        action_id = action_data.get('id')
        
        if action_id:
            # Update existing action
            try:
                action = ChecklistAction.objects.get(id=action_id, execution=execution)
                
                # Apply updates
                allowed_fields = ['status', 'resolution_notes', 'resolved_at']
                for field in allowed_fields:
                    if field in action_data:
                        if field == 'resolved_at' and action_data[field]:
                            setattr(action, field, datetime.fromisoformat(action_data[field]))
                        else:
                            setattr(action, field, action_data[field])
                action.save()
                
                synced_items.append({
                    'type': 'action',
                    'id': action.id,
                    'action': 'updated'
                })
                
            except ChecklistAction.DoesNotExist:
                conflicts.append({
                    'type': 'action_not_found',
                    'action_id': action_id,
                    'message': 'Action not found'
                })
        else:
            # Create new action
            action = ChecklistAction.objects.create(
                execution=execution,
                title=action_data.get('title', ''),
                description=action_data.get('description', ''),
                priority=action_data.get('priority', 'MEDIUM'),
                status=action_data.get('status', 'OPEN'),
                due_date=datetime.fromisoformat(action_data['due_date']) if action_data.get('due_date') else None,
                created_by_id=action_data.get('created_by_id'),
                assigned_to_id=action_data.get('assigned_to_id')
            )
            
            synced_items.append({
                'type': 'action',
                'id': action.id,
                'action': 'created'
            })
        
        return {'synced_items': synced_items, 'conflicts': conflicts}


class ChecklistNotificationService:
    """Service for handling checklist-related notifications"""
    
    def send_assignment_notification(self, execution: ChecklistExecution):
        """Send notification when a checklist is assigned"""
        # In a real implementation, this would integrate with your notification system
        # For now, we'll just create a placeholder
        pass
    
    def send_overdue_notification(self, execution: ChecklistExecution):
        """Send notification when a checklist becomes overdue"""
        pass
    
    def send_completion_notification(self, execution: ChecklistExecution):
        """Send notification when a checklist is completed"""
        pass
    
    def send_action_notification(self, action: ChecklistAction):
        """Send notification when an action is created"""
        pass


class ChecklistReportingService:
    """Service for generating checklist reports and analytics"""
    
    def generate_completion_report(self, restaurant_id: int, start_date: datetime, end_date: datetime) -> Dict:
        """Generate completion rate report"""
        executions = ChecklistExecution.objects.filter(
            template__restaurant_id=restaurant_id,
            created_at__range=[start_date, end_date]
        )
        
        total_count = executions.count()
        completed_count = executions.filter(status='COMPLETED').count()
        
        completion_rate = (completed_count / total_count * 100) if total_count > 0 else 0
        
        return {
            'total_executions': total_count,
            'completed_executions': completed_count,
            'completion_rate': completion_rate,
            'period': {
                'start_date': start_date.isoformat(),
                'end_date': end_date.isoformat()
            }
        }
    
    def generate_template_usage_report(self, restaurant_id: int) -> List[Dict]:
        """Generate template usage statistics"""
        templates = ChecklistTemplate.objects.filter(restaurant_id=restaurant_id)
        
        usage_stats = []
        for template in templates:
            executions = template.executions.all()
            completed = executions.filter(status='COMPLETED')
            
            avg_completion_time = None
            if completed.exists():
                # Calculate average completion time
                completion_times = []
                for execution in completed:
                    if execution.started_at and execution.completed_at:
                        duration = execution.completed_at - execution.started_at
                        completion_times.append(duration.total_seconds())
                
                if completion_times:
                    avg_completion_time = sum(completion_times) / len(completion_times)
            
            usage_stats.append({
                'template_id': template.id,
                'template_name': template.name,
                'template_type': template.template_type,
                'total_executions': executions.count(),
                'completed_executions': completed.count(),
                'completion_rate': (completed.count() / executions.count() * 100) if executions.count() > 0 else 0,
                'average_completion_time_seconds': avg_completion_time
            })
        
        return usage_stats
