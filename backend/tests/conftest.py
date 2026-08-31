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
def isolated_ocr_cache(
    monkeypatch: pytest.MonkeyPatch, tmp_path_factory: pytest.TempPathFactory
) -> None:
    """Keep the searchable-copy cache out of the real data volume.

    services/searchable.py creates its directory on first use, so without this a test run
    would leave (and read) files under backend/data/ocr -- and one test's leftovers would be
    the next one's cache hit.
    """
    monkeypatch.setattr(config, "ocr_cache_dir", str(tmp_path_factory.mktemp("ocr-cache")))


@pytest.fixture(autouse=True)
def clean_webdav_cache() -> Iterator[None]:
    """The listing cache is module-level, so it would otherwise leak between tests."""
    webdav.clear_cache()
    yield
    webdav.clear_cache()


# The account every authenticated fixture signs in as. First registration wins the instance,
# so this user is an admin -- which is what the routes under test run as in production too.
TEST_EMAIL = "owner@example.com"
TEST_PASSWORD = "correct-horse-battery-staple"


@pytest.fixture
def anonymous_client(monkeypatch: pytest.MonkeyPatch) -> Iterator[TestClient]:
    """A client with no session. Everything except /health and /auth/* answers 401."""
    test_engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(test_engine)
    monkeypatch.setattr(db, "engine", test_engine)

    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def client(anonymous_client: TestClient) -> TestClient:
    """Signed in, because that is the only state in which the app is usable.

    Registering here rather than in every test keeps the suite about what each route does,
    not about how it is authenticated -- test_auth.py owns that.
    """
    response = anonymous_client.post(
        "/api/v1/auth/register", json={"email": TEST_EMAIL, "password": TEST_PASSWORD}
    )
    assert response.status_code == 201, response.text
    return anonymous_client
