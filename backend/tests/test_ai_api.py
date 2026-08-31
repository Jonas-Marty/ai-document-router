"""Tests for the endpoint and task-chain API (SPEC 6.3a, 8.7).

CLAUDE.md rule 5 is the one that most needs holding here: an endpoint's key goes in and is
never handed back, only the fact that one is set.
"""

from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db
from app.config import settings as app_config
from app.models import AiEndpoint
from app.services import ai, crypto


def add(client: TestClient, name: str = "Local", **overrides: Any) -> dict[str, Any]:
    payload = {"name": name, "base_url": "http://192.168.1.50:11434/v1", **overrides}
    response = client.post("/api/v1/ai/endpoints", json=payload)
    assert response.status_code == 200, response.text
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

    def test_deleting_one_a_task_still_uses_is_refused(self, client: TestClient) -> None:
        created = add(client)
        client.put(
            "/api/v1/ai/tasks/filing",
            json={"steps": [{"endpoint_id": created["id"], "model_name": "qwen"}]},
        )

        response = client.delete(f"/api/v1/ai/endpoints/{created['id']}")

        assert response.status_code == 422
        assert "filing" in response.json()["error"]["message"]

    def test_a_missing_endpoint_is_a_404(self, client: TestClient) -> None:
        assert client.delete("/api/v1/ai/endpoints/nope").status_code == 404


class TestChains:
    def test_both_tasks_start_empty(self, client: TestClient) -> None:
        chains = client.get("/api/v1/ai/tasks").json()

        assert [chain["task"] for chain in chains] == ["extraction", "filing"]
        assert all(chain["steps"] == [] for chain in chains)

    def test_a_chain_keeps_the_order_it_was_given(self, client: TestClient) -> None:
        local = add(client, "Local")
        hosted = add(client, "Hosted", base_url="https://api.example.com/v1")

        response = client.put(
            "/api/v1/ai/tasks/filing",
            json={
                "steps": [
                    {"endpoint_id": local["id"], "model_name": "qwen2.5"},
                    {"endpoint_id": hosted["id"], "model_name": "gpt-4o"},
                ]
            },
        )

        assert response.status_code == 200
        assert [(s["endpoint_name"], s["model_name"]) for s in response.json()["steps"]] == [
            ("Local", "qwen2.5"),
            ("Hosted", "gpt-4o"),
        ]

    def test_an_endpoint_in_a_chain_reports_what_uses_it(self, client: TestClient) -> None:
        local = add(client)
        client.put(
            "/api/v1/ai/tasks/extraction",
            json={"steps": [{"endpoint_id": local["id"], "model_name": "got-ocr"}]},
        )

        assert client.get("/api/v1/ai/endpoints").json()[0]["used_by"] == ["extraction"]

    def test_rejects_a_step_pointing_at_no_endpoint(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/ai/tasks/filing",
            json={"steps": [{"endpoint_id": "gone", "model_name": "m"}]},
        )

        assert response.status_code == 404

    def test_rejects_a_blank_model_name(self, client: TestClient) -> None:
        local = add(client)
        response = client.put(
            "/api/v1/ai/tasks/filing",
            json={"steps": [{"endpoint_id": local["id"], "model_name": " "}]},
        )

        assert response.status_code == 422

    def test_an_unknown_task_is_rejected(self, client: TestClient) -> None:
        assert client.put("/api/v1/ai/tasks/summarising", json={"steps": []}).status_code == 422


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
