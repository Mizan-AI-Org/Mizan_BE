from django.urls import path
from . import views

urlpatterns = [
    path('pin-login/', views.pin_login, name='pin-login'),
    path('profile/', views.user_profile, name='user-profile'),
    path('staff/', views.staff_list, name='staff-list'),
    path('staff/<uuid:user_id>/', views.staff_detail, name='staff-detail'),
    path('restaurant/location/', views.restaurant_location, name='restaurant-location'),
    path('restaurant/update-location/', views.update_restaurant_location, name='update-restaurant-location'),
]