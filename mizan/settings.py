import os, sys
from pathlib import Path
from decouple import config # type: ignore
from datetime import timedelta

def str_to_bool(value):
    """Convert string to boolean"""
    if isinstance(value, bool):
        return value
    return str(value).lower() in ('true', '1', 'yes', 'on')

STAFF_ROLES_CHOICES = [
    ('SUPER_ADMIN', 'Super Admin'),
    ('ADMIN', 'Admin'),
    ('MANAGER', 'Manager'),
    ('CHEF', 'Chef'),
    ('WAITER', 'Waiter'),
    ('KITCHEN_HELP', 'Kitchen Help'),
    ('BARTENDER', 'Bartender'),
    ('RECEPTIONIST', 'Receptionist'),
    ('CLEANER', 'Cleaner'),
    ('SECURITY', 'Security'),
    ('CASHIER', 'Cashier'),
]
# ---------------------------
# Base
# ---------------------------
BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production!')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = ['localhost', '127.0.0.1', 'app.heymizan.ai', 'api.heymizan.ai']

# ---------------------------
# Installed Apps
# ---------------------------
INSTALLED_APPS = [
    'daphne',
    # Django apps
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'django_filters',

    # Third-party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'channels',
    'drf_spectacular',  # Optional: for API schema and docs
    'notifications.apps.NotificationsConfig',

    # Local apps
    'attendance', # Attendance module app
    'accounts',
    'dashboard',
    'scheduling',
    'timeclock',
    'reporting',
    'staff',
    'chat',
    # 'ai_assistant',  # AI Assistant app (removed)
    'firebase_admin', #  firebase_admin
    'pos',  # Point of Sale app
    'core',  # Core utilities app
    'checklists',  # Checklist management app
    'billing',     # Billing & Subscriptions
    'menu',
    'inventory',
]

# ---------------------------
# Firebase Admin SDK Initialization
# ---------------------------
import json
import firebase_admin # type: ignore
from firebase_admin import credentials # type: ignore

FIREBASE_SERVICE_ACCOUNT_KEY = config('FIREBASE_SERVICE_ACCOUNT_KEY', default='{}')

if not firebase_admin._apps and FIREBASE_SERVICE_ACCOUNT_KEY != '{}':
    try:
        cred = credentials.Certificate(json.loads(FIREBASE_SERVICE_ACCOUNT_KEY))
        firebase_admin.initialize_app(cred)
        print("Firebase Admin SDK initialized successfully.")
    except Exception as e:
        print(f"Error initializing Firebase Admin SDK: {e}")

# ---------------------------
# Middleware
# ---------------------------
MIDDLEWARE = [
    'corsheaders.middleware.CorsMiddleware',                      # MUST be first for CORS
    'django.middleware.security.SecurityMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',       # REQUIRED before auth
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',    # REQUIRED for admin
    'django.contrib.messages.middleware.MessageMiddleware',       # REQUIRED for admin
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

CORS_ALLOW_CREDENTIALS = True

# ---------------------------
# URLs
# ---------------------------
ROOT_URLCONF = 'mizan.urls'
WSGI_APPLICATION = 'mizan.wsgi.application'
ASGI_APPLICATION = 'mizan.asgi.application'

# ---------------------------
# Templates
# ---------------------------
TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [os.path.join(BASE_DIR, 'templates')],
        'APP_DIRS': True,
        'OPTIONS': {
            'context_processors': [
                'django.template.context_processors.debug',
                'django.template.context_processors.request',   # REQUIRED for admin
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
            ],
        },
    },
]


# ---------------------------
# Database Configuration
# ---------------------------
POSTGRES_DB = config('POSTGRES_DB', default=config('DB_NAME', default='mizan'))
POSTGRES_USER = config('POSTGRES_USER', default=config('DB_USER', default='postgres'))
POSTGRES_PASSWORD = config('POSTGRES_PASSWORD', default=config('DB_PASSWORD', default=''))
POSTGRES_HOST = config('POSTGRES_HOST', default=config('DB_HOST', default='localhost'))
POSTGRES_PORT = config('POSTGRES_PORT', default=config('DB_PORT', default='5432'))

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": POSTGRES_DB,
        "USER": POSTGRES_USER,
        "PASSWORD": POSTGRES_PASSWORD,
        "HOST": POSTGRES_HOST,
        "PORT": POSTGRES_PORT,
    }
}


# ---------------------------
# Password validation
# ---------------------------
AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

# ---------------------------
# Internationalization
# ---------------------------
LANGUAGE_CODE = 'en-us'
TIME_ZONE = "Africa/Casablanca"
USE_I18N = True
USE_TZ = True

# ---------------------------
# Static / Media files
# ---------------------------
STATIC_URL = '/static/'
STATIC_ROOT = os.path.join(BASE_DIR, 'staticfiles')

MEDIA_URL = '/media/'
MEDIA_ROOT = os.path.join(BASE_DIR, 'media')

# ---------------------------
# Default primary key field type
# ---------------------------
DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'

# ---------------------------
# Authentication Backend
# ---------------------------
AUTHENTICATION_BACKENDS = [
    'accounts.backends.EmailBackend',
]

# ---------------------------
# REST Framework / JWT
# ---------------------------
REST_FRAMEWORK = {
    'DEFAULT_AUTHENTICATION_CLASSES': (
        'rest_framework_simplejwt.authentication.JWTAuthentication',
    ),
    'DEFAULT_PERMISSION_CLASSES': (
        'rest_framework.permissions.IsAuthenticated',
    ),
    'DEFAULT_RENDERER_CLASSES': [
        'rest_framework.renderers.JSONRenderer',
    ],
    'DEFAULT_PARSER_CLASSES': [
        'rest_framework.parsers.JSONParser',
        'rest_framework.parsers.MultiPartParser',
        'rest_framework.parsers.FormParser',
    ],
    # This tells DRF to use drf-spectacular for its schema
    'DEFAULT_SCHEMA_CLASS': 'drf_spectacular.openapi.AutoSchema',
    'DEFAULT_PAGINATION_CLASS': 'rest_framework.pagination.PageNumberPagination',
    'PAGE_SIZE': 10,
    'PAGE_SIZE_QUERY_PARAM': 'page_size',
    'MAX_PAGE_SIZE': 500,
}

SIMPLE_JWT = {
    'ACCESS_TOKEN_LIFETIME': timedelta(days=30),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=30),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# ---------------------------
# CORS Settings
# ---------------------------
CORS_ALLOWED_ORIGINS = [
    "https://app.heymizan.ai",  # React frontend
    "http://127.0.0.1:8080",    # React frontend alternative
    "http://localhost:8080",    # Vite dev server (added)
    "http://localhost:5173",    # Vite dev server default
    "http://127.0.0.1:5173",    # Vite dev server alternative
    "http://localhost:8000",    # Django backend (for testing)
]
CORS_ALLOW_CREDENTIALS = True
CORS_ALLOW_METHODS = ['DELETE', 'GET', 'OPTIONS', 'PATCH', 'POST', 'PUT']
CORS_ALLOW_HEADERS = [
    'accept',
    'accept-encoding',
    'authorization',
    'content-type',
    'dnt',
    'origin',
    'user-agent',
    'x-csrftoken',
    'x-requested-with',
]

CSRF_TRUSTED_ORIGINS = [
    "https://app.heymizan.ai",
    "http://localhost:8080",
]
# ---------------------------
# Channels (WebSockets)
# ---------------------------
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [(os.getenv('REDIS_HOST', 'localhost'), 6379)],  # ✅ use env var or localhost
        },
    },
}


# ---------------------------
# Custom user model
# ---------------------------
AUTH_USER_MODEL = 'accounts.CustomUser'


# This backend prints the email content directly to your console/terminal
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# For development - use console backend
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# ---------------------------
# Default to local dev URL; can be overridden via environment
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:8081')

# ---------------------------
# Security settings (production)
# ---------------------------
if not DEBUG:
    SECURE_SSL_REDIRECT = config('SECURE_SSL_REDIRECT', default=True, cast=bool)
    SESSION_COOKIE_SECURE = True
    CSRF_COOKIE_SECURE = True
    SECURE_HSTS_SECONDS = 31536000  # 1 year
    SECURE_HSTS_INCLUDE_SUBDOMAINS = True
    SECURE_HSTS_PRELOAD = True
    SECURE_PROXY_SSL_HEADER = ('HTTP_X_FORWARDED_PROTO', 'https')
    SECURE_REFERRER_POLICY = "strict-origin-when-cross-origin"
    SECURE_BROWSER_XSS_FILTER = True
    SECURE_CONTENT_TYPE_NOSNIFF = True

# ---------------------------
# Email Configuration
# ---------------------------
# Use a reliable local SMTP sink (Mailhog) in development, SMTP in production
if DEBUG:
    print("Using development email settings", file=sys.stderr)
    EMAIL_HOST = config('EMAIL_HOST', default='localhost')
    EMAIL_PORT = config('EMAIL_PORT', default=1025, cast=int)
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
    EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=False, cast=str_to_bool)
    
    # Custom logic: if user is provided but host is local, assume Gmail
    if EMAIL_HOST_USER and EMAIL_HOST == 'localhost':
        EMAIL_HOST = 'smtp.gmail.com'
        EMAIL_PORT = 587
        EMAIL_USE_TLS = True
        
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='no-reply@mizan.local')
    print(f"Email Backend: {EMAIL_HOST}:{EMAIL_PORT}", file=sys.stderr)
else:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
    DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=config('EMAIL_HOST_USER', default='no-reply@mizan.local'))


WHATSAPP_ACCESS_TOKEN = config('WHATSAPP_ACCESS_TOKEN', default='')
WHATSAPP_PHONE_NUMBER_ID = config('WHATSAPP_PHONE_NUMBER_ID', default='')
WHATSAPP_API_VERSION = config('WHATSAPP_API_VERSION', default='v22.0')
WHATSAPP_BUSINESS_ACCOUNT_ID = config('WHATSAPP_BUSINESS_ACCOUNT_ID', default='')
WHATSAPP_VERIFY_TOKEN = config('WHATSAPP_VERIFY_TOKEN', default='')
WHATSAPP_INVITATION_FLOW_ID = config('WHATSAPP_INVITATION_FLOW_ID', default=None)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', 'redis://redis:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', 'redis://redis:6379/0')

LUA_API_URL = config('LUA_API_URL', default='https://api.heylua.ai')
LUA_API_KEY = config('LUA_API_KEY', default='')
LUA_AGENT_ID = config('LUA_AGENT_ID', default='')
LUA_WEBHOOK_API_KEY = config('LUA_WEBHOOK_API_KEY', default='')

# WhatsApp Invitation Automation (Delegates to Lua Agent by default)
AUTO_WHATSAPP_INVITES = str_to_bool(os.getenv('AUTO_WHATSAPP_INVITES', True))
WHATSAPP_INVITE_DELAY_SECONDS = int(os.getenv('WHATSAPP_INVITE_DELAY_SECONDS', 0))
SUPPORT_CONTACT = os.getenv('SUPPORT_CONTACT', '+212626154332') # Default support contact if needed

# ---------------------------
# Stripe Configuration
# ---------------------------
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
STRIPE_PUBLISHABLE_KEY = config('STRIPE_PUBLISHABLE_KEY', default='')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET', default='')


from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "check_tasks_every_minute": {
        "task": "scheduling.tasks.check_upcoming_tasks",
        "schedule": 5,
    },
    "shift_reminders_30min": {
        "task": "scheduling.reminder_tasks.send_shift_reminders_30min",
        "schedule": crontab(minute='*/5'),  # Every 5 minutes
    },
    "checklist_reminders": {
        "task": "scheduling.reminder_tasks.send_checklist_reminders",
        "schedule": crontab(minute='*/10'),  # Every 10 minutes
    },
    "clock_in_reminders": {
        "task": "scheduling.reminder_tasks.send_clock_in_reminders",
        "schedule": crontab(minute='*/5'),  # Every 5 minutes
    },
}


from django.utils import timezone

now = timezone.now()
print("Now:", now.strftime("%Y-%m-%d %H:%M:%S"), file=sys.stderr)
