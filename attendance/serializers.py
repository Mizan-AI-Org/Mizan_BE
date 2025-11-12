
from rest_framework import serializers
from .models import ShiftReview, ReviewLike

class ShiftReviewSerializer(serializers.ModelSerializer):
    """
    Serializer for the ShiftReview model.
    'staff', 'completed_at', and 'restaurant' are read-only
    because they are set in the view's perform_create logic.
    'likes_count' is an annotated field from the list view.
    """
    
    # staff is read_only=True because it comes from request.user
    staff = serializers.PrimaryKeyRelatedField(read_only=True)
    
    # likes_count is a read-only annotation
    likes_count = serializers.IntegerField(read_only=True, required=False)
    
    # completed_at is read_only=True because it's calculated
    # from 'completed_at_iso' in the view
    completed_at = serializers.DateTimeField(read_only=True)

    class Meta:
        model = ShiftReview
        fields = [
            'id',
            'shift_id',
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

    def validate_shift_id(self, value):
        # You can add validation here to ensure the shift exists
        # e.g., from scheduling.models import AssignedShift
        # if not AssignedShift.objects.filter(id=value).exists():
        #     raise serializers.ValidationError("Shift not found.")
        return value
    
    
class ReviewLikeSerializer(serializers.ModelSerializer):
    class Meta:
        model = ReviewLike
        fields = ["id", "review", "user", "created_at"]
        read_only_fields = ["id", "created_at"]