# from django.urls import path
# from . import views 
# from .views import RestaurantOwnerSignupView, LoginView, InviteStaffView, AcceptInvitationView

# urlpatterns = [
#     path('pin-login/', views.pin_login, name='pin-login'),
#     path('profile/', views.user_profile, name='user-profile'),
#     path('staff/', views.staff_list, name='staff-list'),
#     path('staff/<uuid:user_id>/', views.staff_detail, name='staff-detail'),
#     path('restaurant/location/', views.restaurant_location, name='restaurant-location'),
#     path('restaurant/update-location/', views.update_restaurant_location, name='update-restaurant-location'),
#     path('auth/signup/owner/', RestaurantOwnerSignupView.as_view(), name='owner-signup'),
#     path('auth/login/', LoginView.as_view(), name='login'),
#     path('staff/invite/', InviteStaffView.as_view(), name='invite-staff'),
#     path('staff/accept-invitation/', AcceptInvitationView.as_view(), name='accept-invitation'),
# ]
from django.urls import path
from . import views
from .views import RestaurantOwnerSignupView, LoginView, AcceptInvitationView, LogoutView

urlpatterns = [
    # Auth endpoints
    path('signup/owner/', RestaurantOwnerSignupView.as_view(), name='owner-signup'),
    path('login/', LoginView.as_view(), name='login'),
    path('logout/', LogoutView.as_view(), name='logout'),
    path('accept-invitation/', AcceptInvitationView.as_view(), name='accept-invitation'),
    
    # User management
    path('profile/', views.user_profile, name='user-profile'),
    path('me/', views.user_profile, name='current-user'),
    
    # Staff management (move these to staff app later)
    
    # Restaurant
    path('restaurant/location/', views.restaurant_location, name='restaurant-location'),
    path('restaurant/update-location/', views.update_restaurant_location, name='update-restaurant-location'),
    
    # PIN login 
    path('pin-login/', views.pin_login, name='pin-login'),
]