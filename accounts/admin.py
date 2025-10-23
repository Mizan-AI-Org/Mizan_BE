from django.contrib import admin

from accounts.models import Restaurant

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