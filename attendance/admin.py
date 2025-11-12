from django.contrib import admin
from .models import ShiftReview, ReviewLike


@admin.register(ShiftReview)
class ShiftReviewAdmin(admin.ModelAdmin):
    list_display = ["staff", "rating", "completed_at", "hours_decimal"]
    list_filter = ["rating", "completed_at"]
    search_fields = ["staff__email", "comments"]


@admin.register(ReviewLike)
class ReviewLikeAdmin(admin.ModelAdmin):
    list_display = ["review", "user", "created_at"]
    list_filter = ["created_at"]
    search_fields = ["user__email"]