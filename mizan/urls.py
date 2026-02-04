"""
URL configuration for mizan project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/4.2/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
from django.contrib import admin
from django.urls import path, include
from django.conf import settings
from django.conf.urls.static import static
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView

from rest_framework.routers import DefaultRouter

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/analytics/', include('dashboard.urls_analytics')),
    path('api/', include('accounts.urls')),
    path('api/dashboard/', include('dashboard.urls')),
    path('api/attendance/', include('attendance.urls')),
    path('api/menu/', include('menu.urls')),
    path('api/inventory/', include('inventory.urls')),
    path('api/reporting/', include('reporting.urls')),
    path('api/timeclock/', include('timeclock.urls')),
    path('api/scheduling/', include('scheduling.urls')),
    path('api/staff/', include('staff.urls')),
    path('api/notifications/', include('notifications.urls')),
    path('api/pos/', include('pos.urls')),
    path('', include('checklists.urls')),  # Checklist management URLs
    path('api/billing/', include('billing.urls')),
    # AI Assistant routes removed
    path('api/attendance/', include('attendance.urls')),  # Attendance module URLs


    # SWAGGER URLS
    path('api/schema/', SpectacularAPIView.as_view(), name='schema'),
    path('api/swagger-ui/', SpectacularSwaggerView.as_view(url_name='schema'), name='swagger-ui'),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
