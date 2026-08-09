# FinAlly — single container, single port (PLAN.md §3, §11).
#
# Stage 1 builds the Next.js static export. Stage 2 installs the Python
# runtime with uv. Stage 3 is stage 2 plus the exported frontend, so the
# Python half can be built and verified on its own with:
#
#     docker build --target backend .
#
# Build context is the repo root (the frontend and backend both live under it).

# ─────────────────────────────────────────────────────────────────────────────
# Stage 1 — frontend: Next.js static export → /build/out
# ─────────────────────────────────────────────────────────────────────────────
FROM node:20-slim AS frontend

ENV NEXT_TELEMETRY_DISABLED=1 \
    CI=true

WORKDIR /build

# Dependencies first so a source-only change does not reinstall node_modules.
# The lockfile glob is optional: it does not fail the build when absent, and
# the install step below picks `npm ci` or `npm install` accordingly.
COPY frontend/package.json frontend/package-lock.json* ./
RUN if [ -f package-lock.json ]; then npm ci; else npm install; fi

COPY frontend/ ./
RUN npm run build

# `output: 'export'` writes `out/` (API_CONTRACT.md §7). Fail loudly here
# rather than shipping an image whose static directory is silently empty.
RUN test -f out/index.html || (echo "frontend build produced no out/index.html — is output: 'export' set?" && exit 1)

# ─────────────────────────────────────────────────────────────────────────────
# Stage 2 — backend: FastAPI runtime installed from the uv lockfile
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.12-slim AS backend

# Pinned uv, copied from the official distroless image (binaries live at the
# root there) — no curl|sh bootstrap.
COPY --from=ghcr.io/astral-sh/uv:0.9 /uv /uvx /usr/local/bin/

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never \
    PATH="/app/.venv/bin:$PATH"

WORKDIR /app

# Lockfile-only layer: dependencies rebuild only when pyproject/uv.lock change.
# `--no-install-project` because `app/` is not copied yet.
COPY backend/pyproject.toml backend/uv.lock ./
RUN uv sync --frozen --no-dev --no-install-project

COPY backend/app ./app
RUN uv sync --frozen --no-dev

# Default runtime configuration (API_CONTRACT.md §5.2). `/app/db` is the mount
# point for the named volume `finally-data`; it must exist in the image so a
# fresh volume inherits it. Both are overridable from the environment.
ENV DB_PATH=/app/db/finally.db \
    STATIC_DIR=/app/static \
    PORT=8000
RUN mkdir -p /app/db /app/static

EXPOSE 8000

# Shallow liveness probe — /api/health reports only that the process serves
# requests (backend/app/main.py), which is exactly what a restart policy
# should key off. urllib avoids adding curl to the image.
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health', timeout=4).status == 200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]

# ─────────────────────────────────────────────────────────────────────────────
# Stage 3 — runtime: backend + the exported frontend (the default target)
# ─────────────────────────────────────────────────────────────────────────────
FROM backend AS runtime

COPY --from=frontend /build/out /app/static
