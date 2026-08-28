from typing import Any

from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.config import settings as app_config
from app.models import AppSettings
from app.services import crypto

VALID_PAYLOAD: dict[str, Any] = {
    "allowed_root_folders": ["/Documents"],
    "trash_folder_path": "/Trash",
    "filename_pattern": None,
    "filename_pattern_hint": None,
    "ai_endpoint_url": "https://api.example.com/v1",
    "ai_model_name": "gpt-4o",
    "ai_api_key": "super-secret-key",
}


def test_get_settings_returns_seeded_defaults(client: TestClient) -> None:
    response = client.get("/api/v1/settings")

    assert response.status_code == 200
    data = response.json()
    assert data == {
        "allowed_root_folders": [],
        "trash_folder_path": "",
        "filename_pattern": None,
        "filename_pattern_hint": None,
        "ai_endpoint_url": "",
        "ai_model_name": "",
        "ai_api_key_set": False,
    }


def test_put_settings_round_trips_and_never_exposes_the_key(client: TestClient) -> None:
    response = client.put("/api/v1/settings", json=VALID_PAYLOAD)

    assert response.status_code == 200
    data = response.json()
    assert data["allowed_root_folders"] == ["/Documents"]
    assert data["trash_folder_path"] == "/Trash"
    assert data["ai_endpoint_url"] == "https://api.example.com/v1"
    assert data["ai_api_key_set"] is True
    assert "ai_api_key" not in data
    assert "ai_api_key_encrypted" not in data

    get_response = client.get("/api/v1/settings")
    get_data = get_response.json()
    assert get_data["allowed_root_folders"] == ["/Documents"]
    assert get_data["ai_api_key_set"] is True
    assert "ai_api_key" not in get_data


def test_put_settings_leaves_key_unchanged_when_omitted(client: TestClient) -> None:
    client.put("/api/v1/settings", json=VALID_PAYLOAD)

    payload = {**VALID_PAYLOAD, "trash_folder_path": "/Attic", "ai_api_key": ""}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["trash_folder_path"] == "/Attic"
    assert data["ai_api_key_set"] is True


def test_put_settings_rejects_empty_allowed_roots(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "allowed_root_folders": []}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_rejects_duplicate_allowed_roots(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "allowed_root_folders": ["/Documents", "/Documents"]}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_rejects_nested_allowed_roots(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "allowed_root_folders": ["/Documents", "/Documents/Finance"]}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_rejects_relative_allowed_root(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "allowed_root_folders": ["Documents"]}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_rejects_trash_folder_inside_allowed_root(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "trash_folder_path": "/Documents/Trash"}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_rejects_invalid_regex(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "filename_pattern": "["}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_accepts_valid_regex(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "filename_pattern": r"^\d{4}-\d{2}-\d{2}_.+$"}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 200
    assert response.json()["filename_pattern"] == r"^\d{4}-\d{2}-\d{2}_.+$"


def test_put_settings_rejects_plain_http_public_endpoint(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "ai_endpoint_url": "http://api.example.com/v1"}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_allows_http_for_private_host(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "ai_endpoint_url": "http://192.168.1.50:11434/v1"}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 200
    assert response.json()["ai_endpoint_url"] == "http://192.168.1.50:11434/v1"


def test_put_settings_allows_http_for_localhost(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "ai_endpoint_url": "http://localhost:11434/v1"}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 200


def test_put_settings_saves_folders_before_the_ai_endpoint_is_configured(
    client: TestClient,
) -> None:
    """Regression: every settings card PUTs the whole settings object, filling the fields it
    does not own from the server's current values. On a freshly seeded database that means
    saving Folders sends back the still-empty ai_endpoint_url -- and rejecting it left no card
    that could be saved first, because saving AI fails on the still-empty allowed roots."""
    payload = {
        "allowed_root_folders": ["/Test-Inbox"],
        "trash_folder_path": "/Test-Trash",
        "filename_pattern": None,
        "filename_pattern_hint": None,
        "ai_endpoint_url": "",
        "ai_model_name": "",
    }

    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 200
    data = response.json()
    assert data["allowed_root_folders"] == ["/Test-Inbox"]
    assert data["ai_endpoint_url"] == ""

    # And the AI card saves on top of it, which is what was previously unreachable.
    ai_response = client.put(
        "/api/v1/settings",
        json={**payload, "ai_endpoint_url": "https://api.example.com/v1", "ai_model_name": "m"},
    )

    assert ai_response.status_code == 200
    assert ai_response.json()["ai_endpoint_url"] == "https://api.example.com/v1"


def test_put_settings_rejects_a_blank_but_non_empty_endpoint(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "ai_endpoint_url": "   "}
    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["code"] == "validation_error"


def test_put_settings_rejects_malformed_body_with_error_envelope(client: TestClient) -> None:
    response = client.put("/api/v1/settings", json={})

    assert response.status_code == 422
    body = response.json()
    assert body["error"]["code"] == "validation_error"
    assert isinstance(body["error"]["message"], str) and body["error"]["message"]


def test_unknown_route_returns_error_envelope(client: TestClient) -> None:
    response = client.get("/api/v1/nonexistent")

    assert response.status_code == 404
    assert response.json()["error"]["code"] == "not_found"


def test_stored_api_key_is_decryptable_with_the_secret_key(client: TestClient) -> None:
    client.put("/api/v1/settings", json=VALID_PAYLOAD)

    with Session(db.engine) as session:
        settings = session.get(AppSettings, 1)
        assert settings is not None
        assert settings.ai_api_key_encrypted is not None
        decrypted = crypto.decrypt(app_config.secret_key, settings.ai_api_key_encrypted)

    assert decrypted == "super-secret-key"
