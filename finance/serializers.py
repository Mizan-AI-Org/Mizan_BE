from __future__ import annotations

from rest_framework import serializers

from .models import Invoice


class InvoiceSerializer(serializers.ModelSerializer):
    is_overdue = serializers.BooleanField(read_only=True)
    days_until_due = serializers.IntegerField(read_only=True)
    location_name = serializers.SerializerMethodField()
    created_by_name = serializers.SerializerMethodField()
    paid_by_name = serializers.SerializerMethodField()
    attachment_url = serializers.SerializerMethodField()
    has_attachment = serializers.SerializerMethodField()

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
            "attachment",
            "attachment_content_type",
            "attachment_filename",
            "attachment_url",
            "has_attachment",
            "photo_url",
            "paid_at",
            "paid_amount",
            "payment_method",
            "payment_reference",
            "purchase_order",
            "match_status",
            "match_confidence",
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
            "attachment_url",
            "has_attachment",
            "match_status",
            "match_confidence",
            "created_at",
            "updated_at",
        ]

    def _absolute_file_url(self, file_field) -> str:
        if not file_field:
            return ""
        url = file_field.url
        request = self.context.get("request")
        if request and url:
            return request.build_absolute_uri(url)
        return url

    def get_attachment_url(self, obj):
        stored = obj.attachment or obj.photo
        if stored:
            return self._absolute_file_url(stored)
        return (obj.photo_url or "").strip()

    def get_has_attachment(self, obj) -> bool:
        return bool(obj.attachment or obj.photo or (obj.photo_url or "").strip())

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
