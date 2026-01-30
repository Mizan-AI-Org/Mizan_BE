"""
Serializers for Checklist Management System
"""
from rest_framework import serializers
from .models import (
    ChecklistTemplate, ChecklistStep, ChecklistExecution,
    ChecklistStepResponse, ChecklistEvidence, ChecklistAction
)
from accounts.serializers import CustomUserSerializer
from django.utils import timezone


class ChecklistStepSerializer(serializers.ModelSerializer):
    """Serializer for checklist steps"""
    
    class Meta:
        model = ChecklistStep
        fields = [
            'id', 'title', 'description', 'step_type', 'order',
            'is_required', 'requires_photo', 'requires_note', 'requires_signature',
            'measurement_type', 'measurement_unit', 'min_value', 'max_value', 'target_value',
            'conditional_logic', 'depends_on_step', 'validation_rules'
        ]


class ChecklistTemplateSerializer(serializers.ModelSerializer):
    """Serializer for checklist templates"""
    steps = ChecklistStepSerializer(many=True, read_only=True)
    created_by = CustomUserSerializer(read_only=True)
    
    class Meta:
        model = ChecklistTemplate
        fields = [
            'id', 'name', 'description', 'category', 'version',
            'is_active', 'estimated_duration', 'requires_supervisor_approval',
            'created_by', 'created_at', 'updated_at', 'steps'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChecklistTemplateCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating checklist templates with steps"""
    steps = ChecklistStepSerializer(many=True)
    
    class Meta:
        model = ChecklistTemplate
        fields = [
            'name', 'description', 'category', 'version',
            'estimated_duration', 'requires_supervisor_approval', 'steps'
        ]
    
    def create(self, validated_data):
        steps_data = validated_data.pop('steps')
        template = ChecklistTemplate.objects.create(**validated_data)
        
        for step_data in steps_data:
            ChecklistStep.objects.create(template=template, **step_data)
        
        return template


class ChecklistEvidenceSerializer(serializers.ModelSerializer):
    """Serializer for checklist evidence"""
    
    class Meta:
        model = ChecklistEvidence
        fields = [
            'id', 'evidence_type', 'filename', 'file_size', 'mime_type',
            'file_path', 'thumbnail_path', 'visibility', 'metadata',
            'captured_at', 'uploaded_at'
        ]
        read_only_fields = ['id', 'uploaded_at']


class ChecklistStepResponseSerializer(serializers.ModelSerializer):
    """Serializer for checklist step responses"""
    step = ChecklistStepSerializer(read_only=True)
    evidence = ChecklistEvidenceSerializer(many=True, read_only=True)
    
    class Meta:
        model = ChecklistStepResponse
        fields = [
            'id', 'step', 'is_completed', 'status',
            'text_response', 'measurement_value', 'boolean_response',
            'notes', 'signature_data', 'evidence',
            'started_at', 'completed_at', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at']


class ChecklistActionSerializer(serializers.ModelSerializer):
    """Serializer for checklist actions"""
    assigned_to = CustomUserSerializer(read_only=True)
    created_by = CustomUserSerializer(read_only=True)
    resolved_by = CustomUserSerializer(read_only=True)
    
    class Meta:
        model = ChecklistAction
        fields = [
            'id', 'title', 'description', 'priority', 'status',
            'assigned_to', 'due_date', 'resolved_at', 'resolved_by',
            'resolution_notes', 'created_by', 'created_at', 'updated_at'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'resolved_at', 'resolved_by']


class ChecklistExecutionSerializer(serializers.ModelSerializer):
    """Serializer for checklist executions"""
    template = ChecklistTemplateSerializer(read_only=True)
    assigned_to = CustomUserSerializer(read_only=True)
    assigned_shift_id = serializers.SerializerMethodField()
    assigned_shift_info = serializers.SerializerMethodField()
    current_step = ChecklistStepSerializer(read_only=True)
    step_responses = ChecklistStepResponseSerializer(many=True, read_only=True)
    actions = ChecklistActionSerializer(many=True, read_only=True)
    approved_by = CustomUserSerializer(read_only=True)
    compiled_summary = serializers.SerializerMethodField()
    
    class Meta:
        model = ChecklistExecution
        fields = [
            'id', 'template', 'assigned_to', 'status', 'started_at', 'completed_at',
            'due_date', 'current_step', 'progress_percentage', 'completion_notes',
            'supervisor_approved', 'approved_by', 'approved_at',
            'assigned_shift_id', 'assigned_shift_info',
            'compiled_summary', 'analysis_results',
            'step_responses', 'actions', 'created_at', 'updated_at',
            'last_synced_at', 'sync_version'
        ]
        read_only_fields = [
            'id', 'progress_percentage', 'created_at', 'updated_at',
            'last_synced_at', 'sync_version'
        ]

    def get_assigned_shift_id(self, obj):
        try:
            return str(obj.assigned_shift_id) if obj.assigned_shift_id else None
        except Exception:
            return None

    def get_assigned_shift_info(self, obj):
        shift = getattr(obj, 'assigned_shift', None)
        if not shift:
            return None
        try:
            start_dt = shift.start_time
            end_dt = shift.end_time
            try:
                if start_dt:
                    start_dt = timezone.localtime(start_dt)
                if end_dt:
                    end_dt = timezone.localtime(end_dt)
            except Exception:
                pass
            return {
                'id': str(shift.id),
                'shift_date': shift.shift_date.isoformat() if getattr(shift, 'shift_date', None) else None,
                'start_time': start_dt.isoformat() if start_dt else None,
                'end_time': end_dt.isoformat() if end_dt else None,
                'role': getattr(shift, 'role', None),
                'department': getattr(shift, 'department', None),
            }
        except Exception:
            return None

    def get_compiled_summary(self, obj):
        """
        Lightweight "manager summary" compiled from step responses/actions.
        """
        try:
            srs = list(getattr(obj, 'step_responses', []).all()) if hasattr(getattr(obj, 'step_responses', None), 'all') else (obj.step_responses or [])
        except Exception:
            srs = []
        try:
            acts = list(getattr(obj, 'actions', []).all()) if hasattr(getattr(obj, 'actions', None), 'all') else (obj.actions or [])
        except Exception:
            acts = []

        total_steps = len(srs)
        completed_steps = 0
        skipped_steps = 0
        failed_steps = 0
        required_missing = 0
        evidence_items = 0
        signature_items = 0
        notes_items = 0
        out_of_range_measurements = 0

        for sr in srs:
            try:
                if getattr(sr, 'is_completed', False) or str(getattr(sr, 'status', '')).upper() == 'COMPLETED':
                    completed_steps += 1
                st = str(getattr(sr, 'status', '')).upper()
                if st == 'SKIPPED':
                    skipped_steps += 1
                if st == 'FAILED':
                    failed_steps += 1

                step = getattr(sr, 'step', None)
                if step and getattr(step, 'is_required', False):
                    if not getattr(sr, 'is_completed', False) and st not in ('COMPLETED',):
                        required_missing += 1

                if getattr(sr, 'notes', None):
                    notes_items += 1
                if getattr(sr, 'signature_data', None):
                    signature_items += 1

                ev = getattr(sr, 'evidence', None)
                if ev is not None and hasattr(ev, 'count'):
                    evidence_items += int(ev.count())
                elif isinstance(ev, list):
                    evidence_items += len(ev)

                # Out-of-range measurement checks
                mv = getattr(sr, 'measurement_value', None)
                if mv is not None and step:
                    try:
                        min_v = getattr(step, 'min_value', None)
                        max_v = getattr(step, 'max_value', None)
                        if min_v is not None and mv < min_v:
                            out_of_range_measurements += 1
                        if max_v is not None and mv > max_v:
                            out_of_range_measurements += 1
                    except Exception:
                        pass
            except Exception:
                continue

        duration_minutes = None
        if getattr(obj, 'started_at', None) and getattr(obj, 'completed_at', None):
            try:
                duration_minutes = int((obj.completed_at - obj.started_at).total_seconds() / 60)
            except Exception:
                duration_minutes = None

        actions_open = 0
        actions_resolved = 0
        for a in acts:
            st = str(getattr(a, 'status', '')).upper()
            if st in ('OPEN', 'IN_PROGRESS'):
                actions_open += 1
            if st == 'RESOLVED':
                actions_resolved += 1

        completion_rate = int((completed_steps / total_steps) * 100) if total_steps else 0

        return {
            'total_steps': total_steps,
            'completed_steps': completed_steps,
            'skipped_steps': skipped_steps,
            'failed_steps': failed_steps,
            'required_missing': required_missing,
            'completion_rate': completion_rate,
            'duration_minutes': duration_minutes,
            'evidence_items': evidence_items,
            'notes_items': notes_items,
            'signature_items': signature_items,
            'out_of_range_measurements': out_of_range_measurements,
            'actions_open': actions_open,
            'actions_resolved': actions_resolved,
        }


class ChecklistSubmissionListSerializer(serializers.ModelSerializer):
    """
    Lightweight serializer for manager "Submitted Checklists" table.
    Avoids sending step_responses payload for every row.
    """
    template = serializers.SerializerMethodField()
    submitted_by = serializers.SerializerMethodField()
    submitted_at = serializers.SerializerMethodField()
    notes = serializers.CharField(source='completion_notes', allow_null=True, required=False)
    compiled_summary = serializers.SerializerMethodField()

    class Meta:
        model = ChecklistExecution
        fields = [
            'id',
            'template',
            'submitted_by',
            'submitted_at',
            'status',
            'supervisor_approved',
            'approved_at',
            'notes',
            'compiled_summary',
            'analysis_results',
        ]

    def get_template(self, obj):
        tpl = getattr(obj, 'template', None)
        if not tpl:
            return None
        return {
            'id': str(tpl.id),
            'name': getattr(tpl, 'name', None),
            'description': getattr(tpl, 'description', None),
            'category': getattr(tpl, 'category', None),
        }

    def get_submitted_by(self, obj):
        u = getattr(obj, 'assigned_to', None)
        if not u:
            return None
        name = None
        try:
            name = u.get_full_name()
        except Exception:
            name = f"{getattr(u, 'first_name', '')} {getattr(u, 'last_name', '')}".strip()
        return {'id': str(u.id), 'name': name or getattr(u, 'email', None)}

    def get_submitted_at(self, obj):
        dt = getattr(obj, 'completed_at', None) or getattr(obj, 'updated_at', None)
        if not dt:
            return None
        try:
            return timezone.localtime(dt).isoformat()
        except Exception:
            return dt.isoformat()

    def get_compiled_summary(self, obj):
        # Reuse the compiled summary from the full serializer
        return ChecklistExecutionSerializer(context=self.context).get_compiled_summary(obj)


class ChecklistExecutionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating checklist executions"""
    template_id = serializers.UUIDField()
    assigned_shift = serializers.UUIDField(required=False)
    
    class Meta:
        model = ChecklistExecution
        fields = ['template_id', 'due_date', 'completion_notes', 'assigned_shift']
    
    def create(self, validated_data):
        template_id = validated_data.pop('template_id')
        assigned_shift_id = validated_data.pop('assigned_shift', None)
        template = ChecklistTemplate.objects.get(id=template_id)

        assigned_shift_obj = None
        if assigned_shift_id:
            try:
                from scheduling.models import AssignedShift
                assigned_shift_obj = AssignedShift.objects.get(id=assigned_shift_id)
            except Exception:
                assigned_shift_obj = None

        execution = ChecklistExecution.objects.create(
            template=template,
            assigned_to=self.context['request'].user,
            assigned_shift=assigned_shift_obj,
            **validated_data
        )

        for step in template.steps.all():
            ChecklistStepResponse.objects.create(
                execution=execution,
                step=step
            )

        return execution


class ChecklistExecutionUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating checklist execution progress"""
    
    class Meta:
        model = ChecklistExecution
        fields = ['status', 'completion_notes', 'current_step']
    
    def update(self, instance, validated_data):
        if 'status' in validated_data:
            new_status = validated_data['status']
            if new_status == 'IN_PROGRESS' and instance.status == 'NOT_STARTED':
                instance.start_execution()
            elif new_status == 'COMPLETED' and instance.status == 'IN_PROGRESS':
                instance.complete_execution(validated_data.get('completion_notes'))
        
        return super().update(instance, validated_data)


class ChecklistStepResponseUpdateSerializer(serializers.ModelSerializer):
    """Serializer for updating step responses"""
    
    class Meta:
        model = ChecklistStepResponse
        fields = [
            'is_completed', 'status', 'text_response', 'measurement_value',
            'boolean_response', 'notes', 'signature_data'
        ]
    
    def update(self, instance, validated_data):
        if validated_data.get('is_completed') and not instance.is_completed:
            instance.completed_at = timezone.now()
            if not instance.started_at:
                instance.started_at = timezone.now()
        
        updated_instance = super().update(instance, validated_data)
        
        # Update execution progress
        updated_instance.execution.calculate_progress()
        
        return updated_instance


class ChecklistSyncSerializer(serializers.Serializer):
    """Serializer for offline sync operations"""
    execution_id = serializers.UUIDField()
    step_responses = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )
    evidence_items = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )
    # Accept 'evidence' alias used by some clients
    evidence = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )
    actions = serializers.ListField(
        child=serializers.DictField(),
        required=False
    )
    last_sync_version = serializers.IntegerField(required=False)
    
    def validate_execution_id(self, value):
        """Validate that execution exists and user has access"""
        try:
            execution = ChecklistExecution.objects.get(id=value)
            if execution.assigned_to != self.context['request'].user:
                raise serializers.ValidationError("Access denied to this checklist execution")
            return value
        except ChecklistExecution.DoesNotExist:
            raise serializers.ValidationError("Checklist execution not found")