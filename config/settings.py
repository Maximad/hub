from pathlib import Path
import os

from django.core.exceptions import ImproperlyConfigured

BASE_DIR = Path(__file__).resolve().parent.parent


def optional_bool_env(name):
    """Return an optional, strictly parsed boolean environment setting."""
    value = os.getenv(name)
    if value is None:
        return None
    normalized = value.strip().lower()
    if normalized in {'1', 'true', 'yes', 'on'}:
        return True
    if normalized in {'0', 'false', 'no', 'off'}:
        return False
    raise ValueError(
        f'{name} must be one of: 1, true, yes, on, 0, false, no, off'
    )


def bool_env(name, default):
    """Return a strictly parsed boolean environment setting."""
    value = optional_bool_env(name)
    return default if value is None else value


def non_negative_int_env(name, default):
    """Return a non-negative integer environment setting."""
    raw_value = os.getenv(name, str(default))
    try:
        value = int(raw_value)
    except ValueError as exc:
        raise ImproperlyConfigured(f'{name} must be an integer') from exc
    if value < 0:
        raise ImproperlyConfigured(f'{name} must be zero or greater')
    return value


for _setting_name in (
    'POSTING_LEDGER_WRITES_ENABLED',
    'POSTING_DUAL_READ_ENABLED',
    'POSTING_REPORT_READS_ENABLED',
):
    _setting_value = optional_bool_env(_setting_name)
    if _setting_value is not None:
        globals()[_setting_name] = _setting_value

DEBUG = bool_env('DJANGO_DEBUG', False)
SECRET_KEY = os.getenv('DJANGO_SECRET_KEY', '').strip()
_SECRET_KEY_PLACEHOLDERS = {
    '',
    'change-me-in-production',
    'change-this-in-production',
    'replace-me',
}
if not DEBUG and (
    SECRET_KEY.lower() in _SECRET_KEY_PLACEHOLDERS
    or SECRET_KEY.lower().startswith('django-insecure-')
):
    raise ImproperlyConfigured(
        'DJANGO_SECRET_KEY must be set to a non-placeholder value when DJANGO_DEBUG=False'
    )
# A deterministic key is acceptable only for local debug mode. Production exits above.
if not SECRET_KEY:
    SECRET_KEY = 'debug-only-not-for-production'
ALLOWED_HOSTS = [host.strip() for host in os.getenv('DJANGO_ALLOWED_HOSTS', '127.0.0.1,localhost').split(',') if host.strip()]
RESERVATION_DEFAULT_DURATION_MINUTES = non_negative_int_env('RESERVATION_DEFAULT_DURATION_MINUTES', 120)
CSRF_TRUSTED_ORIGINS = [
    origin.strip()
    for origin in os.environ.get("DJANGO_CSRF_TRUSTED_ORIGINS", "").split(",")
    if origin.strip()
]
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")
USE_X_FORWARDED_HOST = True
SECURE_SSL_REDIRECT = bool_env('DJANGO_SECURE_SSL_REDIRECT', not DEBUG)
SESSION_COOKIE_SECURE = bool_env('DJANGO_SESSION_COOKIE_SECURE', not DEBUG)
CSRF_COOKIE_SECURE = bool_env('DJANGO_CSRF_COOKIE_SECURE', not DEBUG)
SECURE_HSTS_SECONDS = non_negative_int_env(
    'DJANGO_SECURE_HSTS_SECONDS', 0 if DEBUG else 3600
)
SECURE_HSTS_INCLUDE_SUBDOMAINS = False
SECURE_HSTS_PRELOAD = False
# These deploy checks recommend a broader HSTS scope than this staged rollout.
# They are deliberately silenced until the documented subdomain audit is complete.
SILENCED_SYSTEM_CHECKS = ['security.W005', 'security.W021']
SECURE_CONTENT_TYPE_NOSNIFF = bool_env('DJANGO_SECURE_CONTENT_TYPE_NOSNIFF', True)
SECURE_REFERRER_POLICY = os.getenv(
    'DJANGO_SECURE_REFERRER_POLICY', 'strict-origin-when-cross-origin'
).strip()
X_FRAME_OPTIONS = os.getenv('DJANGO_X_FRAME_OPTIONS', 'DENY').strip().upper()

INSTALLED_APPS = [
    'django.contrib.admin',
    'django.contrib.auth',
    'django.contrib.contenttypes',
    'django.contrib.sessions',
    'django.contrib.messages',
    'django.contrib.staticfiles',
    'core',
    'accounts',
    'catalog',
    'locations',
    'orders',
    'payments',
    'members',
    'internet',
    'reports',
    'audit',
    'reservations',
    'events',
    'vendors',
]

MIDDLEWARE = [
    'django.middleware.security.SecurityMiddleware',
    'whitenoise.middleware.WhiteNoiseMiddleware',
    'django.contrib.sessions.middleware.SessionMiddleware',
    'django.middleware.locale.LocaleMiddleware',
    'django.middleware.common.CommonMiddleware',
    'django.middleware.csrf.CsrfViewMiddleware',
    'django.contrib.auth.middleware.AuthenticationMiddleware',
    'django.contrib.messages.middleware.MessageMiddleware',
    'core.middleware.table_visit_guard.TableVisitGuardMiddleware',
    'django.middleware.clickjacking.XFrameOptionsMiddleware',
]

ROOT_URLCONF = 'config.urls'

TEMPLATES = [
    {
        'BACKEND': 'django.template.backends.django.DjangoTemplates',
        'DIRS': [BASE_DIR / 'templates'],
        'APP_DIRS': True,
        'OPTIONS': {
            'builtins': ['core.templatetags.hub_numbers'],
            'context_processors': [
                'django.template.context_processors.request',
                'django.contrib.auth.context_processors.auth',
                'django.contrib.messages.context_processors.messages',
                'core.context_processors.system_settings',
            ],
        },
    },
]

WSGI_APPLICATION = 'config.wsgi.application'

DATABASES = {
    'default': {
        'ENGINE': 'django.db.backends.postgresql',
        'NAME': os.getenv('POSTGRES_DB', 'hub'),
        'USER': os.getenv('POSTGRES_USER', 'hub'),
        'PASSWORD': os.getenv('POSTGRES_PASSWORD', 'hub'),
        'HOST': os.getenv('POSTGRES_HOST', 'db'),
        'PORT': os.getenv('POSTGRES_PORT', '5432'),
    }
}

AUTH_PASSWORD_VALIDATORS = [
    {'NAME': 'django.contrib.auth.password_validation.UserAttributeSimilarityValidator'},
    {'NAME': 'django.contrib.auth.password_validation.MinimumLengthValidator'},
    {'NAME': 'django.contrib.auth.password_validation.CommonPasswordValidator'},
    {'NAME': 'django.contrib.auth.password_validation.NumericPasswordValidator'},
]

LANGUAGE_CODE = 'ar'
LANGUAGES = [
    ('ar', 'العربية'),
    ('en', 'English'),
]
TIME_ZONE = 'Asia/Damascus'
USE_I18N = True
USE_TZ = True

STATIC_URL = '/static/'
STATIC_ROOT = BASE_DIR / 'staticfiles'
STATICFILES_DIRS = [BASE_DIR / 'static']
STATICFILES_STORAGE = 'whitenoise.storage.CompressedManifestStaticFilesStorage'
MEDIA_URL = '/media/'
MEDIA_ROOT = Path(os.environ.get('DJANGO_MEDIA_ROOT', BASE_DIR / 'media'))
MEDIA_ASSET_MAX_UPLOAD_SIZE = int(os.environ.get('MEDIA_ASSET_MAX_UPLOAD_SIZE', 10 * 1024 * 1024))
MEDIA_ASSET_ALLOWED_EXTENSIONS = {ext.strip().lower() for ext in os.environ.get('MEDIA_ASSET_ALLOWED_EXTENSIONS', 'jpg,jpeg,png,webp,gif,pdf').split(',') if ext.strip()}
STORAGES = {
    'default': {
        'BACKEND': 'django.core.files.storage.FileSystemStorage',
    },
    'staticfiles': {
        'BACKEND': 'whitenoise.storage.CompressedManifestStaticFilesStorage',
    },
}

DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'
AUTH_USER_MODEL = 'accounts.User'

LOGIN_URL = '/admin/login/'
LOGIN_REDIRECT_URL = '/staff/'
LOGOUT_REDIRECT_URL = '/admin/login/'

# Keep handled request failures visible to the container runtime without
# enabling DEBUG or including request bodies, cookies, or session data.
LOGGING = {
    'version': 1,
    'disable_existing_loggers': False,
    'handlers': {
        'console': {
            'class': 'logging.StreamHandler',
            'stream': 'ext://sys.stderr',
        },
    },
    'loggers': {
        'django.request': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        'django.server': {
            'handlers': ['console'],
            'level': 'ERROR',
            'propagate': False,
        },
        # CsrfViewMiddleware logs only the sanitized rejection reason and path;
        # it does not include cookies, submitted data, or token values.
        'django.security.csrf': {
            'handlers': ['console'],
            'level': 'WARNING',
            'propagate': False,
        },
    },
}

# Persistent membership recognition (raw credentials are never stored server-side).
MEMBER_DEVICE_COOKIE_NAME = os.getenv('MEMBER_DEVICE_COOKIE_NAME', 'hub_member_device')
MEMBER_DEVICE_COOKIE_AGE = int(os.getenv('MEMBER_DEVICE_COOKIE_AGE', 60 * 60 * 24 * 365))
MEMBER_ACTIVATION_TOKEN_AGE = int(os.getenv('MEMBER_ACTIVATION_TOKEN_AGE', 60 * 60 * 24 * 7))
MEMBER_DEVICE_COOKIE_SECURE = os.getenv('MEMBER_DEVICE_COOKIE_SECURE', str(not DEBUG)).lower() == 'true'
PUBLIC_BASE_URL = os.getenv('PUBLIC_BASE_URL', '')

# True background notifications are opt-in and disabled until a worker and
# browser subscription flow are deployed. VAPID private material is server-only.
PUSH_NOTIFICATIONS_ENABLED = bool_env('PUSH_NOTIFICATIONS_ENABLED', False)
PUSH_PROVIDER = os.getenv('PUSH_PROVIDER', 'webpush').strip().lower()
VAPID_PUBLIC_KEY = os.getenv('VAPID_PUBLIC_KEY', '').strip()
VAPID_PRIVATE_KEY = os.getenv('VAPID_PRIVATE_KEY', '').strip()
VAPID_SUBJECT = os.getenv('VAPID_SUBJECT', '').strip()
PUSH_HTTP_TIMEOUT_SECONDS = float(os.getenv('PUSH_HTTP_TIMEOUT_SECONDS', '10'))
PUSH_ENDPOINT_ALLOWED_HOSTS = tuple(
    host.strip().lower()
    for host in os.getenv(
        'PUSH_ENDPOINT_ALLOWED_HOSTS',
        'fcm.googleapis.com,updates.push.services.mozilla.com,'
        'push.services.mozilla.com,web.push.apple.com,.notify.windows.com',
    ).split(',')
    if host.strip()
)

# A second, manager-level user must approve transfers at or above this amount.
TRANSFER_APPROVAL_LIMIT_SYP = int(os.getenv('TRANSFER_APPROVAL_LIMIT_SYP', '50000'))

# Manual networking remains the safe default. These placeholders contain no secrets.
MIKROTIK_ENABLED = os.getenv('MIKROTIK_ENABLED', 'false').lower() == 'true'
MIKROTIK_BASE_URL = os.getenv('MIKROTIK_BASE_URL', os.getenv('MIKROTIK_HOST', ''))
MIKROTIK_USERNAME = os.getenv('MIKROTIK_USERNAME', '')
MIKROTIK_PASSWORD = os.getenv('MIKROTIK_PASSWORD', '')
MIKROTIK_VERIFY_TLS = os.getenv('MIKROTIK_VERIFY_TLS', 'true').lower() == 'true'
MIKROTIK_CA_FILE = os.getenv('MIKROTIK_CA_FILE', '')
MIKROTIK_CONNECT_TIMEOUT = float(os.getenv('MIKROTIK_CONNECT_TIMEOUT', '5'))
MIKROTIK_READ_TIMEOUT = float(os.getenv('MIKROTIK_READ_TIMEOUT', os.getenv('MIKROTIK_TIMEOUT', '10')))
MIKROTIK_HOTSPOT_SERVER = os.getenv('MIKROTIK_HOTSPOT_SERVER', '')
MIKROTIK_HOTSPOT_LOGIN_URL = os.getenv('MIKROTIK_HOTSPOT_LOGIN_URL', '').strip()
MIKROTIK_USER_PREFIX = os.getenv('MIKROTIK_USER_PREFIX', 'hub-')
MIKROTIK_DEFAULT_PROFILE = os.getenv('MIKROTIK_DEFAULT_PROFILE', '')
MIKROTIK_CUSTOMER_PROFILE_CODE = os.getenv('MIKROTIK_CUSTOMER_PROFILE_CODE', 'fast').strip() or 'fast'
MIKROTIK_CREDENTIAL_KEY = os.getenv('MIKROTIK_CREDENTIAL_KEY', '')

# Currency safety values are reporting values in the new Syrian pound.  They are
# review levels, not transaction limits, and may be overridden per operation.
CURRENCY_RATE_MAX_AGE_DAYS = int(os.getenv('CURRENCY_RATE_MAX_AGE_DAYS', '3'))
CURRENCY_RISK_THRESHOLDS = {
    'default': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
    'product': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
    'payment': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
    'purchase': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
    'expense': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
    'transfer': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
    'cash_closing': {'warning': '5000', 'acknowledgment': '10000', 'manager': '50000'},
}
