from typing import Any

from fastapi.testclient import TestClient

VALID_PAYLOAD: dict[str, Any] = {
    "allowed_root_folders": ["/Documents"],
    "trash_folder_path": "/Trash",
    "filename_pattern": None,
    "filename_pattern_hint": None,
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
        "store_ocr_text": True,
    }


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
