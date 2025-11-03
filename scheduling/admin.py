from django.contrib import admin
from .models import (
    ScheduleTemplate, TemplateShift, WeeklySchedule, AssignedShift,
    ShiftSwapRequest, TaskCategory, ShiftTask
)
from .admin_audit import add_audit_info_to_admin

@admin.register(ScheduleTemplate)
@add_audit_info_to_admin
class ScheduleTemplateAdmin(admin.ModelAdmin):
    list_display = ['name', 'restaurant', 'is_active', 'created_at', 'updated_at']
    list_filter = ['is_active', 'restaurant', 'created_at']
    search_fields = ['name', 'description']
    readonly_fields = ['id', 'created_at', 'updated_at']

@admin.register(TemplateShift)
class TemplateShiftAdmin(admin.ModelAdmin):
    list_display = ['template', 'role', 'day_of_week', 'start_time', 'end_time', 'required_staff']
    list_filter = ['role', 'day_of_week']
    search_fields = ['template__name', 'role']

@admin.register(WeeklySchedule)
@add_audit_info_to_admin
class WeeklyScheduleAdmin(admin.ModelAdmin):
    list_display = ['restaurant', 'week_start', 'week_end', 'is_published', 'created_at']
    list_filter = ['is_published', 'restaurant']
    search_fields = ['restaurant__name']

@admin.register(AssignedShift)
class AssignedShiftAdmin(admin.ModelAdmin):
    list_display = ['staff', 'shift_date', 'role', 'start_time', 'end_time', 'actual_hours']
    list_filter = ['role', 'shift_date', 'schedule__restaurant']
    search_fields = ['staff__email', 'staff__first_name', 'staff__last_name']

@admin.register(ShiftSwapRequest)
class ShiftSwapRequestAdmin(admin.ModelAdmin):
    list_display = ['requester', 'shift_to_swap', 'receiver', 'status', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['requester__email', 'receiver__email']

@admin.register(TaskCategory)
class TaskCategoryAdmin(admin.ModelAdmin):
    list_display = ['name', 'restaurant', 'color', 'created_at']
    list_filter = ['restaurant']
    search_fields = ['name', 'restaurant__name']

@admin.register(ShiftTask)
@add_audit_info_to_admin
class ShiftTaskAdmin(admin.ModelAdmin):
    list_display = ['title', 'shift', 'priority', 'status', 'assigned_to', 'created_at']
    list_filter = ['priority', 'status', 'category', 'created_at']
    search_fields = ['title', 'description', 'shift__staff__username']
    readonly_fields = ['id', 'created_at', 'updated_at', 'completed_at']
