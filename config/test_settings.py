from .settings import *  # noqa: F403,F401

# Tests deliberately inherit the production PostgreSQL database configuration.
# Several application constraints and concurrency guarantees cannot be exercised
# faithfully by SQLite, so a lightweight SQLite test fallback is not supported.
STORAGES = {
    'default': {'BACKEND': 'django.core.files.storage.FileSystemStorage'},
    'staticfiles': {'BACKEND': 'django.contrib.staticfiles.storage.StaticFilesStorage'},
}
PASSWORD_HASHERS = ['django.contrib.auth.hashers.MD5PasswordHasher']
