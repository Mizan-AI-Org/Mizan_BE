# Mounted at api/analytics/ so that frontend calls to /api/analytics/staff-performance/ etc. work
from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views_extended import DashboardAnalyticsViewSet

router = DefaultRouter()
router.register(r'', DashboardAnalyticsViewSet, basename='analytics-standalone')

urlpatterns = [
    path('', include(router.urls)),
]
