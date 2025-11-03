from django.urls import path, include
from rest_framework.routers import DefaultRouter
from . import views
from .views import (
    StaffCreateView, CategoryListAPIView, 
    ProductListAPIView, OrderCreateAPIView, OrderDetailAPIView, 
    StaffOrderListAPIView, TableListCreateAPIView, TableDetailAPIView,
    TableAssignOrderAPIView, TableClearOrderAPIView, CategoryDetailAPIView,
    ProductDetailAPIView, TablesNeedingCleaningListAPIView, MarkTableCleanAPIView, RestaurantOrderListAPIView,
    ScheduleViewSet, StaffProfileViewSet, ScheduleChangeViewSet, 
    ScheduleNotificationViewSet, StaffAvailabilityViewSet, PerformanceMetricViewSet
)
from .views_safety import (
    StandardOperatingProcedureViewSet, SafetyChecklistViewSet, ScheduleTaskViewSet,
    SafetyConcernReportViewSet, SafetyRecognitionViewSet
)

router = DefaultRouter()
router.register(r'schedules', ScheduleViewSet)
router.register(r'profiles', StaffProfileViewSet)
router.register(r'schedule-changes', ScheduleChangeViewSet)
router.register(r'notifications', ScheduleNotificationViewSet)
router.register(r'availability', StaffAvailabilityViewSet)
router.register(r'performance', PerformanceMetricViewSet)
router.register(r'sops', StandardOperatingProcedureViewSet, basename='sop')
router.register(r'safety-checklists', SafetyChecklistViewSet, basename='safety-checklist')
router.register(r'schedule-tasks', ScheduleTaskViewSet, basename='schedule-task')
router.register(r'safety-concerns', SafetyConcernReportViewSet, basename='safety-concern')
router.register(r'safety-recognitions', SafetyRecognitionViewSet, basename='safety-recognition')

urlpatterns = [
    path('create/', views.StaffCreateView.as_view(), name='staff-create'),
    path('', views.staff_list, name='staff-list'),
    path('<uuid:user_id>/', views.staff_detail, name='staff-detail'),
    path('dashboard/', views.staff_dashboard, name='staff-dashboard'),
    path('stats/', views.staff_stats, name='staff-stats'),
    path('<uuid:staff_id>/', views.remove_staff, name='remove-staff'),
    path('<uuid:staff_id>/role/', views.update_staff_role, name='update-staff-role'),
    path('categories/', CategoryListAPIView.as_view(), name='category-list'),
    path('categories/<uuid:pk>/', CategoryDetailAPIView.as_view(), name='category-detail'),
    path('products/', ProductListAPIView.as_view(), name='product-list'),
    path('products/<uuid:pk>/', ProductDetailAPIView.as_view(), name='product-detail'),
    path('orders/', OrderCreateAPIView.as_view(), name='order-create'),
    path('orders/<uuid:pk>/', OrderDetailAPIView.as_view(), name='order-detail'),
    path('my-orders/', StaffOrderListAPIView.as_view(), name='my-orders'),
    path('restaurant-orders/', RestaurantOrderListAPIView.as_view(), name='restaurant-orders'),
    path('tables/', TableListCreateAPIView.as_view(), name='table-list-create'),
    path('tables/<uuid:pk>/', TableDetailAPIView.as_view(), name='table-detail'),
    path('tables/<uuid:pk>/assign-order/', TableAssignOrderAPIView.as_view(), name='table-assign-order'),
    path('tables/<uuid:pk>/clear-order/', TableClearOrderAPIView.as_view(), name='table-clear-order'),
    path('tables/needing-cleaning/', TablesNeedingCleaningListAPIView.as_view(), name='tables-needing-cleaning'),
    path('tables/<uuid:pk>/mark-clean/', MarkTableCleanAPIView.as_view(), name='mark-table-clean'),
    path('', include(router.urls)),
]