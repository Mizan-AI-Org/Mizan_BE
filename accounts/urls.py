from django.urls import path
from .views import (
    RestaurantOwnerSignupView,
    LoginView,
    AcceptInvitationView,
    LogoutView,
    MeView,
    InviteStaffView,
    StaffListView,
    StaffDetailView,
    RestaurantDetailView,
    StaffPinLoginView,
)

urlpatterns = [
    # AUTH ENDPOINTS
    path('auth/signup/owner/', RestaurantOwnerSignupView.as_view(), name='owner-signup'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/me/', MeView.as_view(), name='me'),
    path('auth/accept-invitation/', AcceptInvitationView.as_view(), name='accept-invitation'),

    # STAFF MANAGEMENT ENDPOINTS
    path('staff/invite/', InviteStaffView.as_view(), name='invite-staff'),
    path('staff/list/', StaffListView.as_view(), name='staff-list'),
    path('staff/<uuid:pk>/role/', StaffDetailView.as_view(), name='staff-detail-role'), # PUT for role update
    path('staff/<uuid:pk>/', StaffDetailView.as_view(), name='staff-detail'), # GET, DELETE
    path('staff/login/', StaffPinLoginView.as_view(), name='staff-pin-login'),
    # RESTAURANT MANAGEMENT ENDPOINTS
    path('restaurant/', RestaurantDetailView.as_view(), name='restaurant-detail'),
]