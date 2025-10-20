from django.urls import path
from .views import ReportListAPIView, ReportGenerateAPIView, ReportDetailAPIView

urlpatterns = [
    path('reports/', ReportListAPIView.as_view(), name='report-list'),
    path('reports/generate/', ReportGenerateAPIView.as_view(), name='report-generate'),
    path('reports/<uuid:pk>/', ReportDetailAPIView.as_view(), name='report-detail'),
]