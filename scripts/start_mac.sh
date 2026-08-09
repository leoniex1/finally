#!/usr/bin/env bash
#
# Start FinAlly (macOS/Linux). Idempotent — safe to run repeatedly.
#
#   ./scripts/start_mac.sh              start, building the image if missing
#   ./scripts/start_mac.sh --build      force a rebuild and recreate the container
#   ./scripts/start_mac.sh --open       also open the app in your browser
#   ./scripts/start_mac.sh --port 9000  publish on a different host port
#
set -euo pipefail

IMAGE="finally:latest"
CONTAINER="finally"
VOLUME="finally-data"        # named Docker volume holding the SQLite database
PORT="${PORT:-8000}"
BUILD=0
OPEN=0

usage() {
    sed -n '3,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
    case "$1" in
        --build) BUILD=1 ;;
        --open)  OPEN=1 ;;
        --port)  PORT="${2:?--port needs a value}"; shift ;;
        -h|--help) usage; exit 0 ;;
        *) echo "Unknown option: $1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_ROOT"

# ── Preflight ────────────────────────────────────────────────────────────────
if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed or not on PATH." >&2
    echo "Install Docker Desktop: https://docs.docker.com/get-docker/" >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Error: the Docker daemon is not responding. Is Docker Desktop running?" >&2
    exit 1
fi

# The container reads its configuration from --env-file, so the file has to
# exist. A blank OPENROUTER_API_KEY only disables chat (PLAN.md §5), so we
# seed one from the template rather than refusing to start.
if [ ! -f .env ]; then
    cp .env.example .env
    echo "Created .env from .env.example."
    echo "  Add your OPENROUTER_API_KEY to enable the AI chat panel; everything else works without it."
fi

# ── Image ────────────────────────────────────────────────────────────────────
if [ "$BUILD" -eq 1 ] || [ -z "$(docker images -q "$IMAGE" 2>/dev/null)" ]; then
    echo "Building $IMAGE ..."
    docker build -t "$IMAGE" .
else
    echo "Using existing image $IMAGE (pass --build to rebuild)."
fi

# ── Container ────────────────────────────────────────────────────────────────
RUNNING="$(docker ps --quiet --filter "name=^/${CONTAINER}$")"
if [ -n "$RUNNING" ] && [ "$BUILD" -eq 0 ]; then
    echo "FinAlly is already running."
else
    if [ -n "$(docker ps --all --quiet --filter "name=^/${CONTAINER}$")" ]; then
        echo "Replacing the existing '$CONTAINER' container (the $VOLUME volume is untouched) ..."
        docker rm --force "$CONTAINER" >/dev/null
    fi

    # This volume form must match docker-compose.yml, stop_mac.sh, the Windows
    # scripts, and the documented `docker run` — one database, one entry point
    # shape (PLAN.md §11).
    docker run --detach \
        --name "$CONTAINER" \
        --publish "${PORT}:8000" \
        --env-file .env \
        --volume "${VOLUME}:/app/db" \
        --restart unless-stopped \
        "$IMAGE" >/dev/null
    echo "Started container '$CONTAINER'."
fi

# ── Wait for health ──────────────────────────────────────────────────────────
URL="http://localhost:${PORT}"
printf "Waiting for the app to become healthy "
for _ in $(seq 1 60); do
    STATE="$(docker inspect --format '{{.State.Status}}' "$CONTAINER" 2>/dev/null || echo missing)"
    if [ "$STATE" != "running" ]; then
        echo
        echo "Error: container is '$STATE'. Last log lines:" >&2
        docker logs --tail 40 "$CONTAINER" >&2 || true
        exit 1
    fi
    HEALTH="$(docker inspect --format '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo none)"
    if [ "$HEALTH" = "healthy" ] || [ "$HEALTH" = "none" ]; then
        echo
        break
    fi
    printf "."
    sleep 2
done
echo

echo
echo "  FinAlly is running at $URL"
echo "  Data lives in the Docker volume '$VOLUME' (docker volume rm $VOLUME resets it)."
echo "  Logs:  docker logs -f $CONTAINER"
echo "  Stop:  ./scripts/stop_mac.sh"
echo

if [ "$OPEN" -eq 1 ]; then
    if command -v open >/dev/null 2>&1; then
        open "$URL"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "$URL" >/dev/null 2>&1 &
    else
        echo "(Could not find a browser opener; visit $URL yourself.)"
    fi
fi
