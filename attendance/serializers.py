
from rest_framework import serializers
from .models import ShiftReview, ReviewLike

class ShiftReviewSerializer(serializers.ModelSerializer):
    """
    Serializer for the ShiftReview model.
    'staff', 'completed_at', and 'restaurant' are read-only
    because they are set in the view's perform_create logic.
    'likes_count' is an annotated field from the list view.
    """
    
    staff = serializers.PrimaryKeyRelatedField(read_only=True)
    likes_count = serializers.IntegerField(read_only=True, required=False)
    completed_at = serializers.DateTimeField(read_only=True)
    session_id = serializers.UUIDField(required=False)

    class Meta:
        model = ShiftReview
        fields = [
            'id',
            'session_id',
            'staff',
            'rating',
            'tags',
            'comments',
            'completed_at',
            'hours_decimal',
            'restaurant',
            'created_at',
            'updated_at',
            'likes_count',
        ]
        # These fields are also set by the server or are not
        # required on creation
        read_only_fields = [
            'id', 
            'restaurant', 
            'created_at', 
            'updated_at'
        ]

    def validate_session_id(self, value):
        return value

    def validate_rating(self, value):
        if not isinstance(value, int):
            raise serializers.ValidationError('Rating must be an integer')
        if value < 1 or value > 5:
            raise serializers.ValidationError('Rating must be between 1 and 5')
        return value

    def validate_tags(self, value):
        if value is None:
            return []
        if not isinstance(value, list):
            raise serializers.ValidationError('Tags must be a list')
        return [str(v) for v in value]
    
    
class ReviewLikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewLike
        fields = ["id", "review", "user", "created_at"]
        read_only_fields = ["id", "created_at"]