#!/bin/sh
set -e
case "$1" in
  web)
    python manage.py migrate --noinput
    python manage.py bootstrap
    exec gunicorn aria2panel.wsgi:application \
      --bind 0.0.0.0:8000 \
      --workers "${GUNICORN_WORKERS:-3}" \
      --timeout "${GUNICORN_TIMEOUT:-60}" \
      --access-logfile -
    ;;
  scheduler)
    # the web container runs the migrations, wait until the schema is there
    until python manage.py migrate --check >/dev/null 2>&1; do
      echo "waiting for database migrations..."
      sleep 3
    done
    exec python manage.py run_scheduler
    ;;
  *)
    exec "$@"
    ;;
esac
