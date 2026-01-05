from django.urls import path, include, re_path
from rest_framework.routers import DefaultRouter
from .views import (
    CustomTokenObtainPairView, CustomTokenRefreshView, RegisterView, VerifyEmailView, PasswordResetRequestView,
    PasswordResetConfirmView, RestaurantDetailView, RestaurantUpdateView, StaffInvitationListView, 
    StaffProfileUpdateView, ResendVerificationEmailView, StaffListAPIView,
    LoginView, MeView, InviteStaffView, AcceptInvitationView, StaffPinLoginView, pin_login,
    StaffListView
)
from .views_extended import RestaurantSettingsViewSet, StaffLocationViewSet
from .views_invitations import InvitationViewSet, UserManagementViewSet
from .views_agent import AgentContextView

router = DefaultRouter()
router.register(r'settings', RestaurantSettingsViewSet, basename='settings')
router.register(r'location', StaffLocationViewSet, basename='location')
router.register(r'invitations', InvitationViewSet, basename='invitations')
router.register(r'users', UserManagementViewSet, basename='users')

urlpatterns = [
    path('', include(router.urls)),
    path('token/', CustomTokenObtainPairView.as_view(), name='token_obtain_pair'),
    path('token/refresh/', CustomTokenRefreshView.as_view(), name='token_refresh'),
    path('register/', RegisterView.as_view(), name='register'),
    path('verify-email/', VerifyEmailView.as_view(), name='verify_email'),
    path('resend-verification-email/', ResendVerificationEmailView.as_view(), name='resend_verification_email'),
    path('password-reset-request/', PasswordResetRequestView.as_view(), name='password_reset_request'),
    path('password-reset-confirm/', PasswordResetConfirmView.as_view(), name='password_reset_confirm'),
    path('restaurant/<uuid:pk>/', RestaurantDetailView.as_view(), name='restaurant_detail'),
    path('restaurant/<uuid:pk>/update/', RestaurantUpdateView.as_view(), name='restaurant_update'),
    path('staff/invitations/', StaffInvitationListView.as_view(), name='staff_invitations'),
    path('staff/profile/<uuid:pk>/update/', StaffProfileUpdateView.as_view(), name='staff_profile_update'),
    path('staff/', StaffListAPIView.as_view(), name='staff_list'),
    # path('staff/users/', StaffUsersListView.as_view(), name='staff_users_list'),
    re_path(r'^auth/login/?$', LoginView.as_view(), name='login'),
    # Use class-based view for PIN login (public endpoint)
    re_path(r'^auth/pin-login/?$', StaffPinLoginView.as_view(), name='pin_login'),
    path('auth/me/', MeView.as_view(), name='me'),
    path('staff/invite/', InviteStaffView.as_view(), name='invite_staff'),
    path('staff/accept-invitation/', AcceptInvitationView.as_view(), name='accept_invitation'),
    re_path(r'^staff/login/?$', StaffPinLoginView.as_view(), name='pin_login'),
    path('auth/agent-context/', AgentContextView.as_view(), name='agent_context'),
]
