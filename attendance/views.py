from datetime import datetime
import logging
from django.db.models import Count, Q
from django.db.models import Count
from django.utils.dateparse import parse_date
from rest_framework import generics, permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

from accounts.permissions import IsAdminOrSuperAdmin, IsAdminOrManager
from accounts.models import AuditLog
from .models import ShiftReview, ReviewLike
try:
    # Optional import; only used to derive restaurant when user has none
    from scheduling.models import AssignedShift
except Exception:  # pragma: no cover
    AssignedShift = None  # type: igxnore

logger = logging.getLogger(__name__)
from .serializers import ShiftReviewSerializer


class ShiftReviewListCreateAPIView(generics.ListCreateAPIView):
    queryset = ShiftReview.objects.all()
    serializer_class = ShiftReviewSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_queryset(self):
        qs = super().get_queryset().select_related("staff")
        # Aggregate likes count for list
        qs = qs.annotate(likes_count=Count("likes"))

        # Filters: date_from, date_to, staff_id, rating
        date_from = self.request.query_params.get("date_from")
        date_to = self.request.query_params.get("date_to")
        staff_id = self.request.query_params.get("staff_id")
        staff_email = self.request.query_params.get("staff_email")
        rating = self.request.query_params.get("rating")

        if date_from:
            try:
                df = parse_date(date_from)
                if df:
                    qs = qs.filter(completed_at__date__gte=df)
            except Exception:
                pass
        if date_to:
            try:
                dt = parse_date(date_to)
                if dt:
                    qs = qs.filter(completed_at__date__lte=dt)
            except Exception:
                pass
        if staff_id:
            qs = qs.filter(staff_id=staff_id)
        if staff_email:
            qs = qs.filter(staff__email__iexact=staff_email)
        if rating:
            try:
                qs = qs.filter(rating=int(rating))
            except Exception:
                pass

        # Restrict by restaurant if user has restaurant context, but include
        # orphaned reviews (restaurant_id is NULL) when the staff belongs to
        # the same restaurant. This prevents hidden data when older records
        # were saved without restaurant linkage.
        try:
            restaurant_id = getattr(self.request.user, "restaurant_id", None)
            if restaurant_id:
                qs = qs.filter(
                    Q(restaurant_id=restaurant_id) |
                    Q(restaurant_id__isnull=True, staff__restaurant_id=restaurant_id)
                )
        except Exception:
            pass

        try:
            logger.debug(
                "ShiftReview list filters applied",
                extra={
                    "date_from": date_from,
                    "date_to": date_to,
                    "staff_id": staff_id,
                    "rating": rating,
                    "staff_email": staff_email,
                    "restaurant_id": getattr(self.request.user, "restaurant_id", None),
                },
            )
        except Exception:
            # Logging should never break the endpoint
            pass
        return qs.order_by("-completed_at")

    def perform_create(self, serializer):
        # staff is the authenticated user by default
        user = self.request.user
        session = self.request.data.get("session_id") or self.request.data.get("shift_id")  # backward compat
        completed_at_iso = self.request.data.get("completed_at_iso")
        try:
            # Support both ISO strings with 'Z' and offset-aware times
            # Replace trailing 'Z' with '+00:00' for fromisoformat compatibility
            iso_value = (completed_at_iso or "").replace("Z", "+00:00")
            completed_at = datetime.fromisoformat(iso_value) if iso_value else datetime.utcnow()
        except Exception:
            completed_at = datetime.utcnow()

        # hours_decimal is optional; if not provided, try compute from shift
        hours_decimal = self.request.data.get("hours_decimal")
        extra = {}
        if hours_decimal is not None:
            extra["hours_decimal"] = hours_decimal

        restaurant_obj = getattr(user, "restaurant", None)
        # Previously attempted to derive restaurant from a scheduled shift id. Now session_id
        # refers to a timeclock ClockEvent; derivation from scheduling is skipped.

        # Final fallback: try user's primary role mapping to derive restaurant
        if restaurant_obj is None:
            try:
                from accounts.models import UserRole  # local import to avoid circulars
                primary_role = (
                    UserRole.objects.select_related("restaurant")
                    .filter(user=user, is_primary=True)
                    .first()
                )
                if not primary_role:
                    primary_role = (
                        UserRole.objects.select_related("restaurant")
                        .filter(user=user)
                        .first()
                    )
                if primary_role:
                    restaurant_obj = getattr(primary_role, "restaurant", None)
            except Exception:
                # If RBAC not configured or query fails, proceed without restaurant
                pass

        instance = serializer.save(
            staff=user,
            session_id=session,
            completed_at=completed_at,
            restaurant=restaurant_obj,
            **extra,
        )

        try:
            logger.info(
                "ShiftReview created",
                extra={
                    "review_id": str(getattr(instance, "id", "")),
                    "staff_id": str(getattr(user, "id", "")),
                    "restaurant_id": getattr(restaurant_obj, "id", None),
                    "session_id": session,
                    "rating": self.request.data.get("rating"),
                    "tags": self.request.data.get("tags"),
                    "hours_decimal": self.request.data.get("hours_decimal"),
                },
            )
            AuditLog.create_log(
                restaurant=restaurant_obj,
                user=user,
                action_type='CREATE',
                entity_type='SHIFT_REVIEW',
                entity_id=str(getattr(instance, 'id', '')),
                description='Shift review submitted',
                new_values={
                    'session_id': str(session),
                    'rating': self.request.data.get('rating'),
                    'tags': self.request.data.get('tags'),
                    'hours_decimal': self.request.data.get('hours_decimal'),
                },
            )
        except Exception:
            pass

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        if not serializer.is_valid():
            try:
                logger.error(
                    "ShiftReview validation failed",
                    extra={
                        "errors": serializer.errors,
                        "payload_keys": list(request.data.keys()),
                        "staff_id": str(getattr(request.user, "id", "")),
                    },
                )
                AuditLog.create_log(
                    restaurant=getattr(request.user, 'restaurant', None),
                    user=request.user,
                    action_type='UPDATE',
                    entity_type='SHIFT_REVIEW',
                    entity_id=None,
                    description='Shift review submission failed',
                    old_values={},
                    new_values={
                        'errors': serializer.errors,
                        'payload_keys': list(request.data.keys()),
                    },
                )
            except Exception:
                pass
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        self.perform_create(serializer)
        headers = self.get_success_headers(serializer.data)
        return Response(serializer.data, status=status.HTTP_201_CREATED, headers=headers)


class ReviewLikeToggleAPIView(APIView):
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        """Toggle like for the authenticated user on a specific review."""
        try:
            review = ShiftReview.objects.get(pk=pk)
        except ShiftReview.DoesNotExist:
            return Response({"detail": "Review not found"}, status=status.HTTP_404_NOT_FOUND)

        like, created = ReviewLike.objects.get_or_create(review=review, user=request.user)
        if not created:
            like.delete()
            toggled = False
        else:
            toggled = True

        count = ReviewLike.objects.filter(review=review).count()
        return Response({"liked": toggled, "likes_count": count})


class ShiftReviewStatsAPIView(APIView):
    permission_classes = [IsAdminOrManager]

    def get(self, request):
        qs = ShiftReview.objects.all()
        date_from = request.query_params.get("date_from")
        date_to = request.query_params.get("date_to")
        if date_from:
            df = parse_date(date_from)
            if df:
                qs = qs.filter(completed_at__date__gte=df)
        if date_to:
            dt = parse_date(date_to)
            if dt:
                qs = qs.filter(completed_at__date__lte=dt)

        try:
            restaurant_id = getattr(request.user, "restaurant_id", None)
            if restaurant_id:
                # Include reviews explicitly linked to the restaurant and
                # orphaned reviews where the staff belongs to the same restaurant.
                # This mirrors the list endpoint behavior to avoid hidden data.
                qs = qs.filter(
                    Q(restaurant_id=restaurant_id) |
                    Q(restaurant_id__isnull=True, staff__restaurant_id=restaurant_id)
                )
        except Exception:
            pass

        # Aggregations: count by rating, total likes, recent tag frequency
        by_rating = (
            qs.values("rating").annotate(count=Count("id")).order_by("rating")
        )
        total_likes = ReviewLike.objects.filter(review__in=qs).count()
        tag_counts = {}
        for r in qs:
            for t in (r.tags or []):
                tag_counts[t] = tag_counts.get(t, 0) + 1

        return Response({
            "by_rating": list(by_rating),
            "total_reviews": qs.count(),
            "total_likes": total_likes,
            "tag_counts": tag_counts,
        })