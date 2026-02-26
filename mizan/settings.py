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
DEBUG = config('DEBUG', default=False, cast=bool)
ALLOWED_HOSTS = config('ALLOWED_HOSTS', default='localhost,127.0.0.1,app.heymizan.ai,api.heymizan.ai').split(',')

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
        # logger.info("Firebase Admin SDK initialized successfully.")

    except Exception as e:
        # logger.error(f"Error initializing Firebase Admin SDK: {e}")
        pass


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
FRONTEND_URL = config('FRONTEND_URL', default='http://localhost:8080')

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
# Uses SMTP when EMAIL_HOST is provided (Zoho/Gmail/etc). In DEBUG, defaults to a local
# SMTP sink like Mailhog unless overridden by env vars.
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'

EMAIL_HOST = config('EMAIL_HOST', default='localhost' if DEBUG else 'smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=1025 if DEBUG else 587, cast=int)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')

# TLS vs SSL:
# - Zoho supports TLS on 587 and SSL on 465.
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=(False if DEBUG else True), cast=str_to_bool)
EMAIL_USE_SSL = config('EMAIL_USE_SSL', default=False, cast=str_to_bool)
if EMAIL_USE_SSL:
    EMAIL_USE_TLS = False

# Prevent hanging SMTP connections (seconds). Django's SMTP backend reads this.
EMAIL_TIMEOUT = config('EMAIL_TIMEOUT', default=20, cast=int)

DEFAULT_FROM_EMAIL = config(
    'DEFAULT_FROM_EMAIL',
    default=(EMAIL_HOST_USER if EMAIL_HOST_USER else 'no-reply@mizan.local')
)

if DEBUG:
    # print("Using development email settings", file=sys.stderr)
    # print(f"Email Backend: {EMAIL_HOST}:{EMAIL_PORT} tls={EMAIL_USE_TLS} ssl={EMAIL_USE_SSL}", file=sys.stderr)
    pass



WHATSAPP_ACCESS_TOKEN = config('WHATSAPP_ACCESS_TOKEN', default='')
WHATSAPP_PHONE_NUMBER_ID = config('WHATSAPP_PHONE_NUMBER_ID', default='')
WHATSAPP_API_VERSION = config('WHATSAPP_API_VERSION', default='v22.0')
WHATSAPP_BUSINESS_ACCOUNT_ID = config('WHATSAPP_BUSINESS_ACCOUNT_ID', default='')
WHATSAPP_VERIFY_TOKEN = config('WHATSAPP_VERIFY_TOKEN', default='')
# ONE-TAP activation: digits-only phone for wa.me link (e.g. 212784476751). Agent number for account activation.
WHATSAPP_ACTIVATION_WA_PHONE = config('WHATSAPP_ACTIVATION_WA_PHONE', default='212784476751')
WHATSAPP_INVITATION_FLOW_ID = config('WHATSAPP_INVITATION_FLOW_ID', default=None)
WHATSAPP_TEMPLATE_INVITE = config('WHATSAPP_TEMPLATE_INVITE', default='onboarding_invite_v1')
WHATSAPP_TEMPLATE_SHIFT_ASSIGNED = config('WHATSAPP_TEMPLATE_SHIFT_ASSIGNED', default='')
WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_LANGUAGE = config('WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_LANGUAGE', default='en_US')
WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED = config('WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED', default='')
WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED_LANGUAGE = config('WHATSAPP_TEMPLATE_SHIFT_ASSIGNED_DETAILED_LANGUAGE', default='en_US')
# After staff activation we send this template (Welcome {{1}}, account for {{2}} activated...)
WHATSAPP_TEMPLATE_STAFF_ACTIVATED_WELCOME = config('WHATSAPP_TEMPLATE_STAFF_ACTIVATED_WELCOME', default='staff_activated_welcome')
WHATSAPP_TEMPLATE_STAFF_ACTIVATED_WELCOME_HAS_HEADER = config('WHATSAPP_TEMPLATE_STAFF_ACTIVATED_WELCOME_HAS_HEADER', default=False, cast=str_to_bool)
OPENAI_API_KEY = os.getenv('OPENAI_API_KEY', '')
REDIS_HOST = os.getenv('REDIS_HOST', 'localhost')
CELERY_BROKER_URL = os.getenv('CELERY_BROKER_URL', f'redis://{REDIS_HOST}:6379/0')
CELERY_RESULT_BACKEND = os.getenv('CELERY_RESULT_BACKEND', f'redis://{REDIS_HOST}:6379/0')
CELERY_TASK_ALWAYS_EAGER = config('CELERY_TASK_ALWAYS_EAGER', default=False, cast=str_to_bool)
CELERY_TASK_EAGER_PROPAGATES = True

LUA_API_URL = config('LUA_API_URL', default='https://api.heylua.ai')
LUA_API_KEY = config('LUA_API_KEY', default='')
LUA_AGENT_ID = config('LUA_AGENT_ID', default='')
LUA_WEBHOOK_API_KEY = config('LUA_WEBHOOK_API_KEY', default='')
LUA_USER_EVENTS_WEBHOOK = config('LUA_USER_EVENTS_WEBHOOK', default='')
LUA_USER_AUTHENTICATION_WEBHOOK = config('LUA_USER_AUTHENTICATION_WEBHOOK', default='')

# WhatsApp Invitation Automation (Delegates to Lua Agent by default)
AUTO_WHATSAPP_INVITES = str_to_bool(os.getenv('AUTO_WHATSAPP_INVITES', True))
WHATSAPP_INVITE_DELAY_SECONDS = int(os.getenv('WHATSAPP_INVITE_DELAY_SECONDS', 0))
SUPPORT_CONTACT = os.getenv('SUPPORT_CONTACT', '+212626154332') # Default support contact if needed

# WhatsApp templates (align with Lua/Meta approved names)
WHATSAPP_TEMPLATE_STAFF_CLOCK_IN = config('WHATSAPP_TEMPLATE_STAFF_CLOCK_IN', default='staff_clock_in')
WHATSAPP_TEMPLATE_CLOCK_IN_LOCATION = config('WHATSAPP_TEMPLATE_CLOCK_IN_LOCATION', default='clock_in_location_request')
WHATSAPP_TEMPLATE_CLOCK_IN_SUCCESSFUL = config('WHATSAPP_TEMPLATE_CLOCK_IN_SUCCESSFUL', default='clock_in_success')
# Clock-in window: staff can clock in from X min before shift start until Y min after (prevents early/late clock-in)
CLOCK_IN_WINDOW_MINUTES_BEFORE = int(config('CLOCK_IN_WINDOW_MINUTES_BEFORE', default='30'))
CLOCK_IN_WINDOW_MINUTES_AFTER = int(config('CLOCK_IN_WINDOW_MINUTES_AFTER', default='15'))
# Optional: use approved staff_checklist template for each step (body {{1}} = question; buttons Yes/No/N/A). Empty = use interactive buttons with dynamic task text.
WHATSAPP_TEMPLATE_STAFF_CHECKLIST = config('WHATSAPP_TEMPLATE_STAFF_CHECKLIST', default='staff_checklist')
# Shift review: sent when staff shift ends (body {{1}} = first name; buttons Bad/Decent/Good/Great).
WHATSAPP_TEMPLATE_SHIFT_REVIEW = config('WHATSAPP_TEMPLATE_SHIFT_REVIEW', default='shift_review')
WHATSAPP_TEMPLATE_SHIFT_REVIEW_LANGUAGE = config('WHATSAPP_TEMPLATE_SHIFT_REVIEW_LANGUAGE', default='en_US')

# ---------------------------
# Stripe Configuration
# ---------------------------
STRIPE_SECRET_KEY = config('STRIPE_SECRET_KEY', default='')
STRIPE_PUBLISHABLE_KEY = config('STRIPE_PUBLISHABLE_KEY', default='')
STRIPE_WEBHOOK_SECRET = config('STRIPE_WEBHOOK_SECRET', default='')

# ---------------------------
# Square POS Configuration
# ---------------------------
SQUARE_ENV = config('SQUARE_ENV', default=('sandbox' if DEBUG else 'production'))  # 'sandbox' or 'production'
SQUARE_APPLICATION_ID = config('SQUARE_APPLICATION_ID', default='')
SQUARE_APPLICATION_SECRET = config('SQUARE_APPLICATION_SECRET', default='')
SQUARE_REDIRECT_URI = config('SQUARE_REDIRECT_URI', default='')
SQUARE_SCOPES = config(
    'SQUARE_SCOPES',
    default='PAYMENTS_READ,ORDERS_READ,ITEMS_READ,MERCHANT_PROFILE_READ',
)
SQUARE_API_VERSION = config('SQUARE_API_VERSION', default='2024-01-18')
SQUARE_WEBHOOK_SIGNATURE_KEY = config('SQUARE_WEBHOOK_SIGNATURE_KEY', default='')
SQUARE_WEBHOOK_NOTIFICATION_URL = config('SQUARE_WEBHOOK_NOTIFICATION_URL', default='')
# Optional template for tenant-scoped webhook endpoints, e.g.
# https://api.heymizan.ai/api/pos/webhooks/square/{restaurant_id}/
SQUARE_WEBHOOK_NOTIFICATION_URL_TEMPLATE = config('SQUARE_WEBHOOK_NOTIFICATION_URL_TEMPLATE', default='')


from celery.schedules import crontab

CELERY_BEAT_SCHEDULE = {
    "check_tasks_every_5min": {
        "task": "scheduling.tasks.check_upcoming_tasks",
        "schedule": crontab(minute='*/5'),  # Every 5 min: 30-min shift reminder, 10-min clock-in, checklist, clock-out
    },
    "clock_in_reminders": {
        "task": "scheduling.reminder_tasks.send_clock_in_reminders",
        "schedule": crontab(minute='*/5'),  # Every 5 min (backup path for clock-in reminders)
    },
    "checklist_reminders": {
        "task": "scheduling.reminder_tasks.send_checklist_reminders",
        "schedule": crontab(minute='*/10'),  # Every 10 minutes
    },
    "auto_clock_out_at_shift_end": {
        "task": "scheduling.tasks.auto_clock_out_after_shift_end",
        "schedule": crontab(minute='*'),  # Every minute so staff are clocked out immediately when shift ends
    },
}


from django.utils import timezone

now = timezone.now()
# print("Now:", now.strftime("%Y-%m-%d %H:%M:%S"), file=sys.stderr)

