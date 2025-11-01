"""
Django Admin configuration for Checklist models
"""
from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from .models import (
    ChecklistTemplate, ChecklistStep, ChecklistExecution,
    ChecklistStepResponse, ChecklistEvidence, ChecklistAction
)


class ChecklistStepInline(admin.TabularInline):
    """Inline admin for checklist steps"""
    model = ChecklistStep
    extra = 0
    fields = (
        'order', 'title', 'step_type', 'is_required',
        'requires_photo', 'requires_note', 'requires_signature'
    )
    ordering = ['order']


@admin.register(ChecklistTemplate)
class ChecklistTemplateAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistTemplate"""
    list_display = (
        'name', 'category', 'restaurant', 'is_active',
        'step_count', 'estimated_duration', 'created_at'
    )
    list_filter = ('category', 'is_active', 'created_at', 'restaurant')
    search_fields = ('name', 'description')
    readonly_fields = ('id', 'created_at', 'updated_at')
    inlines = [ChecklistStepInline]
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('name', 'description', 'category', 'restaurant')
        }),
        ('Configuration', {
            'fields': ('estimated_duration', 'requires_supervisor_approval', 'is_active')
        }),
        ('Integration', {
            'fields': ('task_template',),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'version', 'created_by', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def step_count(self, obj):
        """Display number of steps in template"""
        return obj.steps.count()
    step_count.short_description = 'Steps'
    
    def save_model(self, request, obj, form, change):
        """Set created_by when creating new template"""
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ChecklistStep)
class ChecklistStepAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistStep"""
    list_display = (
        'title', 'template', 'step_type', 'order',
        'is_required', 'requires_photo', 'requires_signature'
    )
    list_filter = ('step_type', 'is_required', 'requires_photo', 'requires_signature', 'template__category')
    search_fields = ('title', 'description', 'template__name')
    ordering = ['template', 'order']
    
    fieldsets = (
        ('Basic Information', {
            'fields': ('template', 'title', 'description', 'step_type', 'order')
        }),
        ('Requirements', {
            'fields': ('is_required', 'requires_photo', 'requires_note', 'requires_signature')
        }),
        ('Measurements', {
            'fields': ('measurement_type', 'measurement_unit', 'min_value', 'max_value', 'target_value'),
            'classes': ('collapse',)
        }),
        ('Advanced', {
            'fields': ('conditional_logic', 'validation_rules'),
            'classes': ('collapse',)
        })
    )


class ChecklistStepResponseInline(admin.TabularInline):
    """Inline admin for step responses"""
    model = ChecklistStepResponse
    extra = 0
    readonly_fields = ('step', 'is_completed', 'completed_at')
    fields = ('step', 'is_completed', 'status', 'completed_at')


@admin.register(ChecklistExecution)
class ChecklistExecutionAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistExecution"""
    list_display = (
        'template', 'assigned_to', 'status', 'progress_percentage',
        'due_date', 'started_at', 'completed_at'
    )
    list_filter = (
        'status', 'template__category', 'created_at',
        'due_date', 'template__restaurant'
    )
    search_fields = ('template__name', 'assigned_to__email', 'completion_notes')
    readonly_fields = ('id', 'progress_percentage', 'created_at', 'last_synced_at', 'sync_version')
    inlines = [ChecklistStepResponseInline]
    
    fieldsets = (
        ('Assignment', {
            'fields': ('template', 'assigned_to', 'assigned_shift', 'task', 'due_date')
        }),
        ('Progress', {
            'fields': ('status', 'progress_percentage', 'current_step')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at')
        }),
        ('Approval', {
            'fields': ('supervisor_approved', 'approved_by', 'approved_at'),
            'classes': ('collapse',)
        }),
        ('Notes', {
            'fields': ('completion_notes',)
        }),
        ('Sync Information', {
            'fields': ('sync_version', 'last_synced_at'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )
    
    def get_queryset(self, request):
        """Filter by restaurant if user is not superuser"""
        qs = super().get_queryset(request)
        if not request.user.is_superuser and hasattr(request.user, 'restaurant'):
            qs = qs.filter(template__restaurant=request.user.restaurant)
        return qs


@admin.register(ChecklistStepResponse)
class ChecklistStepResponseAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistStepResponse"""
    list_display = (
        'execution', 'step', 'is_completed', 'status',
        'measurement_value', 'completed_at'
    )
    list_filter = ('is_completed', 'status', 'step__step_type', 'completed_at')
    search_fields = ('execution__template__name', 'step__title', 'notes')
    readonly_fields = ('created_at', 'updated_at')
    
    fieldsets = (
        ('Response', {
            'fields': ('execution', 'step', 'is_completed', 'status')
        }),
        ('Response Data', {
            'fields': ('text_response', 'measurement_value', 'boolean_response'),
            'classes': ('collapse',)
        }),
        ('Documentation', {
            'fields': ('notes', 'signature_data')
        }),
        ('Timing', {
            'fields': ('started_at', 'completed_at')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        })
    )


@admin.register(ChecklistEvidence)
class ChecklistEvidenceAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistEvidence"""
    list_display = (
        'step_response', 'evidence_type', 'filename',
        'file_size_display', 'visibility', 'uploaded_at'
    )
    list_filter = ('evidence_type', 'visibility', 'uploaded_at')
    search_fields = ('filename', 'step_response__step__title')
    readonly_fields = ('id', 'file_size', 'uploaded_at')
    
    fieldsets = (
        ('File Information', {
            'fields': ('step_response', 'evidence_type', 'filename', 'file_path')
        }),
        ('File Details', {
            'fields': ('file_size', 'mime_type', 'visibility')
        }),
        ('Metadata', {
            'fields': ('metadata',),
            'classes': ('collapse',)
        }),
        ('System', {
            'fields': ('id', 'uploaded_at'),
            'classes': ('collapse',)
        })
    )
    
    def file_size_display(self, obj):
        """Display file size in human readable format"""
        if obj.file_size:
            if obj.file_size < 1024:
                return f"{obj.file_size} B"
            elif obj.file_size < 1024 * 1024:
                return f"{obj.file_size / 1024:.1f} KB"
            else:
                return f"{obj.file_size / (1024 * 1024):.1f} MB"
        return "Unknown"
    file_size_display.short_description = 'File Size'


@admin.register(ChecklistAction)
class ChecklistActionAdmin(admin.ModelAdmin):
    """Admin interface for ChecklistAction"""
    list_display = (
        'title', 'execution', 'priority', 'status',
        'assigned_to', 'due_date', 'created_at'
    )
    list_filter = ('priority', 'status', 'created_at', 'due_date')
    search_fields = ('title', 'description', 'execution__template__name')
    readonly_fields = ('id', 'created_at', 'resolved_at')
    
    fieldsets = (
        ('Action Details', {
            'fields': ('execution', 'title', 'description', 'priority')
        }),
        ('Assignment', {
            'fields': ('assigned_to', 'due_date')
        }),
        ('Status', {
            'fields': ('status', 'resolution_notes')
        }),
        ('Resolution', {
            'fields': ('resolved_by', 'resolved_at'),
            'classes': ('collapse',)
        }),
        ('Metadata', {
            'fields': ('id', 'created_by', 'created_at'),
            'classes': ('collapse',)
        })
    )
    
    def save_model(self, request, obj, form, change):
        """Set created_by when creating new action"""
        if not change:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


# Custom admin site configuration
admin.site.site_header = "Mizan Checklist Administration"
admin.site.site_title = "Mizan Checklist Admin"
admin.site.index_title = "Welcome to Mizan Checklist Administration"