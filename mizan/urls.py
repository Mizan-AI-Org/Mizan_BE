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

urlpatterns = [
    path('admin/', admin.site.urls),
    path('api/reporting/', include('reporting.urls')),
    path('api/auth/', include('accounts.urls')),  # Auth endpoints
    path('api/staff/', include('staff.urls')),    # Staff management
    path('api/timeloss/', include('timeclock.urls')),  # Time tracking (using timeclock app)
    path('api/schedule/', include('scheduling.urls')),  # Schedule (using scheduling app)
    path('api/notifications/', include('notifications.urls')), # Notifications
    path('api/chat/', include('chat.urls')), # Chat
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)