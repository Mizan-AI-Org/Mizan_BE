"""Agent endpoints for menu / recipe food-cost (manager copilot)."""
from __future__ import annotations

import logging

from rest_framework import permissions, status
from rest_framework.decorators import api_view, authentication_classes, permission_classes
from rest_framework.response import Response

from .food_cost import compute_food_cost_report

logger = logging.getLogger(__name__)


@api_view(["GET"])
@authentication_classes([])
@permission_classes([permissions.AllowAny])
def agent_food_cost(request):
    """
    GET /api/menu/agent/food-cost/

    Query: restaurant_id (or X-Restaurant-Id), limit (default 25),
           sort=food_cost_pct|margin
    Auth: Bearer agent key or user JWT via _resolve_restaurant_for_agent.
    """
    from scheduling.views_agent import _resolve_restaurant_for_agent

    restaurant, _, err = _resolve_restaurant_for_agent(request)
    if err:
        return Response({"success": False, "error": err["error"]}, status=err["status"])

    try:
        limit = int(request.query_params.get("limit") or request.GET.get("limit") or 25)
    except (TypeError, ValueError):
        limit = 25
    sort = str(request.query_params.get("sort") or request.GET.get("sort") or "food_cost_pct").strip()
    if sort not in ("food_cost_pct", "margin"):
        sort = "food_cost_pct"

    try:
        payload = compute_food_cost_report(restaurant, limit=limit, sort=sort)
    except Exception as e:
        logger.exception("agent_food_cost failed")
        return Response(
            {"success": False, "error": str(e)[:200]},
            status=status.HTTP_500_INTERNAL_SERVER_ERROR,
        )

    items = payload.get("items") or []
    if not items:
        msg = (
            "No recipes with costed ingredients yet — add recipes and ingredient costs "
            "to see food-cost % and margin."
        )
    else:
        top = items[0]
        msg = (
            f"Top food-cost item: {top['name']} at {top['food_cost_pct']}% "
            f"(portion {top['portion_cost']}, sell {top['price']}). "
            f"{payload['total_with_recipes']} recipes costed"
            + (
                f"; avg food cost {payload['avg_food_cost_pct']}%."
                if payload.get("avg_food_cost_pct") is not None
                else "."
            )
        )

    return Response(
        {
            "success": True,
            "message_for_user": msg,
            **payload,
        }
    )
