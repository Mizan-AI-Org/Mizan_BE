from django.contrib import admin

from accounts.models import Restaurant, StaffActivationRecord


@admin.register(StaffActivationRecord)
class StaffActivationRecordAdmin(admin.ModelAdmin):
    list_display = ['phone', 'restaurant', 'status', 'user', 'activated_at', 'created_at']
    list_filter = ['status', 'restaurant']
    search_fields = ['phone', 'first_name', 'last_name']


# Register your models here.
@admin.register(Restaurant)
class RestaurantAdmin(admin.ModelAdmin):
    list_display = ['name', 'address', 'latitude', 'longitude', 'radius']
    fieldsets = (
        ('Basic Info', {
            'fields': ('name', 'address', 'phone', 'email')
        }),
        ('Location Settings', {
            'fields': ('latitude', 'longitude', 'radius')
        }),
    )