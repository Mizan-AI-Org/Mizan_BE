"""JWT list endpoint for tenant documents uploaded via Miya."""

from __future__ import annotations

from rest_framework import permissions
from rest_framework.response import Response
from rest_framework.views import APIView

from core.http_caching import json_response_with_cache
from miya.models import TenantDocument
from miya.services.tenant_documents import serialize_tenant_document


class TenantDocumentsListView(APIView):
    """GET /api/dashboard/tenant-documents/"""

    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        restaurant = getattr(request.user, "restaurant", None)
        if not restaurant:
            return Response({"success": False, "error": "No workspace linked"}, status=400)

        limit = min(int(request.query_params.get("limit") or 20), 40)
        q = str(request.query_params.get("q") or "").strip().lower()
        qs = TenantDocument.objects.filter(restaurant=restaurant).order_by("-created_at")

        rows = []
        for doc in qs[: limit * 3]:
            if q and q not in doc.title.lower() and q not in (doc.summary or "").lower():
                continue
            rows.append(serialize_tenant_document(doc))
            if len(rows) >= limit:
                break

        return json_response_with_cache(
            request,
            {"success": True, "count": len(rows), "documents": rows},
            max_age=30,
        )
