from django.contrib import admin
from django.utils.html import format_html
from .models import Table, Order, OrderLineItem, Payment, POSTransaction, ReceiptSetting


@admin.register(Table)
class TableAdmin(admin.ModelAdmin):
    list_display = ['table_number', 'capacity', 'status_colored', 'section', 'restaurant']
    list_filter = ['status', 'section', 'restaurant', 'created_at']
    search_fields = ['table_number', 'section', 'restaurant__name']
    fieldsets = (
        ('Basic Info', {
            'fields': ('restaurant', 'table_number', 'capacity', 'section', 'status')
        }),
        ('Settings', {
            'fields': ('is_active',)
        }),
    )
    
    def status_colored(self, obj):
        colors = {
            'AVAILABLE': '#28a745',
            'OCCUPIED': '#ffc107',
            'RESERVED': '#0dcaf0',
            'MAINTENANCE': '#dc3545',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_colored.short_description = 'Status'


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ['order_number', 'restaurant', 'status_colored', 'order_type', 'total_amount', 'server', 'order_time']
    list_filter = ['status', 'order_type', 'restaurant', 'order_time']
    search_fields = ['order_number', 'customer_name', 'customer_phone', 'restaurant__name']
    readonly_fields = ['order_number', 'order_time', 'created_at', 'updated_at', 'subtotal', 'total_amount']
    fieldsets = (
        ('Order Info', {
            'fields': ('restaurant', 'order_number', 'order_type', 'status', 'table', 'server')
        }),
        ('Customer Details', {
            'fields': ('customer_name', 'customer_phone', 'customer_email', 'guest_count')
        }),
        ('Delivery Info', {
            'fields': ('delivery_address', 'delivery_instructions'),
            'classes': ('collapse',)
        }),
        ('Amounts', {
            'fields': ('subtotal', 'tax_amount', 'discount_amount', 'discount_reason', 'total_amount')
        }),
        ('Status & Notes', {
            'fields': ('notes', 'is_priority', 'order_time', 'ready_time', 'completion_time')
        }),
        ('Metadata', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def status_colored(self, obj):
        colors = {
            'PENDING': '#6c757d',
            'CONFIRMED': '#0dcaf0',
            'PREPARING': '#ffc107',
            'READY': '#0d6efd',
            'SERVED': '#6f42c1',
            'COMPLETED': '#28a745',
            'CANCELLED': '#dc3545',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_colored.short_description = 'Status'


@admin.register(OrderLineItem)
class OrderLineItemAdmin(admin.ModelAdmin):
    list_display = ['order', 'menu_item', 'quantity', 'unit_price', 'total_price', 'status']
    list_filter = ['status', 'created_at', 'order__restaurant']
    search_fields = ['order__order_number', 'menu_item__name']
    readonly_fields = ['total_price', 'created_at', 'updated_at']
    fieldsets = (
        ('Order Info', {
            'fields': ('order', 'menu_item')
        }),
        ('Item Details', {
            'fields': ('quantity', 'unit_price', 'total_price')
        }),
        ('Notes', {
            'fields': ('special_instructions', 'status')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['id', 'order', 'payment_method', 'amount', 'status_colored', 'tip_amount', 'payment_time']
    list_filter = ['status', 'payment_method', 'payment_time', 'restaurant']
    search_fields = ['order__order_number', 'transaction_id']
    readonly_fields = ['payment_time', 'created_at', 'updated_at']
    fieldsets = (
        ('Order & Restaurant', {
            'fields': ('order', 'restaurant')
        }),
        ('Payment Details', {
            'fields': ('payment_method', 'amount', 'status', 'transaction_id', 'processor_name')
        }),
        ('Amounts', {
            'fields': ('amount_paid', 'change_given', 'tip_amount')
        }),
        ('Refund Info', {
            'fields': ('refund_amount', 'refund_reason'),
            'classes': ('collapse',)
        }),
        ('Processing', {
            'fields': ('processed_by', 'notes', 'payment_time')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )
    
    def status_colored(self, obj):
        colors = {
            'PENDING': '#ffc107',
            'COMPLETED': '#28a745',
            'FAILED': '#dc3545',
            'REFUNDED': '#0dcaf0',
            'PARTIALLY_REFUNDED': '#0d6efd',
        }
        color = colors.get(obj.status, '#6c757d')
        return format_html(
            '<span style="background-color: {}; color: white; padding: 3px 10px; border-radius: 3px;">{}</span>',
            color,
            obj.get_status_display()
        )
    status_colored.short_description = 'Status'


@admin.register(POSTransaction)
class POSTransactionAdmin(admin.ModelAdmin):
    list_display = ['transaction_type', 'order', 'user', 'amount_involved', 'created_at']
    list_filter = ['transaction_type', 'created_at', 'restaurant']
    search_fields = ['order__order_number', 'user__email', 'description']
    readonly_fields = ['created_at']
    fieldsets = (
        ('Transaction Info', {
            'fields': ('restaurant', 'transaction_type', 'order', 'user')
        }),
        ('Changes', {
            'fields': ('description', 'previous_value', 'new_value', 'amount_involved')
        }),
        ('Timestamp', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )


@admin.register(ReceiptSetting)
class ReceiptSettingAdmin(admin.ModelAdmin):
    list_display = ['restaurant', 'paper_width', 'font_size_items', 'updated_at']
    readonly_fields = ['created_at', 'updated_at']
    fieldsets = (
        ('Restaurant', {
            'fields': ('restaurant',)
        }),
        ('Header & Footer', {
            'fields': ('header_text', 'footer_text', 'logo')
        }),
        ('Display Options', {
            'fields': ('show_item_codes', 'show_item_descriptions', 'show_unit_prices',
                      'show_discount_details', 'show_tax_breakdown')
        }),
        ('Printer Settings', {
            'fields': ('paper_width', 'font_size_items', 'font_size_total')
        }),
        ('Timestamps', {
            'fields': ('created_at', 'updated_at'),
            'classes': ('collapse',)
        }),
    )