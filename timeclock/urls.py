from django.urls import path
from . import views
from inventory.views_agent_morocco import agent_open_cash_session, agent_close_cash_session

urlpatterns = [
    # Existing mobile endpoints
    path('clock-in/', views.clock_in, name='clock-in'),
    path('clock-out/', views.clock_out, name='clock-out'),
    path('break/start/', views.start_break, name='start-break'),
    path('break/end/', views.end_break, name='end-break'),
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
    path('attendance-history/', views.attendance_history, name='my-attendance-history'),
    path('attendance-history/<uuid:user_id>/', views.attendance_history, name='staff-attendance-history'),

    # Manager override (clock-in/out for staff who lost phone)
    path('staff/<uuid:staff_id>/manager-clock-in/', views.manager_clock_in, name='manager-clock-in'),
    path('staff/<uuid:staff_id>/manager-clock-out/', views.manager_clock_out, name='manager-clock-out'),

    # Agent endpoints
    path('agent/clock-in/', views.agent_clock_in, name='agent-clock-in'),
    path('agent/clock-in-by-phone/', views.agent_clock_in_by_phone, name='agent-clock-in-by-phone'),
    path('agent/clock-out-by-phone/', views.agent_clock_out_by_phone, name='agent-clock-out-by-phone'),
    path('agent/clock-out/', views.agent_clock_out, name='agent-clock-out'),
    path('agent/attendance-report/', views.agent_attendance_report, name='agent-attendance-report'),

    # Cash reconciliation (Miya)
    path('agent/cash/open/', agent_open_cash_session, name='agent-cash-open'),
    path('agent/cash/close/', agent_close_cash_session, name='agent-cash-close'),
]