from django.urls import path
from . import views

urlpatterns = [
    path('clock-in/', views.clock_in, name='clock-in'),
    path('clock-out/', views.clock_out, name='clock-out'),
    path('attendance/today/', views.today_attendance, name='today-attendance'),
    path('attendance/staff/<uuid:user_id>/', views.staff_attendance, name='staff-attendance'),
]