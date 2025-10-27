import os
from pathlib import Path
from decouple import config
from datetime import timedelta

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
# GEOS_LIBRARY_PATH = '/opt/homebrew/lib/libgeos_c.dylib'  
# GDAL_LIBRARY_PATH = '/opt/homebrew/lib/libgdal.dylib'  
GDAL_LIBRARY_PATH = '/usr/lib/aarch64-linux-gnu/libgdal.so'
GEOS_LIBRARY_PATH = '/usr/lib/aarch64-linux-gnu/libgeos_c.so'
# import platform

# if platform.system() == "Darwin":  # macOS
#     GEOS_LIBRARY_PATH = '/opt/homebrew/lib/libgeos_c.dylib'
#     GDAL_LIBRARY_PATH = '/opt/homebrew/lib/libgdal.dylib'
# else:  # Linux / Docker
#     GEOS_LIBRARY_PATH = '/usr/lib/x86_64-linux-gnu/libgeos_c.so'
#     GDAL_LIBRARY_PATH = '/usr/lib/x86_64-linux-gnu/libgdal.so'


SECRET_KEY = config('SECRET_KEY', default='django-insecure-change-this-in-production!')
DEBUG = config('DEBUG', default=True, cast=bool)
ALLOWED_HOSTS = ['localhost', '127.0.0.1']

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

    # Third-party apps
    'rest_framework',
    'rest_framework_simplejwt',
    'corsheaders',
    'channels',
    'drf_spectacular',  # Optional: for API schema and docs

    # Local apps
    'accounts',
    'dashboard',
    'scheduling',
    'timeclock',
    'reporting',
    'menu', # New menu app
    'inventory', # New inventory app
    'staff',
    'notifications',
    'kitchen',
    'chat',
    'ai_assistant',  # AI Assistant app
    'firebase_admin', #  firebase_admin
    'pos',  # Point of Sale app
    'core',  # Core utilities app
]

# ---------------------------
# Firebase Admin SDK Initialization
# ---------------------------
import json
import firebase_admin
from firebase_admin import credentials

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

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("POSTGRES_DB", "mizan_db"),
        "USER": os.getenv("POSTGRES_USER", "aankote"),  # local default
        "PASSWORD": os.getenv("POSTGRES_PASSWORD", ""),  # local default
        "HOST": os.getenv("POSTGRES_HOST", "localhost"),
        "PORT": os.getenv("POSTGRES_PORT", "5432"),
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
    'ACCESS_TOKEN_LIFETIME': timedelta(hours=1),
    'REFRESH_TOKEN_LIFETIME': timedelta(days=1),
    'ROTATE_REFRESH_TOKENS': True,
    'BLACKLIST_AFTER_ROTATION': True,
}

# ---------------------------
# CORS Settings
# ---------------------------
CORS_ALLOWED_ORIGINS = [
    "http://localhost:8080",  # React frontend
    "http://127.0.0.1:8080",  # React frontend alternative
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
    'default': {
        'BACKEND': 'channels_redis.core.RedisChannelLayer',
        'CONFIG': {
            "hosts": [('127.0.0.1', 6379)],
        },
    },
}

# ---------------------------
# Custom user model
# ---------------------------
AUTH_USER_MODEL = 'accounts.CustomUser'


# Email Configuration
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = config('EMAIL_HOST', default='smtp.gmail.com')
EMAIL_PORT = config('EMAIL_PORT', default=587, cast=int)
EMAIL_USE_TLS = config('EMAIL_USE_TLS', default=True, cast=bool)
EMAIL_HOST_USER = config('EMAIL_HOST_USER', default='your-email@example.com')
EMAIL_HOST_PASSWORD = config('EMAIL_HOST_PASSWORD', default='your-app-password')
DEFAULT_FROM_EMAIL = config('DEFAULT_FROM_EMAIL', default='your-email@example.com')

# For development - use console backend
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# ---------------------------
# Security settings (production)
# ---------------------------
# CSRF_COOKIE_SECURE = True
# SESSION_COOKIE_SECURE = True
# SECURE_SSL_REDIRECT = True
# SECURE_HSTS_SECONDS = 31536000
# SECURE_HSTS_INCLUDE_SUBDOMAINS = True
# SECURE_HSTS_PRELOAD = True