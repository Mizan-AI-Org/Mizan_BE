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
    ('CHEF', 'Chef'),
    ('WAITER', 'Waiter'),
    ('CLEANER', 'Cleaner'),
    ('CASHIER', 'Cashier'),
]
# ---------------------------
# Base
# ---------------------------
BASE_DIR = Path(__file__).resolve().parent.parent


SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production!')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = ['localhost', '127.0.0.1', '0.0.0.0']

# ---------------------------
# Installed Apps
# ---------------------------
INSTALLED_APPS = [
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
    'menu', # New menu app
    'inventory', # New inventory app
    'staff',
    # 'notifications',
    'kitchen',
    'chat',
    # 'ai_assistant',  # AI Assistant app (removed)
    'firebase_admin', #  firebase_admin
    'pos',  # Point of Sale app
    'core',  # Core utilities app
    'checklists',  # Checklist management app
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
# Database (PostgreSQL)
# # ---------------------------
# DATABASES = {
#     'default': {
#         'ENGINE': 'django.db.backends.postgresql',
#         'NAME': config('DB_NAME', default='mizan_db2'),
#         'USER': config('DB_USER', default='mizan_user'),
#         'PASSWORD': config('DB_PASSWORD', default='mizan_password123'),
#         'HOST': config('DB_HOST', default='localhost'),
#         'PORT': config('DB_PORT', default='5432'),
#     }
# }

USE_SQLITE = os.getenv("USE_SQLITE", "0") in ["1", "true", "True"]

DATABASES = (
    {
        "default": {
            "ENGINE": "django.db.backends.sqlite3",
            "NAME": BASE_DIR / "db.sqlite3",
        }
    }
    if USE_SQLITE
    else {
        "default": {
            "ENGINE": "django.db.backends.postgresql",
            "NAME": os.getenv("POSTGRES_DB", "mizan_db"),
            "USER": os.getenv("POSTGRES_USER", "aankote"),
            "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),
            "HOST": os.getenv("POSTGRES_HOST", "localhost"),
            "PORT": os.getenv("POSTGRES_PORT", "5432"),
        }
    }
)


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
TIME_ZONE = 'UTC'
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
    "http://localhost:8080",  # React frontend
    "http://127.0.0.1:8080",  # React frontend alternative
    "http://localhost:5173",  # Vite dev server default
    "http://127.0.0.1:5173",  # Vite dev server alternative
    "http://localhost:8000",  # Django backend (for testing)
    "http://127.0.0.1:8000",  # Django backend alternative
]

CORS_ALLOW_ALL_ORIGINS = True  # ⚠️ development only
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

# ---------------------------
# Channels (WebSockets)
# ---------------------------
CHANNEL_LAYERS = {
    "default": {
        "BACKEND": "channels_redis.core.RedisChannelLayer",
        "CONFIG": {
            "hosts": [("redis", 6379)],  # ✅ use the docker service name here
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
# Security settings (production)
# ---------------------------
# Default to local dev URL; can be overridden via environment
FRONTEND_URL = os.getenv('FRONTEND_URL', 'http://localhost:8081')

# ---------------------------
# Email Configuration
# ---------------------------
# Use a reliable local SMTP sink (Mailhog) in development, SMTP in production
if DEBUG:
    print("Using development email settings", file=sys.stderr)
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('DEV_EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.getenv('DEV_EMAIL_PORT', '1025'))
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = os.getenv('DEV_EMAIL_USER', '')
    EMAIL_HOST_PASSWORD = os.getenv('DEV_EMAIL_PASSWORD', '')
    DEFAULT_FROM_EMAIL = os.getenv('DEFAULT_FROM_EMAIL', 'no-reply@mizan.local')
    print("DEV_EMAIL_USER:", EMAIL_HOST, file=sys.stderr)  
else:
    EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
    EMAIL_HOST = os.getenv('EMAIL_HOST', 'smtp.gmail.com')
    EMAIL_PORT = int(os.getenv('EMAIL_PORT', '587'))
    EMAIL_USE_TLS = True
    EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='')
    EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='')
    DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default=config('EMAIL_HOST_USER', default='no-reply@mizan.local'))


# WHATSAPP_ACCESS_TOKEN = os.getenv('WHATSAPP_ACCESS_TOKEN', 'EAAcJkGF80TQBQPKRkLsk2GFkybUMUjzQ9WHNX07ifjFB9KUwDBoV2CcRZAWZC35Q9EsMZCLv5RBZAOT6qzBACYBhX0Q3tq0aZCZCUBCiBZAxTTsNTi5ZAkB2ObE2AI6RoneYPqwTj39ZCtEdI1AZCMz8KFUnDe5US20wbL0wNgL3cSyUeBir9skpzbv4ZAidImn8keZAaKg10pIvlwQaJ2AcrhaXnpObgk04V9nUe9C1ZAOZBjsQlRF8ChI9yoaqYEIl1ofO3hROzmpBCUDzSFjH64zY0qxrmt')
WHATSAPP_ACCESS_TOKEN="EAAcJkGF80TQBQFE0fW3HpebOMjQ3fNY3Tidt7d1K3q6ItZAAAtrLK8KH48EE99EPmKGZAtXZCGdJnfLgZCIxNhPor2U7ReqWfBCQQvR8qZBvGKxzf8B8BGnVOzQO7rlWGxjfKzZBfomLexM1cr9IcGpTUVLNyZBLloF9ZAfekv4q2s7OTOwmduzweNmGI1JBOxkfkfu6Qi67qz7kHBbi8GIXZBWoKlOFVfo9vgjVtuEKbUwB3kN4NVWICYQHqkVcTFoZACN4PTn4TKYKla4vE0qe0pEYxrUQZDZD"
WHATSAPP_PHONE_NUMBER_ID= os.getenv('WHATSAPP_PHONE_NUMBER_ID', '')
WHATSAPP_API_VERSION= os.getenv('WHATSAPP_API_VERSION', 'v22.0')
# # EMAIL Configuration for Production
# EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
# EMAIL_HOST = 'smtp.gmail.com'
# EMAIL_PORT = 587
# EMAIL_USE_TLS = True  # For secure connection
# EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', "jarjuadama101@gmail.com")
# EMAIL_HOST_PASSWORD =  os.getenv('EMAIL_HOST_PASSWORD', '')
# DEFAULT_FROM_EMAIL = os.getenv("DEFAULT_FROM_EMAIL", "jarjuadama101@gmail.com")
