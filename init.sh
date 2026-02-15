#!/bin/sh
set -e

# Kurz warten, bis PostgreSQL bereit ist (depends_on healthcheck kann trotzdem etwas voraus sein)
sleep 2

# Migrationen ausführen
python manage.py migrate --noinput

# Statische Dateien sammeln (für Production)
python manage.py collectstatic --noinput --clear 2>/dev/null || true

# Admin-Benutzer optional anlegen (falls ENV gesetzt)
if [ -n "$ADMIN_EMAIL" ] && [ -n "$ADMIN_PASSWORD" ]; then
python manage.py shell -c "
from accounts.models import User
email = '$ADMIN_EMAIL'
password = '$ADMIN_PASSWORD'
if not User.objects.filter(email=email).exists():
    User.objects.create_superuser(email, password)
    print(f'Admin-Account angelegt: {email}')
else:
    print('Admin-Account existiert bereits.')
"
else
  echo 'ADMIN_EMAIL/ADMIN_PASSWORD nicht gesetzt - kein Admin auto-created.'
fi

# Server starten
exec gunicorn team_todo_web.wsgi:application \
  --bind 0.0.0.0:8000 \
  --workers 2 \
  --timeout 90 \
  --graceful-timeout 30 \
  --access-logfile - \
  --error-logfile -
