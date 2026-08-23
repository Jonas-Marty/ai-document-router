from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.main import app
from app.models import Document, DocumentStatus
from app.services import webdav
from app.services.errors import WebDAVUnreachable


def _document(name: str, status: DocumentStatus) -> Document:
    return Document(
        webdav_path=f"/Scans/Inbox/{name}",
        original_filename=name,
        mime_type="application/pdf",
        file_size_bytes=1024,
        content_hash=f"hash-{name}",
        scanned_at=datetime(2026, 8, 21, 9, 0, 0),
        discovered_at=datetime(2026, 8, 21, 9, 1, 0),
        status=status,
    )


def test_health_reports_status_reachability_and_queue_depth(client: TestClient) -> None:
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "webdav_reachable": False,
        "queue_depth": 0,
    }


def test_health_stays_200_when_webdav_is_unreachable(client: TestClient) -> None:
    """The deploy healthcheck hits this endpoint. A dependency outage must not take the
    container unhealthy -- webdav_reachable is the signal, not the status code."""
    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["webdav_reachable"] is False


def test_health_reports_webdav_reachable_when_the_probe_succeeds(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(webdav, "probe_reachable", lambda: True)

    assert client.get("/api/v1/health").json()["webdav_reachable"] is True


def test_queue_depth_counts_pending_and_skipped_only(client: TestClient) -> None:
    with Session(db.engine) as session:
        session.add(_document("a.pdf", DocumentStatus.pending))
        session.add(_document("b.pdf", DocumentStatus.skipped))
        session.add(_document("c.pdf", DocumentStatus.moved))
        session.add(_document("d.pdf", DocumentStatus.trashed))
        session.add(_document("e.pdf", DocumentStatus.failed))
        session.commit()

    assert client.get("/api/v1/health").json()["queue_depth"] == 2


@pytest.fixture
def route_raising_unreachable() -> Iterator[str]:
    """Temporarily mount a route that raises, to exercise the global handler."""
    router = APIRouter()
    path = "/api/v1/_test_unreachable"

    @router.get(path)
    def _boom() -> None:
        raise WebDAVUnreachable("Can't reach the WebDAV server.")

    app.include_router(router)
    try:
        yield path
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) != path
        ]


def test_webdav_unreachable_maps_to_503_envelope(
    client: TestClient, route_raising_unreachable: str
) -> None:
    response = client.get(route_raising_unreachable)

    assert response.status_code == 503
    assert response.json() == {
        "error": {
            "code": "webdav_unreachable",
            "message": "Can't reach the WebDAV server.",
        }
    }
