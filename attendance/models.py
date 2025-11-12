import uuid
from django.conf import settings
from django.db import models


class ShiftReview(models.Model):
    """Feedback submitted by staff immediately after clock-out.

    Links to `scheduling.AssignedShift` to keep referential integrity and
    allows managers to aggregate by staff, date, and rating.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    # Store the shift identifier directly to avoid hard dependency on scheduling app
    shift_id = models.UUIDField()
    staff = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="shift_reviews",
    )

    rating = models.PositiveSmallIntegerField()  # 1..5
    tags = models.JSONField(default=list, blank=True)
    comments = models.TextField(blank=True, null=True)
    completed_at = models.DateTimeField()  # ISO when review was made
    hours_decimal = models.DecimalField(max_digits=6, decimal_places=2, null=True, blank=True)

    restaurant = models.ForeignKey(
        "accounts.Restaurant",
        on_delete=models.CASCADE,
        related_name="shift_reviews",
        null=True,
        blank=True,
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        db_table = "attendance_shift_reviews"
        indexes = [
            models.Index(fields=["staff", "completed_at"]),
            models.Index(fields=["restaurant", "completed_at"]),
            models.Index(fields=["rating"]),
        ]

    def __str__(self):
        return f"Review {self.id} by {self.staff_id} for shift {self.shift_id}"


class ReviewLike(models.Model):
    """Peer 'like' for a shift review to provide lightweight feedback."""

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    review = models.ForeignKey(
        ShiftReview,
        on_delete=models.CASCADE,
        related_name="likes",
    )
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.CASCADE,
        related_name="review_likes",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        db_table = "attendance_review_likes"
        unique_together = ("review", "user")
        indexes = [
            models.Index(fields=["review"]),
            models.Index(fields=["user"]),
        ]

    def __str__(self):
        return f"Like by {self.user_id} on review {self.review_id}"