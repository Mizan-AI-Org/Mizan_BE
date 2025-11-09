from django.urls import path
from .views import (
    ShiftReviewListCreateAPIView,
    ReviewLikeToggleAPIView,
    ShiftReviewStatsAPIView,
)


urlpatterns = [
    path("shift-reviews/", ShiftReviewListCreateAPIView.as_view(), name="shift-review-list-create"),
    path("shift-reviews/<uuid:pk>/like/", ReviewLikeToggleAPIView.as_view(), name="shift-review-like-toggle"),
    path("shift-reviews/stats/", ShiftReviewStatsAPIView.as_view(), name="shift-review-stats"),
]