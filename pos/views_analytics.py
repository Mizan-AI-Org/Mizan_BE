"""
Manager-facing POS analytics endpoints.
Provides today's sales and prep list for next day.
"""
from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework import status
from django.utils.dateparse import parse_date

from .integrations import IntegrationManager


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def sales_today(request):
    """
    Get today's sales summary (or for a specific date).
    Query param: date (YYYY-MM-DD) - optional, defaults to today.
    """
    restaurant = request.user.restaurant
    if not restaurant:
        return Response({"error": "No restaurant associated"}, status=status.HTTP_400_BAD_REQUEST)

    date_str = request.query_params.get("date")
    target_date = parse_date(date_str) if date_str else None

    result = IntegrationManager.get_daily_sales_summary(restaurant, target_date)
    return Response(result)


@api_view(["GET"])
@permission_classes([IsAuthenticated])
def prep_list(request):
    """
    Get prep list / purchase recommendations.
    Query params:
      - date (YYYY-MM-DD): single date, defaults to tomorrow
      - start_date, end_date (YYYY-MM-DD): date range for multi-day prep list
    """
    restaurant = request.user.restaurant
    if not restaurant:
        return Response({"error": "No restaurant associated"}, status=status.HTTP_400_BAD_REQUEST)

    start_str = request.query_params.get("start_date")
    end_str = request.query_params.get("end_date")
    date_str = request.query_params.get("date")

    target_start = parse_date(start_str) if start_str else None
    target_end = parse_date(end_str) if end_str else None
    target_date = parse_date(date_str) if date_str else None

    if target_start and target_end:
        if target_start > target_end:
            return Response({"error": "start_date must be before or equal to end_date"}, status=status.HTTP_400_BAD_REQUEST)
        result = IntegrationManager.generate_prep_list(restaurant, target_date=target_date, target_start_date=target_start, target_end_date=target_end)
    else:
        result = IntegrationManager.generate_prep_list(restaurant, target_date)
    return Response(result)
