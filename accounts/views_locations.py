from datetime import timedelta

from django.db import IntegrityError, transaction
from django.utils import timezone
from rest_framework import serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from core.http_caching import json_response_with_cache
from core.read_through_cache import safe_cache_delete

from .models import BusinessLocation


WRITE_ROLES = {'SUPER_ADMIN', 'ADMIN', 'MANAGER'}


def _invalidate_portfolio_cache(restaurant_id):
    """
    Drop the multi-location portfolio cache entries for a tenant whenever a
    branch is created/updated/deleted/promoted, so the Locations Overview
    page picks up the change on the next poll instead of waiting out the
    60 s TTL. We clear a small day window because late-night edits may
    straddle the day boundary on the server timezone.
    """
    if not restaurant_id:
        return
    rid = str(restaurant_id)
    today = timezone.now().date()
    for offset in (-1, 0, 1):
        day = today + timedelta(days=offset)
        safe_cache_delete(f"dashboard:portfolio:v1:{rid}:{day.isoformat()}")


class BusinessLocationSerializer(serializers.ModelSerializer):
    """
    Public shape for /api/locations/.

    Keeps `restaurant` read-only so managers can't accidentally (or
    intentionally) move a location to another tenant by editing the payload —
    the tenant is always derived from `request.user.restaurant` in the view.

    The `latitude`/`longitude` fields are declared as lenient FloatField
    rather than inheriting from the model's strict DecimalField(9, 6). Map
    libraries hand us values like 34.01234567891234 which blow past
    `decimal_places=6` and DRF rejects them pre-save. Declaring FloatField
    here lets the serializer accept any precision and delegates quantization
    to the ORM on save — matching the behaviour the single-location endpoint
    has always had (it bypassed DRF validation entirely by assigning to the
    model directly).

    `radius` is similarly declared as FloatField so trailing decimals from
    the slider (e.g. 100.0) don't trip DecimalField parsing.
    """

    latitude = serializers.FloatField(required=False, allow_null=True)
    longitude = serializers.FloatField(required=False, allow_null=True)
    radius = serializers.FloatField(required=False)

    class Meta:
        model = BusinessLocation
        fields = [
            'id',
            'name',
            'address',
            'latitude',
            'longitude',
            'radius',
            'geofence_enabled',
            'geofence_polygon',
            'timezone',
            'is_primary',
            'is_active',
            'created_at',
            'updated_at',
        ]
        read_only_fields = ['id', 'is_primary', 'created_at', 'updated_at']

    def validate_latitude(self, value):
        if value is None:
            return value
        if value < -90 or value > 90:
            raise serializers.ValidationError('Latitude must be between -90 and 90.')
        return value

    def validate_longitude(self, value):
        if value is None:
            return value
        if value < -180 or value > 180:
            raise serializers.ValidationError('Longitude must be between -180 and 180.')
        return value

    def validate_radius(self, value):
        if value is None:
            return value
        try:
            value = float(value)
        except (TypeError, ValueError):
            raise serializers.ValidationError('Geofence radius must be a number.')
        # Inclusive bounds — UI default is exactly 100m.
        if value < 5 or value > 100:
            raise serializers.ValidationError(
                'Geofence radius must be between 5 and 100 meters.'
            )
        return value

    def validate_name(self, value):
        value = (value or '').strip()
        if not value:
            raise serializers.ValidationError('Location name is required.')
        return value

    def create(self, validated_data):
        # Coerce float coords/radius to values DecimalField accepts cleanly.
        for key in ('latitude', 'longitude', 'radius'):
            if key in validated_data and validated_data[key] is not None:
                validated_data[key] = round(
                    float(validated_data[key]), 6 if key != 'radius' else 2
                )
        if not validated_data.get('geofence_polygon'):
            validated_data['geofence_polygon'] = []
        try:
            return super().create(validated_data)
        except IntegrityError:
            # Unique primary-per-tenant race: force non-primary and retry once.
            restaurant = validated_data.get('restaurant')
            return BusinessLocation.objects.create(
                **{**validated_data, 'is_primary': False, 'restaurant': restaurant}
            )


class BusinessLocationViewSet(viewsets.ModelViewSet):
    """
    CRUD for a tenant's sites.

    - Every query is implicitly filtered to the caller's tenant, so a manager
      from restaurant A can never read or mutate a site belonging to
      restaurant B.
    - `is_primary` is never set via the serializer: the first site created
      becomes primary automatically (see BusinessLocation.save), and changing
      primary goes through the dedicated `set_primary` action so we can atomically
      flip the flag and sync Restaurant.*.
    - Deleting the primary site is blocked unless the tenant has another
      active site to promote — otherwise the geofence would silently open up.
    """

    serializer_class = BusinessLocationSerializer
    permission_classes = [IsAuthenticated]

    def get_queryset(self):
        rest = getattr(self.request.user, 'restaurant', None)
        if rest is None:
            return BusinessLocation.objects.none()
        qs = BusinessLocation.objects.filter(restaurant=rest)
        # Settings / pickers should not surface soft-deleted branches.
        # Detail/update/destroy still receive the pk; inactive rows are
        # excluded from list unless explicitly requested.
        if self.action == "list":
            include_inactive = str(
                self.request.query_params.get("include_inactive") or ""
            ).lower() in {"1", "true", "yes"}
            if not include_inactive:
                qs = qs.filter(is_active=True)
        return qs

    def list(self, request, *args, **kwargs):
        """
        Override DRF's default list to attach an ETag + Cache-Control. The
        location list is read on almost every page (sidebar, schedule
        modals, staff invite, dashboard widgets) but only changes when an
        admin opens the Settings page — perfect candidate for 304s.
        """
        queryset = self.filter_queryset(self.get_queryset())
        page = self.paginate_queryset(queryset)
        serializer = self.get_serializer(
            page if page is not None else queryset, many=True
        )
        if page is not None:
            payload = self.get_paginated_response(serializer.data).data
        else:
            payload = serializer.data
        return json_response_with_cache(
            request,
            payload,
            max_age=120,                   # 2 min: locations rarely change
            private=True,
            stale_while_revalidate=300,    # serve-stale for another 5 min
        )

    def perform_create(self, serializer):
        rest = getattr(self.request.user, 'restaurant', None)
        if rest is None:
            raise serializers.ValidationError(
                {'detail': 'No workspace associated with your account.'}
            )
        # Mirror the existing Settings page access control — anyone with
        # admin-level access to settings (SUPER_ADMIN / ADMIN / MANAGER) can
        # add branches. STAFF and specialised roles (CHEF, WAITER, …) never
        # see this UI, but we still guard the API.
        if getattr(self.request.user, 'role', None) not in WRITE_ROLES:
            raise serializers.ValidationError(
                {'detail': 'You do not have permission to add business locations.'}
            )
        try:
            serializer.save(restaurant=rest)
        except IntegrityError as exc:
            raise serializers.ValidationError(
                {
                    'detail': (
                        'Could not create that location because of a conflict '
                        'with an existing primary site. Refresh and try again.'
                    ),
                    'code': 'location_conflict',
                    'error': str(exc)[:200],
                }
            )
        _invalidate_portfolio_cache(rest.id)

    def create(self, request, *args, **kwargs):
        """
        Override to return a clear JSON body on unexpected failures instead of
        a bare 500 (e.g. mid-deploy / DB blip), so the Settings toast is useful.
        """
        try:
            return super().create(request, *args, **kwargs)
        except serializers.ValidationError:
            raise
        except Exception as exc:
            import logging
            logging.getLogger(__name__).exception("BusinessLocation create failed: %s", exc)
            return Response(
                {
                    'detail': (
                        'Could not create the location right now. '
                        'Please try again in a moment.'
                    ),
                    'error': str(exc)[:300],
                },
                status=status.HTTP_503_SERVICE_UNAVAILABLE,
            )

    def perform_update(self, serializer):
        # Mirror the existing single-location lock: once coordinates are set
        # on the PRIMARY, only SUPER_ADMIN can move it. Non-primary branches
        # stay editable by any admin/owner.
        instance = serializer.instance
        user = self.request.user
        new_lat = serializer.validated_data.get('latitude', instance.latitude)
        new_lng = serializer.validated_data.get('longitude', instance.longitude)
        moved = (new_lat != instance.latitude) or (new_lng != instance.longitude)
        if (
            instance.is_primary
            and instance.latitude is not None
            and instance.longitude is not None
            and moved
            and user.role != 'SUPER_ADMIN'
        ):
            raise serializers.ValidationError(
                {'detail': 'Primary location coordinates are locked. Contact a SUPER_ADMIN to move it.'}
            )
        serializer.save()
        _invalidate_portfolio_cache(instance.restaurant_id)

    def perform_destroy(self, instance):
        """Soft-delete the branch (``is_active=False``).

        Hard deletes break Locations Overview when portfolio cache/HTTP
        caching races the mutation, and also orphan historical shifts /
        clock events. Portfolio and geofence already filter ``is_active``.
        """
        rest = instance.restaurant
        if not instance.is_active:
            _invalidate_portfolio_cache(rest.id)
            return

        remaining = BusinessLocation.objects.filter(
            restaurant=rest, is_active=True
        ).exclude(pk=instance.pk)
        if instance.is_primary and not remaining.exists():
            raise serializers.ValidationError(
                {'detail': 'Cannot delete the only active location. Add another site first.'}
            )

        with transaction.atomic():
            if instance.is_primary:
                new_primary = remaining.order_by('-updated_at').first()
                instance.is_primary = False
                instance.is_active = False
                instance.save(update_fields=['is_primary', 'is_active', 'updated_at'])
                if new_primary:
                    new_primary.is_primary = True
                    new_primary.save()  # triggers Restaurant.* sync
            else:
                instance.is_active = False
                instance.save(update_fields=['is_active', 'updated_at'])
        _invalidate_portfolio_cache(rest.id)

    @action(detail=True, methods=['post'], url_path='set-primary')
    def set_primary(self, request, pk=None):
        """Promote this branch to primary (and demote the previous one)."""
        location = self.get_object()
        if getattr(request.user, 'role', None) not in WRITE_ROLES:
            return Response(
                {'detail': 'You do not have permission to change the primary location.'},
                status=status.HTTP_403_FORBIDDEN,
            )
        if location.is_primary:
            serializer = self.get_serializer(location)
            return Response(serializer.data)
        with transaction.atomic():
            BusinessLocation.objects.filter(
                restaurant=location.restaurant, is_primary=True
            ).exclude(pk=location.pk).update(is_primary=False)
            location.is_primary = True
            location.save()  # syncs Restaurant.*
        _invalidate_portfolio_cache(location.restaurant_id)
        serializer = self.get_serializer(location)
        return Response(serializer.data)
