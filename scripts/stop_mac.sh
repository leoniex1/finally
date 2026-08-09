#!/usr/bin/env bash
#
# Stop FinAlly (macOS/Linux). Idempotent — safe to run when nothing is running.
#
# Removes the container but NEVER the `finally-data` volume: your cash balance,
# positions, trades and watchlist survive a stop/start cycle. To throw the
# database away deliberately:  docker volume rm finally-data
#
set -euo pipefail

CONTAINER="finally"
VOLUME="finally-data"

if ! command -v docker >/dev/null 2>&1; then
    echo "Error: docker is not installed or not on PATH." >&2
    exit 1
fi

if ! docker info >/dev/null 2>&1; then
    echo "Error: the Docker daemon is not responding. Is Docker Desktop running?" >&2
    exit 1
fi

if [ -n "$(docker ps --all --quiet --filter "name=^/${CONTAINER}$")" ]; then
    docker rm --force "$CONTAINER" >/dev/null
    echo "Stopped and removed the '$CONTAINER' container."
else
    echo "No '$CONTAINER' container found — nothing to stop."
fi

echo "The '$VOLUME' volume was left intact; your portfolio is still there."
