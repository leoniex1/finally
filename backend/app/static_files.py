"""Serving the Next.js static export from FastAPI (`API_CONTRACT.md` §5.3,
`PLAN.md` §11).

One container, one port, one origin: the API and the frontend are the same
app, which is what removes CORS from the project entirely. The catch-all
route registered here is the last route in the application, so it can only
ever match a path no API router claimed.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Optional

from fastapi import FastAPI
from fastapi.responses import FileResponse, JSONResponse
from starlette.responses import Response

logger = logging.getLogger("app.static_files")

#: Everything under `/api` belongs to a router. If one of those paths falls
#: through to here it is a genuine 404 — answering it with `index.html`
#: would hand a JSON client a page of HTML and a `200`, which is a far more
#: confusing failure than a 404.
API_PREFIX = "api"

#: Next.js emits content-hashed filenames under `_next/static`, so they are
#: safe to cache permanently; the HTML that references them is not.
IMMUTABLE_PREFIX = "_next/static/"
IMMUTABLE_CACHE_CONTROL = "public, max-age=31536000, immutable"


def mount_static(app: FastAPI, static_dir: str) -> bool:
    """Register the static-file catch-all. Returns whether it was mounted.

    Must be called **after** every `/api` router (`API_CONTRACT.md` §10):
    Starlette matches routes in registration order, and `/{full_path:path}`
    matches everything.

    A missing `static_dir` is a no-op with a warning, not a crash — running
    the backend alone against `next dev` is the normal frontend workflow,
    and the container's health check must not depend on a build artifact
    that development deliberately does not produce.
    """
    root = Path(static_dir).resolve()
    if not root.is_dir():
        logger.warning(
            "static directory %s does not exist; serving API only "
            "(expected in backend-only development)",
            root,
        )
        return False

    @app.get("/{full_path:path}", include_in_schema=False)
    async def serve_static(full_path: str) -> Response:
        return _resolve(root, full_path)

    logger.info("serving static frontend from %s", root)
    return True


def _resolve(root: Path, full_path: str) -> Response:
    """Map a request path onto a file in the export, or onto the SPA
    fallback."""
    relative = full_path.strip("/")

    if relative == API_PREFIX or relative.startswith(API_PREFIX + "/"):
        return JSONResponse({"detail": "Not Found"}, status_code=404)

    target = _find_file(root, relative)
    if target is not None:
        headers = (
            {"Cache-Control": IMMUTABLE_CACHE_CONTROL}
            if relative.startswith(IMMUTABLE_PREFIX)
            else None
        )
        return FileResponse(target, headers=headers)

    return _fallback(root)


def _find_file(root: Path, relative: str) -> Optional[Path]:
    """The first candidate that exists, in `output: 'export'` order.

    Next.js writes route `/foo` as `foo.html` (and a route with nested
    children as `foo/index.html`), so an extensionless URL has to try both
    before it can be called a miss.
    """
    if not relative:
        candidates = ("index.html",)
    else:
        candidates = (relative, f"{relative}.html", f"{relative}/index.html")

    for candidate in candidates:
        resolved = _safe_join(root, candidate)
        if resolved is not None and resolved.is_file():
            return resolved
    return None


def _safe_join(root: Path, relative: str) -> Optional[Path]:
    """Join inside `root`, or `None` if the result escapes it.

    Starlette normalizes most traversal attempts out of the path, but this
    handler concatenates its own suffixes onto user input, so the containment
    check is done here rather than assumed upstream.
    """
    try:
        resolved = (root / relative).resolve()
    except (OSError, ValueError):  # malformed path (NUL bytes, long names)
        return None
    if resolved != root and root not in resolved.parents:
        return None
    return resolved


def _fallback(root: Path) -> Response:
    """Unknown non-API path: the export's 404 page if it has one, otherwise
    `index.html` so a client-side route survives a reload."""
    not_found = root / "404.html"
    if not_found.is_file():
        return FileResponse(not_found, status_code=404)

    index = root / "index.html"
    if index.is_file():
        return FileResponse(index)

    return JSONResponse({"detail": "Not Found"}, status_code=404)
