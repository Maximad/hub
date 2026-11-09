FROM python:3.12.14-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

COPY requirements.lock /app/
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY . /app/
ARG VCS_REF=unknown
LABEL org.opencontainers.image.revision=$VCS_REF
RUN DJANGO_DEBUG=true MIKROTIK_ENABLED=false python manage.py collectstatic --noinput

CMD ["gunicorn", "config.wsgi:application", "--bind", "0.0.0.0:8000"]

