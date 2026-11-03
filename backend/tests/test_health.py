from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi import APIRouter
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.main import app
from app.models import Document, DocumentStatus
from app.schemas import NextcloudWrite
from app.services import settings as settings_service
from app.services import webdav
from app.services.audit import Actor
from app.services.errors import WebDAVUnreachable
from tests.conftest import signed_in_settings, signed_in_user_id


def _connect_nextcloud() -> None:
    with Session(db.engine) as session:
        settings_service.update_nextcloud(
            session,
            signed_in_settings(session),
            NextcloudWrite(
                base_url="https://cloud.example.com/remote.php/dav/files/me",
                username="me",
                password="app-password",
            ),
            actor=Actor(user_id=None, label="test"),
        )


def _document(name: str, status: DocumentStatus) -> Document:
    return Document(
        user_id=signed_in_user_id(),
        webdav_path=f"/Scans/Inbox/{name}",
        original_filename=name,
        mime_type="application/pdf",
        file_size_bytes=1024,
        content_hash=f"hash-{name}",
        scanned_at=datetime(2026, 8, 21, 9, 0, 0),
        discovered_at=datetime(2026, 8, 21, 9, 1, 0),
        status=status,
    )


def test_without_a_session_says_only_that_the_api_is_up(anonymous_client: TestClient) -> None:
    response = anonymous_client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json() == {
        "status": "ok",
        "webdav_configured": False,
        "webdav_reachable": None,
        "queue_depth": 0,
    }


def test_before_nextcloud_is_connected_there_is_nothing_to_probe(client: TestClient) -> None:
    body = client.get("/api/v1/health").json()

    assert (body["webdav_configured"], body["webdav_reachable"]) == (False, None)


def test_stays_200_when_the_users_nextcloud_is_unreachable(client: TestClient) -> None:
    """The deploy healthcheck hits this endpoint. A dependency outage must not take the
    container unhealthy -- webdav_reachable is the signal, not the status code."""
    _connect_nextcloud()

    response = client.get("/api/v1/health")

    assert response.status_code == 200
    assert response.json()["webdav_configured"] is True
    assert response.json()["webdav_reachable"] is False


def test_probes_the_signed_in_users_own_nextcloud(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    _connect_nextcloud()
    probed: list[webdav.Connection] = []

    def probe(connection: webdav.Connection) -> bool:
        probed.append(connection)
        return True

    monkeypatch.setattr(webdav, "probe_reachable", probe)

    assert client.get("/api/v1/health").json()["webdav_reachable"] is True
    assert [(c.username, c.password) for c in probed] == [("me", "app-password")]


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
