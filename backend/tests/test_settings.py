from typing import Any

from fastapi.testclient import TestClient

VALID_PAYLOAD: dict[str, Any] = {
    "allowed_root_folders": ["/Documents"],
    "trash_folder_path": "/Trash",
    "watch_folder_path": "/Scans/Inbox",
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
        "watch_folder_path": "",
        "filename_pattern": None,
        "filename_pattern_hint": None,
        "store_ocr_text": True,
        "filing_instructions": None,
        "extraction_instructions": None,
    }


def test_put_settings_stores_trimmed_instructions_and_blank_as_none(client: TestClient) -> None:
    payload = {
        **VALID_PAYLOAD,
        "filing_instructions": "  Insurance letters go under /Documents/Versicherung.  ",
        "extraction_instructions": "   ",
    }

    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 200
    assert response.json()["filing_instructions"] == (
        "Insurance letters go under /Documents/Versicherung."
    )
    assert response.json()["extraction_instructions"] is None


def test_put_settings_rejects_overlong_instructions(client: TestClient) -> None:
    payload = {**VALID_PAYLOAD, "extraction_instructions": "x" * 2001}

    response = client.put("/api/v1/settings", json=payload)

    assert response.status_code == 422
    assert response.json()["error"]["message"] == (
        "Extraction instructions must be 2000 characters or fewer."
    )


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


class TestWatchFolder:
    def test_is_required(self, client: TestClient) -> None:
        response = client.put("/api/v1/settings", json={**VALID_PAYLOAD, "watch_folder_path": ""})

        assert response.status_code == 422

    def test_may_sit_inside_an_allowed_root(self, client: TestClient) -> None:
        payload = {**VALID_PAYLOAD, "watch_folder_path": "/Documents/Inbox/"}

        response = client.put("/api/v1/settings", json=payload)

        assert response.status_code == 200
        assert response.json()["watch_folder_path"] == "/Documents/Inbox"

    def test_must_not_be_or_contain_an_allowed_root(self, client: TestClient) -> None:
        # Approved documents land in the roots; a watch folder there re-ingests them.
        for watch in ("/Documents", "/"):
            payload = {**VALID_PAYLOAD, "watch_folder_path": watch}

            assert client.put("/api/v1/settings", json=payload).status_code == 422

    def test_must_not_overlap_the_trash(self, client: TestClient) -> None:
        payload = {**VALID_PAYLOAD, "watch_folder_path": "/Trash/Scans"}

        assert client.put("/api/v1/settings", json=payload).status_code == 422
