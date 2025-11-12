from rest_framework import serializers
from .process_models import Process, ProcessTask


class ProcessTaskSerializer(serializers.ModelSerializer):
    assigned_to_name = serializers.SerializerMethodField(read_only=True)

    class Meta:
        model = ProcessTask
        fields = [
            'id', 'process', 'category', 'title', 'description', 'priority', 'status',
            'assigned_to', 'assigned_to_name', 'due_date', 'due_time', 'estimated_duration',
            'verification_type', 'verification_required', 'parent_task', 'progress',
            'started_at', 'completed_at', 'completion_notes', 'created_by', 'updated_by',
            'created_at', 'updated_at'
        ]
        read_only_fields = ['created_by', 'updated_by', 'created_at', 'updated_at']

    def get_assigned_to_name(self, obj):
        user = obj.assigned_to
        if not user:
            return None
        name = f"{getattr(user, 'first_name', '')} {getattr(user, 'last_name', '')}".strip()
        return name or getattr(user, 'username', None)

    def create(self, validated_data):
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data['created_by'] = request.user
            validated_data['updated_by'] = request.user
        return super().create(validated_data)

    def update(self, instance, validated_data):
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data['updated_by'] = request.user
        return super().update(instance, validated_data)


class ProcessSerializer(serializers.ModelSerializer):
    tasks = ProcessTaskSerializer(many=True, required=False)

    class Meta:
        model = Process
        fields = [
            'id', 'restaurant', 'name', 'description', 'status', 'priority', 'is_active',
            'sop_document', 'sop_steps', 'is_critical',
            'created_by', 'updated_by', 'created_at', 'updated_at', 'tasks'
        ]
        read_only_fields = ['created_by', 'updated_by', 'created_at', 'updated_at']

    def create(self, validated_data):
        tasks_data = validated_data.pop('tasks', [])
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data['created_by'] = request.user
            validated_data['updated_by'] = request.user
        process = Process.objects.create(**validated_data)
        for task_data in tasks_data:
            ProcessTask.objects.create(process=process, **task_data)
        return process

    def update(self, instance, validated_data):
        tasks_data = validated_data.pop('tasks', None)
        request = self.context.get('request')
        if request and request.user and request.user.is_authenticated:
            validated_data['updated_by'] = request.user
        instance = super().update(instance, validated_data)
        if tasks_data is not None:
            # Simple replacement strategy; can be extended to upsert later
            instance.tasks.all().delete()
            for task_data in tasks_data:
                ProcessTask.objects.create(process=instance, **task_data)
        return instance