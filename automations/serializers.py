from rest_framework import serializers

from .constants import (
    ACTION_CATALOG,
    ACTION_TYPES,
    CATALOG_CATEGORIES,
    QUICK_START_TEMPLATES,
    TRIGGER_CATALOG,
    TRIGGER_TYPES,
    VARIABLE_TOKENS,
)
from .models import AutomationRunLog, TenantAutomation
from .services.engine import normalize_automation_steps


class TenantAutomationSerializer(serializers.ModelSerializer):
    trigger_label = serializers.SerializerMethodField()
    last_run_ago = serializers.SerializerMethodField()

    class Meta:
        model = TenantAutomation
        fields = [
            "id",
            "name",
            "description",
            "is_active",
            "trigger_type",
            "trigger_config",
            "trigger_label",
            "steps",
            "template_id",
            "run_count",
            "last_run_at",
            "last_run_ago",
            "stop_miya_on_match",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["run_count", "last_run_at", "created_at", "updated_at"]

    def get_trigger_label(self, obj) -> str:
        return TRIGGER_TYPES.get(obj.trigger_type, obj.trigger_type)

    def get_last_run_ago(self, obj) -> str | None:
        if not obj.last_run_at:
            return None
        from django.utils import timezone

        delta = timezone.now() - obj.last_run_at
        if delta.days:
            return f"{delta.days}d ago"
        hours = delta.seconds // 3600
        if hours:
            return f"{hours}h ago"
        return "just now"

    def validate_trigger_type(self, value: str) -> str:
        if value not in TRIGGER_TYPES:
            raise serializers.ValidationError(f"Unknown trigger: {value}")
        return value

    def validate_steps(self, value):
        if not isinstance(value, list):
            raise serializers.ValidationError("steps must be a list")
        normalized = normalize_automation_steps(value)
        for step in normalized:
            stype = step.get("type")
            if stype not in ACTION_TYPES:
                raise serializers.ValidationError(f"Unknown action type: {stype}")
        return normalized

    def to_representation(self, instance):
        data = super().to_representation(instance)
        data["steps"] = normalize_automation_steps(data.get("steps") or [])
        return data


class AutomationRunLogSerializer(serializers.ModelSerializer):
    class Meta:
        model = AutomationRunLog
        fields = ["id", "automation", "phone", "trigger_event", "success", "detail", "created_at"]


class AutomationCatalogSerializer(serializers.Serializer):
    triggers = serializers.DictField()
    actions = serializers.DictField()
    templates = serializers.ListField()
    trigger_catalog = serializers.ListField()
    action_catalog = serializers.ListField()
    categories = serializers.DictField()
    variables = serializers.ListField()

    @staticmethod
    def build():
        return {
            "triggers": TRIGGER_TYPES,
            "actions": ACTION_TYPES,
            "templates": QUICK_START_TEMPLATES,
            "trigger_catalog": [
                {
                    **item,
                    "label": TRIGGER_TYPES.get(item["id"], item["id"]),
                }
                for item in TRIGGER_CATALOG
            ],
            "action_catalog": [
                {
                    **item,
                    "label": ACTION_TYPES.get(item["id"], item["id"]),
                }
                for item in ACTION_CATALOG
            ],
            "categories": CATALOG_CATEGORIES,
            "variables": VARIABLE_TOKENS,
        }
