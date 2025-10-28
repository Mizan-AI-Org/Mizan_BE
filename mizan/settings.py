import os, sys
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
    'pos',
    'staff',
    'notifications',
    'kitchen',
    'chat',
    'firebase_admin', #  firebase_admin
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


# This backend prints the email content directly to your console/terminal
EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'

# For development - use console backend
# EMAIL_BACKEND = 'django.core.mail.backends.console.EmailBackend'
# ---------------------------
# Security settings (production)
# ---------------------------

# EMAIL Configuration for Production
EMAIL_BACKEND = 'django.core.mail.backends.smtp.EmailBackend'
EMAIL_HOST = 'smtp.gmail.com'
EMAIL_PORT = 587
EMAIL_USE_TLS = True  # For secure connection
EMAIL_HOST_USER = os.getenv('EMAIL_HOST_USER', "ankoteayoub@gmail.com")
EMAIL_HOST_PASSWORD =  os.getenv('EMAIL_HOST_PASSWORD', '')
DEFAULT_FROM_EMAIL = os.getenv("EMAIL_HOST_USER", "ankoteayoub@gmail.com")

# EMAIL_HOST_USER = 'put use email here'
# EMAIL_HOST_PASSWORD = 'put password here'
# How to generate password : go to 
# https://myaccount.google.com/apppasswords?continue=https://myaccount.google.com/&pli=1&rapt=AEjHL4M1Onqr9B9F_jjj5MtH4YxLBet2EYxE2vdE3mltlTFeQRAxGSBbsrepaIfONygqGo3tweBl9cemkEWTtBKQ7DhmmmKP6SAjsnb8O7SX6FWrx_PAEk4
DEFAULT_FROM_EMAIL = os.getenv("EMAIL_HOST_USER", "ankoteayoub@gmail.com")