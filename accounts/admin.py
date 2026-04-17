from django.contrib import admin

from accounts.models import EatNowReservation, EatNowWebhookDelivery, Restaurant, StaffActivationRecord


@admin.register(EatNowReservation)
class EatNowReservationAdmin(admin.ModelAdmin):
    list_display = ["guest_name", "external_id", "restaurant", "reservation_date", "reservation_time", "status", "is_deleted", "updated_at"]
    list_filter = ["is_deleted", "restaurant", "status"]
    search_fields = ["external_id", "guest_name", "phone", "email"]
    readonly_fields = ["id", "restaurant", "external_id", "created_at", "updated_at", "raw_reservation"]

    def has_add_permission(self, request):
        return False


@admin.register(EatNowWebhookDelivery)
class EatNowWebhookDeliveryAdmin(admin.ModelAdmin):
    list_display = ["delivery_id", "restaurant", "event_type", "received_at"]
    list_filter = ["event_type", "restaurant"]
    search_fields = ["delivery_id", "event_type"]
    readonly_fields = ["id", "restaurant", "delivery_id", "event_type", "payload", "received_at"]

    def has_add_permission(self, request):
        return False


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