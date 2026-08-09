"""Serving the Next.js export (`API_CONTRACT.md` §5.3).

The two things worth guarding are that a missing export never takes the
backend down, and that the catch-all cannot swallow an `/api` path.
"""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI

from app.static_files import IMMUTABLE_CACHE_CONTROL, mount_static


@pytest.fixture
def export_dir(tmp_path: Path) -> Path:
    """A miniature `output: 'export'` tree — the shapes Next.js actually
    emits: a root page, a flat `foo.html` route, a nested route with its own
    `index.html`, a hashed asset, and a 404 page."""
    root = tmp_path / "static"
    (root / "docs" / "guide").mkdir(parents=True)
    (root / "_next" / "static" / "chunks").mkdir(parents=True)

    (root / "index.html").write_text("<h1>home</h1>", encoding="utf-8")
    (root / "portfolio.html").write_text("<h1>portfolio</h1>", encoding="utf-8")
    (root / "404.html").write_text("<h1>not found</h1>", encoding="utf-8")
    (root / "docs" / "guide" / "index.html").write_text("<h1>guide</h1>", encoding="utf-8")
    (root / "_next" / "static" / "chunks" / "main-abc123.js").write_text(
        "console.log(1)", encoding="utf-8"
    )
    return root


def build_app(static_dir: Path | str) -> FastAPI:
    """An app whose API router is registered *before* the static mount, the
    order `API_CONTRACT.md` §10 requires."""
    app = FastAPI()

    @app.get("/api/health")
    async def health() -> dict:
        return {"status": "ok"}

    mount_static(app, str(static_dir))
    return app


def client_for(app: FastAPI) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    )


# ── Missing export ───────────────────────────────────────────────────────


def test_missing_directory_is_a_no_op(tmp_path: Path) -> None:
    app = FastAPI()
    assert mount_static(app, str(tmp_path / "does-not-exist")) is False


def test_missing_directory_logs_a_warning(tmp_path: Path, caplog) -> None:
    with caplog.at_level("WARNING", logger="app.static_files"):
        mount_static(FastAPI(), str(tmp_path / "nope"))
    assert "static directory" in caplog.text


def test_a_file_path_is_not_a_directory(tmp_path: Path) -> None:
    """`STATIC_DIR` pointed at a file is a misconfiguration, not a crash."""
    stray = tmp_path / "static"
    stray.write_text("not a directory", encoding="utf-8")
    assert mount_static(FastAPI(), str(stray)) is False


async def test_api_still_serves_without_a_static_export(tmp_path: Path) -> None:
    app = build_app(tmp_path / "does-not-exist")
    async with client_for(app) as client:
        assert (await client.get("/api/health")).json() == {"status": "ok"}
        # No catch-all registered, so an unknown path is FastAPI's own 404.
        assert (await client.get("/whatever")).status_code == 404


# ── Resolution ───────────────────────────────────────────────────────────


def test_present_directory_mounts(export_dir: Path) -> None:
    assert mount_static(FastAPI(), str(export_dir)) is True


@pytest.mark.parametrize(
    ("path", "body"),
    [
        ("/", "<h1>home</h1>"),
        ("/index.html", "<h1>home</h1>"),
        ("/portfolio.html", "<h1>portfolio</h1>"),
        ("/portfolio", "<h1>portfolio</h1>"),  # extensionless → foo.html
        ("/docs/guide", "<h1>guide</h1>"),  # extensionless → foo/index.html
        ("/docs/guide/", "<h1>guide</h1>"),  # trailing slash
        ("/_next/static/chunks/main-abc123.js", "console.log(1)"),
    ],
)
async def test_resolves_export_paths(export_dir: Path, path: str, body: str) -> None:
    async with client_for(build_app(export_dir)) as client:
        response = await client.get(path)
    assert response.status_code == 200
    assert response.text == body


async def test_hashed_assets_are_cached_immutably(export_dir: Path) -> None:
    async with client_for(build_app(export_dir)) as client:
        response = await client.get("/_next/static/chunks/main-abc123.js")
    assert response.headers["cache-control"] == IMMUTABLE_CACHE_CONTROL


async def test_html_is_not_cached_immutably(export_dir: Path) -> None:
    async with client_for(build_app(export_dir)) as client:
        response = await client.get("/")
    assert response.headers.get("cache-control") != IMMUTABLE_CACHE_CONTROL


# ── Fallbacks ────────────────────────────────────────────────────────────


async def test_unknown_path_serves_the_404_page(export_dir: Path) -> None:
    async with client_for(build_app(export_dir)) as client:
        response = await client.get("/no/such/route")
    assert response.status_code == 404
    assert response.text == "<h1>not found</h1>"


async def test_unknown_path_falls_back_to_index_without_a_404_page(
    export_dir: Path,
) -> None:
    """A client-side route must survive a reload even when the export
    shipped no 404 page."""
    (export_dir / "404.html").unlink()
    async with client_for(build_app(export_dir)) as client:
        response = await client.get("/no/such/route")
    assert response.status_code == 200
    assert response.text == "<h1>home</h1>"


async def test_empty_export_directory_returns_404(tmp_path: Path) -> None:
    empty = tmp_path / "static"
    empty.mkdir()
    async with client_for(build_app(empty)) as client:
        response = await client.get("/")
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


# ── The `/api` boundary ──────────────────────────────────────────────────


async def test_api_routes_are_not_shadowed(export_dir: Path) -> None:
    async with client_for(build_app(export_dir)) as client:
        response = await client.get("/api/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/api", "/api/", "/api/nope", "/api/portfolio"])
async def test_unknown_api_paths_get_json_404_not_html(
    export_dir: Path, path: str
) -> None:
    """Answering an unmatched `/api` path with `index.html` and a `200` would
    hand a JSON client a page of HTML — a far more confusing failure than a
    404."""
    async with client_for(build_app(export_dir)) as client:
        response = await client.get(path)
    assert response.status_code == 404
    assert response.json() == {"detail": "Not Found"}


def test_catch_all_stays_out_of_the_openapi_schema(export_dir: Path) -> None:
    """`/{full_path}` in the schema would show up as an API endpoint to
    every generated client and to the market-data route assertions."""
    app = build_app(export_dir)
    assert set(app.openapi()["paths"]) == {"/api/health"}


# ── Traversal ────────────────────────────────────────────────────────────


@pytest.mark.parametrize(
    "path",
    [
        "/%2e%2e/secret.txt",
        "/docs/%2e%2e/%2e%2e/secret.txt",
        "/_next/%2e%2e/%2e%2e/secret.txt",
    ],
)
async def test_path_traversal_cannot_escape_the_export(
    export_dir: Path, path: str
) -> None:
    (export_dir.parent / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    async with client_for(build_app(export_dir)) as client:
        response = await client.get(path)
    assert "TOP SECRET" not in response.text
