#!/bin/sh
# Smoke test for backend/Dockerfile and docker-compose.yml. Run from the repo root:
#   sh docker/smoke-test.sh
# Builds the production image, starts the compose stack, checks /healthz, Celery and the
# stripe-cli key guard, runs the backend tests in the container, then takes the stack
# down with `docker compose down` (the database volume is kept).
set -eu

# Always take the stack down, also when a check fails (never -v: keep the database volume).
cleanup() {
  docker rm -f taskbeavo-smoke-web >/dev/null 2>&1 || true
  docker compose down >/dev/null 2>&1 || true
}
trap cleanup EXIT

fail() {
  echo "FAIL: $*" >&2
  exit 1
}

echo "== production image"
docker build -q -t taskbeavo-backend backend/ >/dev/null
[ "$(docker run --rm taskbeavo-backend id -u)" = "10001" ] || fail "image does not run as uid 10001"
docker run --rm taskbeavo-backend python -c \
  "from django.contrib.gis import gdal, geos; print('GDAL', gdal.gdal_version().decode(), 'GEOS', geos.geos_version().decode())" \
  || fail "GDAL/GEOS do not load"
docker run --rm taskbeavo-backend test -f /app/staticfiles/admin/css/base.css \
  || fail "collectstatic did not run at build time"
[ "$(docker run --rm taskbeavo-backend printenv DJANGO_SETTINGS_MODULE)" = "config.settings.production" ] \
  || fail "image does not default to production settings"
if docker run --rm taskbeavo-backend python -c "import django; django.setup()" >/dev/null 2>&1; then
  fail "image starts without SECRET_KEY, DATABASE_URL and REDIS_URL"
fi
docker run --rm taskbeavo-backend test -f /app/scripts/predeploy.sh \
  || fail "the Render pre-deploy script is not in the image"
docker run --rm taskbeavo-backend sh -c 'test ! -e /app/.env && test ! -e /app/.venv' \
  || fail ".env or a host .venv leaked into the image"

echo "== stripe-cli refuses live keys and exits cleanly without a key"
for key in sk_live_not_a_real_key rk_live_not_a_real_key; do
  if docker compose run --rm --no-deps -e STRIPE_SECRET_KEY="$key" stripe-cli >/dev/null 2>&1; then
    fail "stripe-cli started with a live key"
  fi
done
# The empty override wins over backend/.env, so this never starts a real `stripe listen`.
docker compose run --rm --no-deps -e STRIPE_SECRET_KEY= stripe-cli >/dev/null 2>&1 \
  || fail "stripe-cli without a key did not exit cleanly"
# A test-shaped key gets past the guard to `stripe listen`, which then fails only on the
# fake key, never on its own arguments (stripe-cli 1.55 refused a listen without --events).
listen=$(docker compose run --rm --no-deps -e STRIPE_SECRET_KEY=sk_test_not_a_real_key stripe-cli 2>&1 || true)
case "$listen" in
  *"must specify events"*|*"unknown flag"*) fail "stripe-cli rejects the listen arguments" ;;
esac

echo "== compose stack"
docker compose up -d --build --wait postgis redis django celery
status=$(curl -s -o /dev/null -w '%{http_code}' http://localhost:${DJANGO_HOST_PORT:-8000}/healthz)
[ "$status" = "200" ] || fail "/healthz returned $status"
# stripe-cli forwards to django:8000, so Django must accept that Host header.
status=$(curl -s -o /dev/null -w '%{http_code}' -H 'Host: django:8000' http://localhost:${DJANGO_HOST_PORT:-8000}/healthz)
[ "$status" = "200" ] || fail "/healthz with Host django:8000 returned $status"
docker compose exec -T celery sh -c 'celery -A config inspect ping -d "celery@$HOSTNAME" --timeout 10' >/dev/null \
  || fail "celery worker does not answer"

echo "== production image serves /healthz with its default gunicorn command"
network=$(docker inspect -f '{{range $name, $_ := .NetworkSettings.Networks}}{{$name}}{{end}}' \
  "$(docker compose ps -q django)")
docker run -d --rm --name taskbeavo-smoke-web --network "$network" \
  -e DJANGO_SETTINGS_MODULE=config.settings.local \
  -e DATABASE_URL=postgis://taskbeavo:taskbeavo@postgis:5432/taskbeavo \
  -e REDIS_URL=redis://redis:6379/0 \
  taskbeavo-backend >/dev/null
ok=""
for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
  if docker exec taskbeavo-smoke-web python -c \
    "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" \
    >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 2
done
[ -n "$ok" ] || { docker logs taskbeavo-smoke-web >&2; fail "gunicorn image did not serve /healthz"; }
docker rm -f taskbeavo-smoke-web >/dev/null

echo "== backend tests in the container"
# Test settings and Redis db 15, whatever backend/.env or the dev stack use (db 0).
docker compose run --rm -e REDIS_URL=redis://redis:6379/15 django \
  uv run pytest -q --ds=config.settings.test

echo "OK"
