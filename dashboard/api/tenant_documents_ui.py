"""JWT list endpoint for tenant documents uploaded via Miya."""

from __future__ import annotations

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache
from miya.models import TenantDocument
from miya.services.document_intelligence import document_matches_query, normalize_structured_fields
from miya.services.tenant_documents import serialize_tenant_document


class TenantDocumentsListView(APIView):
    """GET /api/dashboard/tenant-documents/ — structured OCR intelligence for dashboard."""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response({"success": False, "error": "No workspace linked"}, status=400)

        limit = min(int(request.query_params.get("limit") or 20), 40)
        q = str(request.query_params.get("q") or "").strip()
        doc_id = str(request.query_params.get("id") or request.query_params.get("document_id") or "").strip()
        since = str(request.query_params.get("since") or "").strip().lower()

        if doc_id:
            doc = TenantDocument.objects.filter(restaurant=restaurant, id=doc_id).first()
            if not doc:
                return Response({"success": False, "error": "Document not found"}, status=404)
            return Response(
                {
                    "success": True,
                    "document": serialize_tenant_document(doc, include_text=True),
                }
            )

        include_history = str(request.query_params.get("include_history") or "").lower() in (
            "1",
            "true",
            "yes",
        )
        family_id = str(request.query_params.get("document_family_id") or "").strip()

        if family_id:
            from miya.services.document_versioning import get_document_versions

            versions = get_document_versions(str(restaurant.id), family_id)
            if not versions:
                return Response({"success": False, "error": "Document family not found"}, status=404)
            return Response(
                {
                    "success": True,
                    "document_family_id": family_id,
                    "count": len(versions),
                    "documents": [serialize_tenant_document(v) for v in versions],
                }
            )

        qs = TenantDocument.objects.filter(restaurant=restaurant)
        if not include_history:
            qs = qs.filter(is_current=True)
        qs = qs.order_by("-created_at")
        if since in ("yesterday", "hier"):
            from datetime import datetime as dt
            from datetime import timedelta

            from django.utils import timezone

            day = timezone.localdate() - timedelta(days=1)
            start = timezone.make_aware(dt.combine(day, dt.min.time()))
            qs = qs.filter(created_at__gte=start, created_at__lt=start + timedelta(days=1))

        rows = []
        for doc in qs[: limit * 4]:
            structured = getattr(doc, "structured_fields", None) or normalize_structured_fields(
                getattr(doc, "parse_metadata", None),
                category=doc.category,
                title=doc.title,
                summary=doc.summary,
            )
            if not document_matches_query(
                title=doc.title or "",
                summary=doc.summary or "",
                category=doc.category or "",
                extracted_text=doc.extracted_text or "",
                structured=structured,
                q=q,
            ):
                continue
            rows.append(serialize_tenant_document(doc))
            if len(rows) >= limit:
                break

        return json_response_with_cache(
            request,
            {"success": True, "count": len(rows), "documents": rows},
            max_age=30,
        )
