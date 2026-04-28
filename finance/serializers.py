from __future__ import annotations

from rest_framework import serializers

from .models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    days_until_due = serializers.IntegerField(read_only=True)
    location_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    paid_by_name = serializers.SerializerMethodField()

    class Meta:
        model = Invoice
        fields = [
            "id",
            "restaurant",
            "location",
            "location_name",
            "vendor_name",
            "invoice_number",
            "amount",
            "currency",
            "issue_date",
            "due_date",
            "status",
            "category",
            "notes",
            "photo",
            "photo_url",
            "paid_at",
            "paid_amount",
            "payment_method",
            "payment_reference",
            "created_by",
            "created_by_name",
            "paid_by",
            "paid_by_name",
            "is_overdue",
            "days_until_due",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "id",
            "restaurant",
            "is_overdue",
            "days_until_due",
            "location_name",
            "created_by_name",
            "paid_by_name",
            "created_at",
            "updated_at",
        ]

    def get_location_name(self, obj):
        return obj.location.name if obj.location_id else ""

    def get_created_by_name(self, obj):
        u = obj.created_by
        if not u:
            return ""
        return u.get_full_name() or u.email or ""

    def get_paid_by_name(self, obj):
        u = obj.paid_by
        if not u:
            return ""
        return u.get_full_name() or u.email or ""
