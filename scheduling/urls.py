from django.urls import path
from . import views

urlpatterns = [
    path('templates/', views.schedule_templates, name='schedule-templates'),
    path('current/', views.current_schedule, name='current-schedule'),
    path('my-schedule/', views.my_schedule, name='my-schedule'),
    path('assign-shift/', views.assign_shift, name='assign-shift'),
]