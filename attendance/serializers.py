from rest_framework import serializers
from .models import ShiftReview, ReviewLike


class ShiftReviewSerializer(serializers.ModelSerializer):
    likes_count = serializers.IntegerField(read_only=True)
    staff_name = serializers.SerializerMethodField()
    shift_id = serializers.UUIDField()
    completed_at_iso = serializers.DateTimeField(source="completed_at")

    class Meta:
        model = ShiftReview
        fields = [
            "id",
            "shift_id",
            "staff",
            "staff_name",
            "rating",
            "tags",
            "comments",
            "completed_at_iso",
            "hours_decimal",
            "likes_count",
        ]
        read_only_fields = ["id", "likes_count", "staff_name"]

    def get_staff_name(self, obj):
        try:
            return f"{obj.staff.first_name} {obj.staff.last_name}".strip() or obj.staff.email
        except Exception:
            return None


class ReviewLikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewLike
        fields = ["id", "review", "user", "created_at"]
        read_only_fields = ["id", "created_at"]