from django.contrib import admin

from .models import AutomationRunLog, TenantAutomation


@admin.register(TenantAutomation)
class TenantAutomationAdmin(admin.ModelAdmin):
    list_display = ("name", "restaurant", "trigger_type", "is_active", "run_count", "updated_at")
    list_filter = ("is_active", "trigger_type")
    search_fields = ("name", "restaurant__name")


@admin.register(AutomationRunLog)
class AutomationRunLogAdmin(admin.ModelAdmin):
    list_display = ("automation", "phone", "trigger_event", "success", "created_at")
    list_filter = ("success",)
