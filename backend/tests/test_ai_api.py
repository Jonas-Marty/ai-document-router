"""Tests for the connection API (SPEC 6.3a, 8.7).

CLAUDE.md rule 5 is the one that most needs holding here: an endpoint's key goes in and is
never handed back, only the fact that one is set.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db
from app.config import settings as app_config
from app.models import AiConfiguration, AiEndpoint
from app.services import ai, configurations, crypto


def add(client: TestClient, name: str = "Local", **overrides: Any) -> dict[str, Any]:
    payload = {"name": name, "base_url": "http://192.168.1.50:11434/v1", **overrides}
    response = client.post("/api/v1/ai/endpoints", json=payload)
    assert response.status_code == 200, response.text
    return response.json()


def configure(client: TestClient, endpoint_id: str, model_name: str = "qwen") -> dict[str, Any]:
    response = client.post(
        "/api/v1/configurations",
        json={
            "name": "Daily",
            "reading_method": "text_layer",
            "reading": {"temperature": 0.1, "max_tokens": 4096},
            "filing_strategy": "one_call",
            "filing": {
                "endpoint_id": endpoint_id,
                "model_name": model_name,
                "temperature": 0.1,
                "max_tokens": 2048,
            },
            "max_examples": 8,
            "max_folders": 250,
            "max_text_chars": 6000,
        },
    )
    assert response.status_code == 201, response.text
    return response.json()


class TestEndpoints:
    def test_an_added_endpoint_is_listed_without_its_key(self, client: TestClient) -> None:
        created = add(client, api_key="super-secret-key")

        assert created["api_key_set"] is True
        assert "api_key" not in created

        listed = client.get("/api/v1/ai/endpoints").json()
        assert [e["name"] for e in listed] == ["Local"]
        assert listed[0]["used_by"] == []
        assert "api_key" not in listed[0]

    def test_the_key_is_stored_encrypted(self, client: TestClient) -> None:
        add(client, api_key="super-secret-key")

        with Session(db.engine) as session:
            endpoint = session.exec(select(AiEndpoint)).one()
            assert endpoint.api_key_encrypted is not None
            assert b"super-secret-key" not in endpoint.api_key_encrypted
            assert (
                crypto.decrypt(app_config.secret_key, endpoint.api_key_encrypted)
                == "super-secret-key"
            )

    def test_an_update_without_a_key_keeps_the_stored_one(self, client: TestClient) -> None:
        created = add(client, api_key="super-secret-key")

        response = client.put(
            f"/api/v1/ai/endpoints/{created['id']}",
            json={"name": "Renamed", "base_url": "https://api.example.com/v1", "api_key": ""},
        )

        assert response.status_code == 200
        assert response.json()["name"] == "Renamed"
        assert response.json()["api_key_set"] is True

    def test_rejects_a_public_http_url(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ai/endpoints", json={"name": "Public", "base_url": "http://api.example.com/v1"}
        )

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_allows_https_anywhere_and_http_on_localhost(self, client: TestClient) -> None:
        add(client, "Hosted", base_url="https://api.example.com/v1")
        add(client, "Laptop", base_url="http://localhost:11434/v1")

    def test_rejects_a_duplicate_name(self, client: TestClient) -> None:
        add(client)
        response = client.post(
            "/api/v1/ai/endpoints",
            json={"name": "Local", "base_url": "http://192.168.1.50:11434/v1"},
        )

        assert response.status_code == 422

    def test_rejects_a_blank_name(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ai/endpoints", json={"name": "  ", "base_url": "https://api.example.com/v1"}
        )

        assert response.status_code == 422

    def test_deleting_an_unused_endpoint_removes_it(self, client: TestClient) -> None:
        created = add(client)

        assert client.delete(f"/api/v1/ai/endpoints/{created['id']}").status_code == 200
        assert client.get("/api/v1/ai/endpoints").json() == []

    def test_deleting_one_a_configuration_uses_is_refused(self, client: TestClient) -> None:
        created = add(client)
        configure(client, created["id"])

        response = client.delete(f"/api/v1/ai/endpoints/{created['id']}")

        assert response.status_code == 422
        assert "'Daily'" in response.json()["error"]["message"]

    def test_an_endpoint_reports_the_configurations_using_it(self, client: TestClient) -> None:
        created = add(client)
        configure(client, created["id"])

        assert client.get("/api/v1/ai/endpoints").json()[0]["used_by"] == ["Daily"]

    def test_a_missing_endpoint_is_a_404(self, client: TestClient) -> None:
        assert client.delete("/api/v1/ai/endpoints/nope").status_code == 404


class TestModelListing:
    def test_uses_the_url_and_key_typed_into_the_form(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """The point of the button is testing a URL you have not committed to yet."""
        seen: dict[str, Any] = {}

        def fake_list_models(*, endpoint_url: str, api_key: str | None) -> list[str]:
            seen.update(endpoint_url=endpoint_url, api_key=api_key)
            return ["a", "b"]

        monkeypatch.setattr(ai, "list_models", fake_list_models)

        response = client.post(
            "/api/v1/ai/models",
            json={"base_url": "https://typed.example.com/v1", "api_key": "typed-key"},
        )

        assert response.status_code == 200
        assert response.json() == {"models": ["a", "b"]}
        assert seen == {"endpoint_url": "https://typed.example.com/v1", "api_key": "typed-key"}

    def test_falls_back_to_a_saved_endpoints_key(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        """The form is never given the saved key back, so a blank one cannot mean "no key"."""
        created = add(client, api_key="super-secret-key")
        seen: dict[str, Any] = {}

        def fake_list_models(*, endpoint_url: str, api_key: str | None) -> list[str]:
            seen.update(api_key=api_key)
            return []

        monkeypatch.setattr(ai, "list_models", fake_list_models)

        response = client.post(
            "/api/v1/ai/models",
            json={
                "base_url": "http://192.168.1.50:11434/v1",
                "api_key": "",
                "endpoint_id": created["id"],
            },
        )

        assert response.status_code == 200
        assert seen == {"api_key": "super-secret-key"}

    def test_requires_a_url(self, client: TestClient) -> None:
        response = client.post("/api/v1/ai/models", json={"base_url": "   "})

        assert response.status_code == 422
        assert response.json()["error"]["code"] == "validation_error"

    def test_rejects_a_public_http_url(self, client: TestClient) -> None:
        response = client.post("/api/v1/ai/models", json={"base_url": "http://api.example.com/v1"})

        assert response.status_code == 422

    def test_an_unreachable_endpoint_is_a_503(self, client: TestClient, monkeypatch: Any) -> None:
        def fake_list_models(*, endpoint_url: str, api_key: str | None) -> list[str]:
            raise ai.AIUnavailable("Couldn't reach the AI endpoint: nope.")

        monkeypatch.setattr(ai, "list_models", fake_list_models)

        response = client.post("/api/v1/ai/models", json={"base_url": "https://api.example.com/v1"})

        assert response.status_code == 503
        body = response.json()["error"]
        assert body["code"] == "ai_unavailable"
        assert "Couldn't reach" in body["message"]


class TestPerAccount:
    def test_endpoints_and_their_names_belong_to_one_account(
        self, client: TestClient, monkeypatch: Any
    ) -> None:
        mine = add(client, "Local")
        monkeypatch.setattr(app_config, "allow_registration", True)
        client.post("/api/v1/auth/logout")
        client.post(
            "/api/v1/auth/register",
            json={"email": "second@example.com", "password": "another-long-password"},
        )

        # Not listed, not reachable by id, and its name is free to reuse.
        assert client.get("/api/v1/ai/endpoints").json() == []
        assert client.delete(f"/api/v1/ai/endpoints/{mine['id']}").status_code == 404
        assert add(client, "Local")["id"] != mine["id"]


class TestReplyTimeout:
    def test_defaults_to_two_minutes_and_can_be_changed(self, client: TestClient) -> None:
        created = add(client)
        assert created["timeout_seconds"] == 120

        updated = client.put(
            f"/api/v1/ai/endpoints/{created['id']}",
            json={"name": "Local", "base_url": created["base_url"], "timeout_seconds": 300},
        ).json()

        assert updated["timeout_seconds"] == 300

    def test_rejects_a_timeout_out_of_range(self, client: TestClient) -> None:
        response = client.post(
            "/api/v1/ai/endpoints",
            json={"name": "x", "base_url": "https://api.example.com/v1", "timeout_seconds": 5},
        )

        assert response.status_code == 422


class TestCapabilities:
    """SPEC 6.3b: what a connection's server holds and charges, set per endpoint."""

    def test_round_trip(self, client: TestClient) -> None:
        created = add(
            client,
            context_limit=16384,
            json_mode="json_object",
            price_input_per_million=0.4,
            price_output_per_million=1.6,
        )

        assert created["context_limit"] == 16384
        assert created["json_mode"] == "json_object"
        assert created["price_input_per_million"] == 0.4
        assert created["price_output_per_million"] == 1.6

    def test_defaults_are_unknown_limit_automatic_json_and_no_price(
        self, client: TestClient
    ) -> None:
        created = add(client)

        assert created["context_limit"] is None
        assert created["json_mode"] == "auto"
        assert created["price_input_per_million"] is None

    def test_null_on_update_clears_a_limit(self, client: TestClient) -> None:
        created = add(client, context_limit=16384)

        response = client.put(
            f"/api/v1/ai/endpoints/{created['id']}",
            json={"name": "Local", "base_url": created["base_url"], "context_limit": None},
        )

        assert response.json()["context_limit"] is None

    def test_a_steps_resolved_call_carries_them(self, client: TestClient) -> None:
        created = add(client, context_limit=8192, price_output_per_million=2.0)
        configure(client, created["id"], "qwen3")

        with Session(db.engine) as session:
            configuration = session.exec(select(AiConfiguration)).one()
            step = configurations.plan(session, configuration).filing_step

        assert step is not None
        assert (step.context_limit, step.price_output) == (8192, 2.0)

    @pytest.mark.parametrize(
        "field",
        [
            {"context_limit": 512},
            {"context_limit": 3_000_000},
            {"price_input_per_million": -1},
            {"price_output_per_million": 20_000},
            {"json_mode": "yaml"},
        ],
    )
    def test_rejects_out_of_range_values(self, client: TestClient, field: dict[str, Any]) -> None:
        payload = {"name": "Local", "base_url": "http://192.168.1.50:11434/v1", **field}
        assert client.post("/api/v1/ai/endpoints", json=payload).status_code == 422
