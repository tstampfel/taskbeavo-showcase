# One image for every backend role: web (default CMD), Celery worker and Celery Beat.
# `docker build -t taskbeavo-backend backend/` builds the final `runtime` stage;
# docker-compose.yml builds the `dev` stage, which adds the dev dependency group.

FROM python:3.12-slim AS base
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    UV_LINK_MODE=copy \
    UV_FROZEN=1 \
    UV_NO_CACHE=1 \
    UV_PYTHON_DOWNLOADS=never \
    # Outside /app, so docker compose can bind-mount the source without hiding the venv.
    UV_PROJECT_ENVIRONMENT=/opt/venv \
    PATH="/opt/venv/bin:$PATH"
# GDAL, GEOS and PROJ for GeoDjango/PostGIS; libpq5 for psycopg.
RUN apt-get update && apt-get install -y --no-install-recommends \
      gdal-bin libgdal-dev libgeos-dev libproj-dev binutils libpq5 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=ghcr.io/astral-sh/uv:0.11.28 /uv /usr/local/bin/uv
RUN useradd --create-home --uid 10001 app
WORKDIR /app

FROM base AS dev
# gettext for `manage.py makemessages` and `compilemessages`. The runtime image doesn't
# need it: the compiled .mo catalogs are committed.
RUN apt-get update && apt-get install -y --no-install-recommends gettext \
    && rm -rf /var/lib/apt/lists/*
COPY pyproject.toml uv.lock ./
RUN UV_COMPILE_BYTECODE=1 uv sync --frozen && chown -R app /opt/venv
USER app
EXPOSE 8000
CMD ["uvicorn", "config.asgi:application", "--host", "0.0.0.0", "--port", "8000", "--reload"]

FROM base AS runtime
COPY pyproject.toml uv.lock ./
RUN UV_COMPILE_BYTECODE=1 uv sync --frozen --no-dev
COPY . .
RUN DJANGO_SETTINGS_MODULE=config.settings.build python manage.py collectstatic --noinput
# Fail closed: without this the entry points fall back to config.settings.local (DEBUG on,
# a public SECRET_KEY). Production settings refuse to start without the real secrets.
ENV DJANGO_SETTINGS_MODULE=config.settings.production
# The source stays root-owned and read-only for the app user.
USER app
EXPOSE 8000
CMD ["gunicorn", "config.asgi:application", "-k", "uvicorn_worker.UvicornWorker", \
     "-b", "0.0.0.0:8000", "--workers", "3", "--timeout", "60"]
