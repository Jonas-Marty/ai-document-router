"""Logging: request ids, the JSON format, the catch-all handler, and keeping the key out."""

import json
import logging
import sys
from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi import APIRouter
from fastapi.testclient import TestClient

from app import log
from app.main import app


@pytest.fixture
def context_caplog(caplog: pytest.LogCaptureFixture) -> pytest.LogCaptureFixture:
    """caplog's handler, tagged with the context column the real console handler adds."""
    caplog.handler.addFilter(log.ContextFilter())
    caplog.set_level(logging.DEBUG)
    return caplog


class TestRequestId:
    def test_an_incoming_id_is_echoed_back(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "trace-123"})

        assert response.headers["X-Request-ID"] == "trace-123"

    def test_one_is_generated_when_none_is_sent(self, client: TestClient) -> None:
        response = client.get("/api/v1/health")

        assert len(response.headers["X-Request-ID"]) == 12

    def test_a_malformed_id_is_replaced_rather_than_echoed(self, client: TestClient) -> None:
        response = client.get("/api/v1/health", headers={"X-Request-ID": "a b;c=d"})

        assert response.headers["X-Request-ID"] != "a b;c=d"
        assert len(response.headers["X-Request-ID"]) == 12

    def test_the_request_line_carries_the_id(
        self, client: TestClient, context_caplog: pytest.LogCaptureFixture
    ) -> None:
        client.get("/api/v1/queue", headers={"X-Request-ID": "trace-456"})

        lines = [r for r in context_caplog.records if r.name == "app.request"]
        assert any(
            "GET /api/v1/queue 200" in r.getMessage() and r.context == "req=trace-456"  # type: ignore[attr-defined]
            for r in lines
        )

    def test_polled_paths_log_below_info(
        self, client: TestClient, context_caplog: pytest.LogCaptureFixture
    ) -> None:
        client.get("/api/v1/health")

        health = [r for r in context_caplog.records if "/api/v1/health" in r.getMessage()]
        assert health and all(r.levelno == logging.DEBUG for r in health)


@pytest.fixture
def route_raising_bug() -> Iterator[str]:
    router = APIRouter()
    path = "/api/v1/_test_bug"

    @router.get(path)
    def _boom() -> None:
        raise RuntimeError("a genuine bug")

    app.include_router(router)
    try:
        yield path
    finally:
        app.router.routes[:] = [
            route for route in app.router.routes if getattr(route, "path", None) != path
        ]


class TestUnexpectedErrors:
    def test_a_bug_becomes_the_error_envelope_with_a_reference(
        self,
        client: TestClient,
        route_raising_bug: str,
        context_caplog: pytest.LogCaptureFixture,
    ) -> None:
        quiet = TestClient(app, raise_server_exceptions=False, cookies=client.cookies)

        response = quiet.get(route_raising_bug, headers={"X-Request-ID": "bug-1"})

        assert response.status_code == 500
        assert response.json() == {
            "error": {
                "code": "internal_error",
                "message": "Something went wrong on the server (reference bug-1).",
            }
        }
        assert response.headers["X-Request-ID"] == "bug-1"
        logged = [r for r in context_caplog.records if r.getMessage() == "Unhandled error"]
        assert logged and logged[0].exc_info is not None


class TestJsonFormatter:
    def test_formats_one_parseable_object_with_the_traceback(self) -> None:
        try:
            raise ValueError("nope")
        except ValueError:
            record = logging.LogRecord(
                "app.x", logging.ERROR, __file__, 1, "failed %s", ("here",), sys.exc_info()
            )
        record.context = "tick=abc"

        parsed = json.loads(log.JsonFormatter().format(record))

        assert parsed["level"] == "ERROR"
        assert parsed["logger"] == "app.x"
        assert parsed["message"] == "failed here"
        assert parsed["context"] == "tick=abc"
        assert "ValueError: nope" in parsed["exc_info"]

    def test_omits_an_empty_context(self) -> None:
        record = logging.LogRecord("app.x", logging.INFO, __file__, 1, "hi", None, None)
        record.context = "-"

        assert "context" not in json.loads(log.JsonFormatter().format(record))


def test_bound_context_is_restored_afterwards() -> None:
    with log.bound("tick=one"):
        assert log.context_var.get() == "tick=one"
    assert log.context_var.get() == ""


@respx.mock
def test_the_api_key_never_reaches_the_log(
    client: TestClient, context_caplog: pytest.LogCaptureFixture
) -> None:
    key = "sk-canary-must-not-be-logged"
    created = client.post(
        "/api/v1/ai/endpoints",
        json={"name": "Hosted", "base_url": "https://api.example.com/v1", "api_key": key},
    ).json()
    client.put(
        f"/api/v1/ai/endpoints/{created['id']}",
        json={"name": "Hosted", "base_url": "https://api.example.com/v1", "api_key": key},
    )
    respx.get("https://api.example.com/v1/models").mock(
        return_value=httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    client.post(
        "/api/v1/ai/models",
        json={"base_url": "https://api.example.com/v1", "endpoint_id": created["id"]},
    )

    assert context_caplog.records
    assert key not in context_caplog.text
