from rest_framework import serializers
from .models import (
    ScheduleTemplate, TemplateShift, AssignedShift, WeeklySchedule, 
    ShiftSwapRequest, TaskCategory, ShiftTask, Timesheet, TimesheetEntry,
    TemplateVersion
)
from .task_templates import TaskTemplate, Task
from .audit import AuditLog
from django.utils import timezone
from datetime import datetime
from django.db.models import Q
import sys
from core.i18n import get_effective_language, normalize_language


class TemplateShiftSerializer(serializers.ModelSerializer):
    class Meta:
        model = TemplateShift
        fields = '__all__'

class ScheduleTemplateSerializer(serializers.ModelSerializer):
    shifts = TemplateShiftSerializer(many=True, read_only=True)

    class Meta:
        model = ScheduleTemplate
        fields = '__all__'


class TemplateVersionSerializer(serializers.ModelSerializer):
    template_name = serializers.CharField(source='template.name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    
    class Meta:
        model = TemplateVersion
        fields = [
            'id', 'template', 'template_name', 'version_number', 'status',
            'description', 'changes_summary', 'created_by', 'created_by_name',
            'created_at', 'activated_at', 'archived_at', 'template_data', 'shifts_data'
        ]
        read_only_fields = ['id', 'created_at', 'activated_at', 'archived_at']


class ScheduleTemplateDetailSerializer(ScheduleTemplateSerializer):
    """Detailed serializer with version information"""
    versions = TemplateVersionSerializer(many=True, read_only=True)
    
    class Meta(ScheduleTemplateSerializer.Meta):
        fields = '__all__'

class TaskCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = TaskCategory
        fields = ['id', 'name', 'description', 'color', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def validate_name(self, value):
        """Validate category name length"""
        if not value or len(value) > 100:
            raise serializers.ValidationError("Category name must be between 1 and 100 characters.")
        return value
    
    def validate_description(self, value):
        """Validate description length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Description cannot exceed 500 characters.")
        return value
    
    def validate_color(self, value):
        """Validate color is a valid hex code"""
        import re
        if value and not re.match(r'^#[0-9A-Fa-f]{6}$', value):
            raise serializers.ValidationError("Color must be a valid hex code (e.g., #FF0000).")
        return value

class ShiftTaskSerializer(serializers.ModelSerializer):
    category_details = TaskCategorySerializer(source='category', read_only=True)
    assigned_to_name = serializers.CharField(source='assigned_to.get_full_name', read_only=True)
    created_by_name = serializers.CharField(source='created_by.get_full_name', read_only=True)
    progress_percentage = serializers.SerializerMethodField()
    subtasks = serializers.SerializerMethodField()
    shift = serializers.PrimaryKeyRelatedField(queryset=AssignedShift.objects.all(), required=False)
    
    class Meta:
        model = ShiftTask
        fields = [
            'id', 'shift', 'category', 'category_details', 'title', 'description',
            'priority', 'status', 'assigned_to', 'assigned_to_name', 'estimated_duration',
            'parent_task', 'notes', 'created_by', 'created_by_name', 'created_at',
            'updated_at', 'completed_at', 'progress_percentage', 'subtasks'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'completed_at']
    
    def validate_title(self, value):
        """Validate task title length"""
        if not value or len(value) > 200:
            raise serializers.ValidationError("Task title must be between 1 and 200 characters.")
        return value
    
    def validate_description(self, value):
        """Validate description length"""
        if value and len(value) > 1000:
            raise serializers.ValidationError("Description cannot exceed 1000 characters.")
        return value
    
    def validate_estimated_duration(self, value):
        """Validate estimated duration is positive"""
        if value is not None and value <= 0:
            raise serializers.ValidationError("Estimated duration must be greater than zero.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Notes cannot exceed 500 characters.")
        return value
    
    def get_progress_percentage(self, obj):
        return obj.get_progress_percentage()
    
    def get_subtasks(self, obj):
        if obj.parent_task is None:
            subtasks = obj.subtasks.all()
            return ShiftTaskSerializer(subtasks, many=True, read_only=True).data
        return []

class TaskTemplateSerializer(serializers.ModelSerializer):
    localized_name = serializers.SerializerMethodField()
    localized_description = serializers.SerializerMethodField()
    localized_tasks = serializers.SerializerMethodField()

    class Meta:
        model = TaskTemplate
        fields = '__all__'
        # Ensure server sets these so clients don't need to send them
        read_only_fields = ['id', 'restaurant', 'created_by', 'created_at', 'updated_at']

    def _lang(self) -> str:
        request = self.context.get("request")
        user = getattr(request, "user", None) if request else None
        restaurant = getattr(user, "restaurant", None) if user and getattr(user, "is_authenticated", False) else None
        return get_effective_language(user=user if user and user.is_authenticated else None, restaurant=restaurant)

    def _i18n_block(self, obj: TaskTemplate, lang: str) -> dict:
        try:
            raw = getattr(obj, "i18n", None) or {}
            if isinstance(raw, dict):
                return raw.get(lang) if isinstance(raw.get(lang), dict) else {}
        except Exception:
            pass
        return {}

    def get_localized_name(self, obj: TaskTemplate):
        lang = normalize_language(self._lang())
        block = self._i18n_block(obj, lang)
        return block.get("name") or obj.name

    def get_localized_description(self, obj: TaskTemplate):
        lang = normalize_language(self._lang())
        block = self._i18n_block(obj, lang)
        return block.get("description") or obj.description

    def get_localized_tasks(self, obj: TaskTemplate):
        lang = normalize_language(self._lang())
        block = self._i18n_block(obj, lang)
        tasks = block.get("tasks")
        return tasks if isinstance(tasks, list) else obj.tasks

    def validate_frequency(self, value):
        """Normalize frequency to match backend choices (uppercase keys).

        Accepts common variants like 'daily', 'Daily', etc., and maps them
        to the canonical choice values: 'DAILY', 'WEEKLY', 'MONTHLY', 'QUARTERLY',
        'ANNUALLY', 'CUSTOM'.
        """
        if not value:
            return 'DAILY'
        normalized = str(value).strip().upper()
        mapping = {
            'DAILY': 'DAILY',
            'WEEKLY': 'WEEKLY',
            'MONTHLY': 'MONTHLY',
            'QUARTERLY': 'QUARTERLY',
            'ANNUALLY': 'ANNUALLY',
            'YEARLY': 'ANNUALLY',  # common alias
            'CUSTOM': 'CUSTOM',
        }
        # Also accept display-form values
        display_mapping = {
            'DAILY': 'DAILY',
            'WEEKLY': 'WEEKLY',
            'MONTHLY': 'MONTHLY',
            'QUARTERLY': 'QUARTERLY',
            'ANNUALLY': 'ANNUALLY',
            'CUSTOM': 'CUSTOM',
        }
        # If normalized is already a key, return mapped
        if normalized in mapping:
            return mapping[normalized]
        # If someone passed the display value capitalized, accept it
        if normalized in display_mapping:
            return display_mapping[normalized]
        raise serializers.ValidationError("Invalid frequency. Must be one of DAILY, WEEKLY, MONTHLY, QUARTERLY, ANNUALLY, CUSTOM.")

    def create(self, validated_data):
        # Inject restaurant and created_by from request context
        request = self.context.get('request')
        if request and hasattr(request, 'user'):
            restaurant = getattr(request.user, 'restaurant', None)
            if restaurant is None:
                # Return a clear 400 instead of 500 when user has no restaurant
                raise serializers.ValidationError({'restaurant': 'User has no associated restaurant.'})
            validated_data['restaurant'] = restaurant
            validated_data['created_by'] = request.user
        return super().create(validated_data)

class TaskSerializer(serializers.ModelSerializer):
    assigned_to_details = serializers.SerializerMethodField()
    category_details = TaskCategorySerializer(source='category', read_only=True)
    subtasks_count = serializers.SerializerMethodField()
    
    class Meta:
        model = Task
        fields = '__all__'
        
    def get_subtasks_count(self, obj):
        return obj.subtasks.count()
        
    def get_assigned_to_details(self, obj):
        from accounts.serializers import UserSerializer
        return UserSerializer(obj.assigned_to.all(), many=True).data

class LenientManyRelatedField(serializers.ManyRelatedField):
    """Custom ManyRelatedField that ignores non-existent PKs instead of erroring"""
    def to_internal_value(self, data):
        if not data:
            return []
        from .task_templates import TaskTemplate
        # Filter to only existing objects
        valid_ids = [pk for pk in data if TaskTemplate.objects.filter(id=pk).exists()]
        return list(TaskTemplate.objects.filter(id__in=valid_ids))


class LenientPKRelatedField(serializers.PrimaryKeyRelatedField):
    """PrimaryKeyRelatedField that uses LenientManyRelatedField for many=True"""
    @classmethod
    def many_init(cls, *args, **kwargs):
        list_kwargs = {'child_relation': cls(*args, **kwargs)}
        for key in kwargs:
            if key in ('read_only', 'write_only', 'required', 'default', 'source', 'allow_empty', 'allow_null'):
                list_kwargs[key] = kwargs[key]
        return LenientManyRelatedField(**list_kwargs)


class AssignedShiftSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.__str__', read_only=True)
    staff_members_details = serializers.SerializerMethodField(read_only=True)
    tasks = ShiftTaskSerializer(many=True, required=False)
    task_templates_details = TaskTemplateSerializer(source='task_templates', many=True, read_only=True)
    # Use lenient field that ignores non-existent IDs
    task_templates = LenientPKRelatedField(
        many=True,
        queryset=TaskTemplate.objects.all(),
        required=False
    )
    # Explicit time fields to handle multiple input formats
    start_time = serializers.DateTimeField(
        input_formats=['iso-8601', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%H:%M:%S', '%H:%M'],
        required=False,
        allow_null=True
    )
    end_time = serializers.DateTimeField(
        input_formats=['iso-8601', '%Y-%m-%dT%H:%M:%S%z', '%Y-%m-%dT%H:%M:%S', '%H:%M:%S', '%H:%M'],
        required=False,
        allow_null=True
    )
    # Override role field to accept any case - we'll normalize in validate_role
    role = serializers.CharField(required=False, allow_blank=True)

    class Meta:
        model = AssignedShift
        fields = [
            'id', 'schedule', 'staff', 'staff_members', 'staff_name', 'staff_members_details',
            'shift_date', 'start_time', 'end_time', 'break_duration', 'role', 'notes', 'color',
            'created_at', 'updated_at', 'tasks', 'task_templates', 'task_templates_details',
            'is_recurring', 'recurrence_group_id'
        ]
        read_only_fields = ['id', 'created_at', 'updated_at', 'schedule']
        extra_kwargs = {
            'role': {'required': False},
            'staff': {'required': False},
        }

    def get_staff_members_details(self, obj):
        return [{"id": s.id, "first_name": s.first_name, "last_name": s.last_name} for s in obj.staff_members.all()]

    def validate(self, attrs):
        request = self.context.get('request')
        staff = attrs.get('staff')
        staff_members = attrs.get('staff_members', [])
        schedule = attrs.get('schedule')
        shift_date = attrs.get('shift_date')
        start = attrs.get('start_time')
        end = attrs.get('end_time')

        # If it's an update, get existing values from instance for validation
        if self.instance:
            shift_date = shift_date or self.instance.shift_date
            start = start or self.instance.start_time
            end = end or self.instance.end_time
            if schedule is None:
                schedule = self.instance.schedule

        # Basic time range validation
        if start and end and end <= start:
            raise serializers.ValidationError("Shift end time must be after start time.")

        # Resolve schedule if not provided but in initial_data
        if schedule is None and isinstance(getattr(self, 'initial_data', {}), dict):
            sched_id = self.initial_data.get('schedule')
            if sched_id:
                from .models import WeeklySchedule
                try:
                    schedule = WeeklySchedule.objects.get(id=sched_id)
                except WeeklySchedule.DoesNotExist:
                    raise serializers.ValidationError({'schedule': 'Schedule not found'})

        if request and hasattr(request, 'user') and getattr(request.user, 'restaurant', None):
            user_restaurant = request.user.restaurant
            if schedule and getattr(schedule, 'restaurant', None) and schedule.restaurant != user_restaurant:
                raise serializers.ValidationError({'restaurant': 'Cross-tenant schedule access denied'})
            
            # Combine staff and staff_members for validation
            all_staff = list(staff_members)
            if staff and staff not in all_staff:
                all_staff.append(staff)
                
            for s in all_staff:
                if s and getattr(s, 'restaurant', None) and s.restaurant != user_restaurant:
                    raise serializers.ValidationError({'staff': f'Staff {s} belongs to a different restaurant'})
                if schedule and s and getattr(schedule, 'restaurant', None) and getattr(s, 'restaurant', None):
                    if schedule.restaurant != s.restaurant:
                        raise serializers.ValidationError({'staff': f'Staff {s} must belong to the same restaurant as the schedule'})

            # Default role from first valid staff member if role not provided
            if not attrs.get('role') and all_staff and getattr(all_staff[0], 'role', None):
                attrs['role'] = all_staff[0].role

            # ENHANCED OVERLAP AND DUPLICATION CHECK
            if shift_date and start and end:
                for staff_member in all_staff:
                    overlaps = AssignedShift.objects.filter(
                        Q(staff=staff_member) | Q(staff_members=staff_member),
                        shift_date=shift_date,
                        status__in=['SCHEDULED', 'CONFIRMED', 'IN_PROGRESS', 'COMPLETED']
                    ).distinct()
                    
                    if self.instance:
                        overlaps = overlaps.exclude(id=self.instance.id)
                    
                    for existing in overlaps:
                        e_start = existing.start_time
                        e_end = existing.end_time
                        
                        # Check time overlap
                        if start < e_end and end > e_start:
                             raise serializers.ValidationError(
                                 f"Staff {staff_member} already has a shift on {shift_date} from {e_start.strftime('%H:%M')} to {e_end.strftime('%H:%M')}"
                             )
                
        return attrs

    def validate_role(self, value):
        from django.conf import settings
        if not value:
            return value
        norm = str(value).strip().upper().replace('-', '_')
        # Check against allowed roles in settings
        allowed = set([c[0] for c in getattr(settings, 'STAFF_ROLES_CHOICES', [])])
        if norm in allowed:
            return norm
        # If it's not in the strict list, still return normalized uppercase
        return norm

    def get_staff_name(self, obj):
        return str(obj.staff)

    def create(self, validated_data):
        tasks_data = validated_data.pop('tasks', [])
        task_templates = validated_data.pop('task_templates', [])
        staff_members = validated_data.pop('staff_members', [])
        
        # Backward compatibility: set staff to first member if not provided
        if staff_members and not validated_data.get('staff'):
            validated_data['staff'] = staff_members[0]
            
        shift = AssignedShift.objects.create(**validated_data)
        
        if staff_members:
            shift.staff_members.set(staff_members)
            
        if task_templates:
            shift.task_templates.set(task_templates)
            
        request = self.context.get('request')
        for task_data in tasks_data:
            try:
                ShiftTask.objects.create(
                    shift=shift,
                    created_by=request.user if request and hasattr(request, 'user') else None,
                    **task_data
                )
            except Exception as e:
                # Log error and continue? Or fail? Better to log for now during diagnosis
                # logger.error(f"Error creating task for shift: {e}")
                pass

                
        return shift

    def update(self, instance, validated_data):
        tasks_data = validated_data.pop('tasks', None)
        task_templates = validated_data.pop('task_templates', None)
        staff_members = validated_data.pop('staff_members', None)
        
        instance = super().update(instance, validated_data)
        
        if staff_members is not None:
            instance.staff_members.set(staff_members)
            # Maintain legacy field
            if staff_members and instance.staff != staff_members[0]:
                instance.staff = staff_members[0]
                instance.save()
        
        if task_templates is not None:
            instance.task_templates.set(task_templates)
            
        if tasks_data is not None:
            request = self.context.get('request')
            existing_tasks = {str(t.id): t for t in instance.tasks.all()}
            new_task_ids = []
            
            for task_data in tasks_data:
                task_id = task_data.get('id')
                # If task_id is present and exists, update it
                if task_id and str(task_id) in existing_tasks:
                    task = existing_tasks[str(task_id)]
                    for attr, value in task_data.items():
                        if attr != 'id' and attr != 'shift':
                            setattr(task, attr, value)
                    task.save()
                    new_task_ids.append(str(task.id))
                else:
                    # Create new task
                    try:
                        new_task = ShiftTask.objects.create(
                            shift=instance,
                            created_by=request.user if request and hasattr(request, 'user') else None,
                            **task_data
                        )
                        new_task_ids.append(str(new_task.id))
                    except Exception as e:
                        # logger.error(f"Error creating task in update: {e}")
                        pass

            
            # Delete tasks that weren't in the update list
            instance.tasks.exclude(id__in=new_task_ids).delete()
            
        return instance


# Unified view item for both ShiftTask and Template Task
class CombinedTaskItemSerializer(serializers.Serializer):
    id = serializers.UUIDField()
    title = serializers.CharField()
    description = serializers.CharField(allow_blank=True, allow_null=True, required=False)
    priority = serializers.CharField()
    status = serializers.CharField()
    due_date = serializers.DateField(allow_null=True, required=False)
    due_time = serializers.TimeField(allow_null=True, required=False)
    source = serializers.ChoiceField(choices=["SHIFT_TASK", "TEMPLATE_TASK"])
    # Minimal association details to avoid heavy nested serialization
    associated_shift = serializers.DictField(child=serializers.CharField(), required=False, allow_null=True)
    associated_template = serializers.DictField(child=serializers.CharField(), required=False, allow_null=True)
    category = serializers.DictField(child=serializers.CharField(), required=False, allow_null=True)
    created_at = serializers.DateTimeField(required=False)
    updated_at = serializers.DateTimeField(required=False)
    # Assigned to is normalized to a list of user id strings
    assigned_to = serializers.ListField(child=serializers.CharField(), required=False)
    
    def validate_break_duration(self, value):
        """Validate break duration is non-negative"""
        if value is not None and value < 0:
            raise serializers.ValidationError("Break duration cannot be negative.")
        return value
    
    def validate_role(self, value):
        """Validate role length"""
        if value and len(value) > 100:
            raise serializers.ValidationError("Role cannot exceed 100 characters.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Notes cannot exceed 500 characters.")
        return value
    
    def validate_color(self, value):
        """Validate color is a valid hex code"""
        import re
        if value and not re.match(r'^#[0-9A-Fa-f]{6}$', value):
            raise serializers.ValidationError("Color must be a valid hex code (e.g., #FF0000).")
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        if 'start_time' in data and 'end_time' in data:
            if data['start_time'] >= data['end_time']:
                raise serializers.ValidationError("end_time must be after start_time.")
        return data

class WeeklyScheduleSerializer(serializers.ModelSerializer):
    assigned_shifts = serializers.SerializerMethodField()
    
    class Meta:
        model = WeeklySchedule
        fields = '__all__'
        # Restaurant is injected server-side in views.perform_create; clients shouldn't send it
        read_only_fields = ['restaurant']

    def validate(self, data):
        # Optional: ensure week_start is a Monday
        week_start = data.get('week_start')
        if week_start is not None and hasattr(week_start, 'weekday') and week_start.weekday() != 0:
            raise serializers.ValidationError({
                'week_start': 'week_start must be a Monday (weekday=0).'
            })

        # Friendly pre-check for uniqueness to avoid DB 500s
        request = self.context.get('request')
        if request and hasattr(request, 'user') and getattr(request.user, 'restaurant', None) and week_start is not None:
            from .models import WeeklySchedule
            exists = WeeklySchedule.objects.filter(
                restaurant=request.user.restaurant,
                week_start=week_start
            ).exists()
            if exists:
                raise serializers.ValidationError({
                    'week_start': 'A weekly schedule for this week already exists.'
                })
        return data

    def get_assigned_shifts(self, obj):
        try:
            qs = obj.assigned_shifts.all()
            return AssignedShiftSerializer(qs, many=True).data
        except Exception:
            return []

class ShiftSwapRequestSerializer(serializers.ModelSerializer):
    shift_to_swap_details = AssignedShiftSerializer(source='shift_to_swap', read_only=True)
    requester_details = serializers.CharField(source='requester.__str__', read_only=True)
    receiver_details = serializers.CharField(source='receiver.__str__', read_only=True)

    class Meta:
        model = ShiftSwapRequest
        fields = '__all__'


# Enhanced serializers for AI scheduling
class AIScheduleRequestSerializer(serializers.Serializer):
    """Serializer for AI schedule generation requests"""
    week_start = serializers.DateField()
    template_id = serializers.UUIDField(required=True)
    labor_budget = serializers.DecimalField(max_digits=10, decimal_places=2, required=False)
    demand_level = serializers.ChoiceField(choices=['LOW', 'MEDIUM', 'HIGH'], default='MEDIUM')
    demand_override = serializers.DictField(
        child=serializers.ChoiceField(choices=['LOW', 'MEDIUM', 'HIGH']),
        required=False
    )
    
    def validate_week_start(self, value):
        """Ensure week_start is a Monday"""
        if value.weekday() != 0:
            raise serializers.ValidationError("week_start must be a Monday")
        return value


# Timesheet Serializers
class TimesheetEntrySerializer(serializers.ModelSerializer):
    shift_details = AssignedShiftSerializer(source='shift', read_only=True)
    
    class Meta:
        model = TimesheetEntry
        fields = ['id', 'shift', 'shift_details', 'hours_worked', 'notes', 'created_at']
        read_only_fields = ['id', 'created_at']
    
    def validate_hours_worked(self, value):
        """Validate hours worked is positive"""
        if value <= 0:
            raise serializers.ValidationError("Hours worked must be greater than zero.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 500:
            raise serializers.ValidationError("Notes cannot exceed 500 characters.")
        return value


class TimesheetSerializer(serializers.ModelSerializer):
    staff_name = serializers.CharField(source='staff.get_full_name', read_only=True)
    approved_by_name = serializers.CharField(source='approved_by.get_full_name', read_only=True, allow_null=True)
    entries = TimesheetEntrySerializer(many=True, read_only=True)
    
    class Meta:
        model = Timesheet
        fields = [
            'id', 'staff', 'staff_name', 'restaurant', 'start_date', 'end_date',
            'total_hours', 'total_earnings', 'hourly_rate', 'status', 'notes',
            'submitted_at', 'approved_at', 'approved_by', 'approved_by_name',
            'paid_at', 'created_at', 'updated_at', 'entries'
        ]
        read_only_fields = [
            'id', 'total_hours', 'total_earnings', 'submitted_at',
            'approved_at', 'approved_by', 'paid_at', 'created_at', 'updated_at'
        ]
    
    def validate_hourly_rate(self, value):
        """Validate hourly rate is positive"""
        if value <= 0:
            raise serializers.ValidationError("Hourly rate must be greater than zero.")
        return value
    
    def validate_notes(self, value):
        """Validate notes length"""
        if value and len(value) > 1000:
            raise serializers.ValidationError("Notes cannot exceed 1000 characters.")
        return value
    
    def validate(self, data):
        """Cross-field validation"""
        if data.get('start_date') and data.get('end_date'):
            if data['start_date'] >= data['end_date']:
                raise serializers.ValidationError("End date must be after start date.")
        
        return data

class AuditLogSerializer(serializers.ModelSerializer):
    """Serializer for audit log entries"""
    user_name = serializers.CharField(source='user.get_full_name', read_only=True)
    user_email = serializers.CharField(source='user.email', read_only=True)
    content_type_name = serializers.CharField(source='content_type.model', read_only=True)
    object_str = serializers.SerializerMethodField()
    
    class Meta:
        model = AuditLog
        fields = [
            'id', 'timestamp', 'user', 'user_name', 'user_email', 'action', 'severity',
            'description', 'content_type', 'content_type_name', 'object_id', 'object_str',
            'old_values', 'new_values', 'metadata', 'ip_address', 'user_agent'
        ]
        read_only_fields = ['id', 'timestamp']
    
    def get_object_str(self, obj):
        """Get string representation of the audited object"""
        if obj.content_object:
            return str(obj.content_object)
        return None
