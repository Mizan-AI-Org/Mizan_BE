from rest_framework import serializers
from .models import DailyKPI, Alert, Task, StaffCapturedOrder
from accounts.serializers import CustomUserSerializer

class DailyKPISerializer(serializers.ModelSerializer):
    class Meta:
        model = DailyKPI
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')

class AlertSerializer(serializers.ModelSerializer):
    class Meta:
        model = Alert
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at')

class TaskSerializer(serializers.ModelSerializer):
    assigned_to_info = CustomUserSerializer(source='assigned_to', read_only=True)

    class Meta:
        model = Task
        fields = '__all__'
        read_only_fields = ('restaurant', 'created_at', 'updated_at')


class DashboardTaskCompactSerializer(serializers.ModelSerializer):
    """
    Small shape for the Tasks & Demands dashboard widget.

    Keeps the payload tiny so the widget can poll without cost creep:
    just id / title / description / priority / status / due_date / source
    info plus the bare-minimum assignee fields needed to render an avatar
    + name. Full user data (restaurant, permissions, …) is not shipped.
    """

    assignee = serializers.SerializerMethodField()

    class Meta:
        model = Task
        fields = (
            'id',
            'title',
            'description',
            'priority',
            'status',
            'due_date',
            'source',
            'source_label',
            'ai_summary',
            'category',
            'assignee',
            'created_at',
            'updated_at',
        )
        read_only_fields = fields

    def get_assignee(self, obj):
        u = obj.assigned_to
        if not u:
            return None
        first = (getattr(u, 'first_name', None) or '').strip()
        last = (getattr(u, 'last_name', None) or '').strip()
        full = (f"{first} {last}").strip() or (getattr(u, 'email', None) or '')
        initials = (
            (first[:1] + last[:1]).upper()
            or (full[:2] if full else '').upper()
        )
        return {
            'id': str(u.pk),
            'name': full,
            'initials': initials or '?',
            'role': getattr(u, 'role', None),
        }


class StaffCapturedOrderSerializer(serializers.ModelSerializer):
    recorded_by_name = serializers.SerializerMethodField()

    class Meta:
        model = StaffCapturedOrder
        fields = (
            "id",
            "customer_name",
            "customer_phone",
            "order_type",
            "table_or_location",
            "items_summary",
            "dietary_notes",
            "special_instructions",
            "channel",
            "fulfillment_status",
            "created_at",
            "updated_at",
            "recorded_by_name",
        )
        read_only_fields = (
            "id",
            "created_at",
            "updated_at",
            "recorded_by_name",
            "fulfillment_status",
        )

    def get_recorded_by_name(self, obj):
        u = obj.recorded_by
        if not u:
            return None
        parts = [getattr(u, "first_name", None) or "", getattr(u, "last_name", None) or ""]
        name = " ".join(p for p in parts if p).strip()
        return name or getattr(u, "email", None) or str(u.pk)

    def validate_items_summary(self, value):
        if not (value or "").strip():
            raise serializers.ValidationError("Items / order details are required.")
        return value.strip()


class StaffCapturedOrderStatusSerializer(serializers.ModelSerializer):
    """PATCH: update fulfillment only."""

    class Meta:
        model = StaffCapturedOrder
        fields = ("fulfillment_status",)


class StaffCapturedOrderPartialUpdateSerializer(StaffCapturedOrderSerializer):
    """PATCH/PUT: update status and/or line items (same fields as create, except restaurant is implicit)."""

    class Meta(StaffCapturedOrderSerializer.Meta):
        read_only_fields = ("id", "created_at", "updated_at", "recorded_by_name")
