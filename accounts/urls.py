from django.urls import path, include
from rest_framework.routers import DefaultRouter
from .views import (
    CustomTokenObtainPairView, CustomTokenRefreshView, RegisterView, VerifyEmailView, PasswordResetRequestView,
    PasswordResetConfirmView, RestaurantDetailView, RestaurantUpdateView, StaffInvitationListView, 
    StaffProfileUpdateView, ResendVerificationEmailView, StaffListAPIView, StaffMemberDetailView,
    LoginView, LogoutView, MeView, InviteStaffView, AcceptInvitationView, StaffPinLoginView, StaffPhoneLoginView, pin_login,
    StaffListView, StaffPasswordResetView, InviteStaffBulkCsvView,
    StaffActivationUploadView, StaffActivationInviteLinkView, StaffActivationPendingListView,
    StaffActivationPendingDeleteView,
    redirect_to_wa_activation,
)
from .views_extended import RestaurantSettingsViewSet, StaffLocationViewSet
from .views_invitations import InvitationViewSet, UserManagementViewSet
from .views_agent import (
    AgentContextView,
    accept_invitation_from_agent,
    get_invitation_by_phone,
    account_activation_from_agent,
    agent_list_failed_invites,
    agent_retry_invite,
    agent_miya_instructions,
)
from .views_staff_report import staff_profile_report_pdf, agent_staff_report_pdf

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
    path('staff/profile/<uuid:pk>/reset-password/', StaffPasswordResetView.as_view(), name='staff_password_reset'),
    path('staff/', StaffListAPIView.as_view(), name='staff_list'),
    path('staff/<uuid:pk>/report/pdf/', staff_profile_report_pdf, name='staff_profile_report_pdf'),
    path('staff/<uuid:pk>/', StaffMemberDetailView.as_view(), name='staff_detail'),
    # path('staff/users/', StaffUsersListView.as_view(), name='staff_users_list'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    # Use class-based view for PIN login (public endpoint)
    path('auth/pin-login/', StaffPinLoginView.as_view(), name='pin_login'),
    path('auth/staff-phone-login/', StaffPhoneLoginView.as_view(), name='staff_phone_login'),
    path('auth/me/', MeView.as_view(), name='me'),
    path('staff/invite/', InviteStaffView.as_view(), name='invite_staff'),
    path('staff/invite-bulk-csv/', InviteStaffBulkCsvView.as_view(), name='invite_staff_bulk_csv'),
    path('staff/activation/upload/', StaffActivationUploadView.as_view(), name='staff_activation_upload'),
    path('staff/activation/invite-link/', StaffActivationInviteLinkView.as_view(), name='staff_activation_invite_link'),
    path('staff/activation/pending/', StaffActivationPendingListView.as_view(), name='staff_activation_pending'),
    path('staff/activation/pending/<uuid:pk>/', StaffActivationPendingDeleteView.as_view(), name='staff_activation_pending_delete'),
    path('go/wa', redirect_to_wa_activation, name='wa_activation_redirect'),
    path('staff/accept-invitation/', AcceptInvitationView.as_view(), name='accept_invitation'),
    path('staff/login/', StaffPinLoginView.as_view(), name='pin_login'),
    path('auth/agent-context/', AgentContextView.as_view(), name='agent_context'),
    
    # Agent Integration
    path('agent/accept-invitation/', accept_invitation_from_agent, name='agent_accept_invitation'),
    path('agent/lookup-invitation/', get_invitation_by_phone, name='agent_lookup_invitation'),
    path('agent/account-activation/', account_activation_from_agent, name='agent_account_activation'),
    path('agent/failed-invites/', agent_list_failed_invites, name='agent_list_failed_invites'),
    path('agent/retry-invite/', agent_retry_invite, name='agent_retry_invite'),
    path('agent/miya-instructions/', agent_miya_instructions, name='agent_miya_instructions'),
    path('agent/staff-report-pdf/', agent_staff_report_pdf, name='agent_staff_report_pdf'),
]