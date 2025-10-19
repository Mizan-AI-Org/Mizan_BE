from django.urls import path
from . import views

urlpatterns = [
    # Existing mobile endpoints
    path('clock-in/', views.clock_in, name='clock-in'),
    path('clock-out/', views.clock_out, name='clock-out'),
    path('attendance/today/', views.today_attendance, name='today-attendance'),
    path('attendance/staff/<uuid:user_id>/', views.staff_attendance, name='staff-attendance'),
    
    # NEW web endpoints with geolocation
    path('web-clock-in/', views.web_clock_in, name='web-clock-in'),
    path('web-clock-out/', views.web_clock_out, name='web-clock-out'),
    path('current-session/', views.current_session, name='current-session'),
    path('restaurant-location/', views.restaurant_location, name='restaurant-location'),
    path('verify-location/', views.verify_location, name='verify-location'),
    path('timecards/', views.timecards, name='timecards'),
    path('staff-dashboard/', views.staff_dashboard_data, name='staff-dashboard-data'),
]