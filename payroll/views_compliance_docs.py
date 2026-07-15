"""JWT CRUD for restaurant compliance documents (Settings UI)."""
from __future__ import annotations

from django.utils import timezone
from django.utils.dateparse import parse_date
from rest_framework import permissions, status, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.serializers import ModelSerializer, CharField, DateField, IntegerField

from payroll.models import ComplianceDocument
from payroll.services.compliance_documents import (
    DOCUMENT_TYPE_CHOICES,
    seed_starter_documents,
    serialize_document,
)


class ComplianceDocumentSerializer(ModelSerializer):
    document_type = CharField(required=False)
    title = CharField(required=False, allow_blank=False)
    description = CharField(required=False, allow_blank=True)
    reference_number = CharField(required=False, allow_blank=True)
    expires_at = DateField(required=False, allow_null=True)
    remind_days_before = IntegerField(required=False, min_value=1, max_value=365)
    status = CharField(required=False)

    class Meta:
        model = ComplianceDocument
        fields = [
            "id",
            "document_type",
            "title",
            "description",
            "reference_number",
            "expires_at",
            "remind_days_before",
            "status",
            "last_notified_at",
            "created_at",
            "updated_at",
        ]
        read_only_fields = ["id", "last_notified_at", "created_at", "updated_at"]


class ComplianceDocumentViewSet(viewsets.ModelViewSet):
    """
    /api/payroll/compliance-documents/
    Managers track business registration, insurance, hygiene certs, etc.
    """

    serializer_class = ComplianceDocumentSerializer
    permission_classes = [permissions.IsAuthenticated]

    def get_restaurant(self):
        user = self.request.user
        return getattr(user, "restaurant", None)

    def get_queryset(self):
        restaurant = self.get_restaurant()
        if not restaurant:
            return ComplianceDocument.objects.none()
        qs = ComplianceDocument.objects.filter(restaurant=restaurant).exclude(
            status=ComplianceDocument.STATUS_ARCHIVED
        )
        dtype = self.request.query_params.get("document_type")
        if dtype:
            qs = qs.filter(document_type=dtype)
        urgency = self.request.query_params.get("urgency")
        if urgency in ("expired", "soon", "unset", "critical", "ok"):
            from payroll.services.compliance_documents import document_urgency

            keep_ids = [
                d.id for d in qs if document_urgency(d.expires_at) == urgency
            ]
            qs = qs.filter(id__in=keep_ids)
        return qs.order_by("expires_at", "title")

    def list(self, request, *args, **kwargs):
        qs = self.get_queryset()
        return Response(
            {
                "success": True,
                "count": qs.count(),
                "document_types": [
                    {"id": i, "label": label} for i, label in DOCUMENT_TYPE_CHOICES
                ],
                "documents": [serialize_document(d) for d in qs],
            }
        )

    def retrieve(self, request, *args, **kwargs):
        doc = self.get_object()
        return Response({"success": True, "document": serialize_document(doc)})

    def create(self, request, *args, **kwargs):
        restaurant = self.get_restaurant()
        if not restaurant:
            return Response(
                {"success": False, "error": "No restaurant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        role = (getattr(request.user, "role", "") or "").upper()
        if role not in {
            "SUPER_ADMIN",
            "ADMIN",
            "OWNER",
            "MANAGER",
        }:
            return Response(
                {"success": False, "error": "Permission denied"},
                status=status.HTTP_403_FORBIDDEN,
            )
        data = request.data if isinstance(request.data, dict) else {}
        title = str(data.get("title") or "").strip()
        if not title:
            return Response(
                {"success": False, "error": "title is required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        dtype = str(data.get("document_type") or ComplianceDocument.TYPE_OTHER).upper()
        if dtype not in {c[0] for c in ComplianceDocument.TYPE_CHOICES}:
            dtype = ComplianceDocument.TYPE_OTHER
        expires_raw = data.get("expires_at")
        expires_at = parse_date(str(expires_raw)) if expires_raw else None
        remind = data.get("remind_days_before")
        try:
            remind_days = max(1, min(365, int(remind))) if remind not in (None, "") else 30
        except (TypeError, ValueError):
            remind_days = 30
        doc = ComplianceDocument.objects.create(
            restaurant=restaurant,
            title=title[:255],
            document_type=dtype,
            description=str(data.get("description") or "")[:2000],
            reference_number=str(data.get("reference_number") or "")[:128],
            expires_at=expires_at,
            remind_days_before=remind_days,
            created_by=request.user,
        )
        return Response(
            {"success": True, "document": serialize_document(doc)},
            status=status.HTTP_201_CREATED,
        )

    def partial_update(self, request, *args, **kwargs):
        doc = self.get_object()
        data = request.data if isinstance(request.data, dict) else {}
        if "title" in data and str(data["title"]).strip():
            doc.title = str(data["title"]).strip()[:255]
        if "description" in data:
            doc.description = str(data["description"] or "")[:2000]
        if "reference_number" in data:
            doc.reference_number = str(data["reference_number"] or "")[:128]
        if "document_type" in data:
            dtype = str(data["document_type"] or "").upper()
            if dtype in {c[0] for c in ComplianceDocument.TYPE_CHOICES}:
                doc.document_type = dtype
        if "expires_at" in data:
            raw = data.get("expires_at")
            doc.expires_at = parse_date(str(raw)) if raw else None
            # Renewing clears expired status
            if doc.expires_at and doc.expires_at >= timezone.now().date():
                doc.status = ComplianceDocument.STATUS_ACTIVE
                doc.last_notified_at = None
        if "remind_days_before" in data and data["remind_days_before"] not in (None, ""):
            try:
                doc.remind_days_before = max(1, min(365, int(data["remind_days_before"])))
            except (TypeError, ValueError):
                pass
        if "status" in data:
            st = str(data["status"] or "").upper()
            if st in {
                ComplianceDocument.STATUS_ACTIVE,
                ComplianceDocument.STATUS_EXPIRED,
                ComplianceDocument.STATUS_ARCHIVED,
            }:
                doc.status = st
        doc.save()
        return Response({"success": True, "document": serialize_document(doc)})

    def destroy(self, request, *args, **kwargs):
        doc = self.get_object()
        doc.status = ComplianceDocument.STATUS_ARCHIVED
        doc.save(update_fields=["status", "updated_at"])
        return Response({"success": True, "archived": True})

    @action(detail=False, methods=["post"], url_path="seed")
    def seed(self, request):
        restaurant = self.get_restaurant()
        if not restaurant:
            return Response(
                {"success": False, "error": "No restaurant"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        created = seed_starter_documents(restaurant)
        return Response(
            {
                "success": True,
                "created": len(created),
                "documents": [serialize_document(d) for d in created],
                "message": (
                    f"Added {len(created)} suggested document(s). "
                    "Set expiry dates so Miya can remind you."
                    if created
                    else "Starter documents are already set up."
                ),
            }
        )
