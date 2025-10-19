from django.urls import path
from . import views
from .views import InviteStaffView

urlpatterns = [
    path('invite/', InviteStaffView.as_view(), name='invite-staff'),
    path('', views.staff_list, name='staff-list'),
    path('<uuid:user_id>/', views.staff_detail, name='staff-detail'),
    path('dashboard/', views.staff_dashboard, name='staff-dashboard'),
    path('stats/', views.staff_stats, name='staff-stats'),
    path('<uuid:staff_id>/', views.remove_staff, name='remove-staff'),
    path('<uuid:staff_id>/role/', views.update_staff_role, name='update-staff-role'),
]