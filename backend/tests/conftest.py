from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.pool import StaticPool
from sqlmodel import SQLModel, create_engine

from app import db
from app.config import settings as config
from app.main import app
from app.services import webdav


@pytest.fixture(autouse=True)
def no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Keep the suite off the network.

    /health probes WebDAV on every call, so without this the tests would make real
    requests to whatever WEBDAV_BASE_URL happens to be set to. Tests that care about the
    probe patch it themselves.
    """
    monkeypatch.setattr(webdav, "probe_reachable", lambda: False)


@pytest.fixture(autouse=True)
def no_poller(monkeypatch: pytest.MonkeyPatch) -> None:
    """The TestClient runs the lifespan, which would otherwise start a real background
    scheduler doing real network I/O for the whole suite."""
    monkeypatch.setattr(config, "poller_enabled", False)


@pytest.fixture(autouse=True)
def clean_webdav_cache() -> Iterator[None]:
    """The listing cache is module-level, so it would otherwise leak between tests."""
    webdav.clear_cache()
    yield
    webdav.clear_cache()


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)

    with TestClient(app) as test_client:
        yield test_client
