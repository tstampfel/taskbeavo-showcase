#!/bin/sh
# Render pre-deploy command (render.yaml): runs on the new image before it takes traffic,
# and a failure here stops the deploy.
# Migrations use Neon's direct connection when DATABASE_URL_DIRECT is set, because the
# pooled one (PgBouncer, transaction mode) breaks session-level statements.
set -eu

if [ -n "${DATABASE_URL_DIRECT:-}" ]; then
  DATABASE_URL="$DATABASE_URL_DIRECT"
  export DATABASE_URL
fi

python manage.py migrate --noinput
# Production refuses to go live while placeholder legal text is current or scheduled.
exec python manage.py check_legal_documents
