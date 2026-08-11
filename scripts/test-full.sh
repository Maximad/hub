#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."

export DJANGO_SETTINGS_MODULE="${DJANGO_SETTINGS_MODULE:-config.test_settings}"

database_engine="$({ python - <<'PY'
import django

django.setup()

from django.conf import settings

print(settings.DATABASES["default"]["ENGINE"])
PY
} 2>&1)" || {
    printf 'Unable to load Django settings:\n%s\n' "$database_engine" >&2
    exit 1
}

if [[ "$database_engine" != "django.db.backends.postgresql" ]]; then
    printf 'The full suite requires PostgreSQL; configured engine is %s.\n' "$database_engine" >&2
    exit 2
fi

python manage.py check
python manage.py makemigrations --check --dry-run
python manage.py test "$@"
