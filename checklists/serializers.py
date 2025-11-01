"""
Serializers for Checklist Management System
"""
from rest_framework import serializers
from .models import (
    ChecklistTemplate, ChecklistStep, ChecklistExecution,
    ChecklistStepResponse, ChecklistEvidence, ChecklistAction
)
from accounts.serializers import CustomUserSerializer


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
            'id', 'name', 'description', 'template_type', 'version',
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
            'name', 'description', 'template_type', 'version',
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
    current_step = ChecklistStepSerializer(read_only=True)
    step_responses = ChecklistStepResponseSerializer(many=True, read_only=True)
    actions = ChecklistActionSerializer(many=True, read_only=True)
    approved_by = CustomUserSerializer(read_only=True)
    
    class Meta:
        model = ChecklistExecution
        fields = [
            'id', 'template', 'assigned_to', 'status', 'started_at', 'completed_at',
            'due_date', 'current_step', 'progress_percentage', 'completion_notes',
            'supervisor_approved', 'approved_by', 'approved_at',
            'step_responses', 'actions', 'created_at', 'updated_at',
            'last_synced_at', 'sync_version'
        ]
        read_only_fields = [
            'id', 'progress_percentage', 'created_at', 'updated_at',
            'last_synced_at', 'sync_version'
        ]


class ChecklistExecutionCreateSerializer(serializers.ModelSerializer):
    """Serializer for creating checklist executions"""
    template_id = serializers.UUIDField()
    
    class Meta:
        model = ChecklistExecution
        fields = ['template_id', 'due_date', 'completion_notes']
    
    def create(self, validated_data):
        template_id = validated_data.pop('template_id')
        template = ChecklistTemplate.objects.get(id=template_id)
        
        execution = ChecklistExecution.objects.create(
            template=template,
            assigned_to=self.context['request'].user,
            **validated_data
        )
        
        # Create step responses for all template steps
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