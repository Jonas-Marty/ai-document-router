from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.services import settings as settings_service
from app.services import webdav
from tests.conftest import signed_in_settings

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
        "proposal_batch_size": 10,
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


@pytest.mark.parametrize("size", [0, 51])
def test_put_settings_rejects_a_batch_size_out_of_range(client: TestClient, size: int) -> None:
    response = client.put("/api/v1/settings", json={**VALID_PAYLOAD, "proposal_batch_size": size})

    assert response.status_code == 422
    assert response.json()["error"]["message"] == ("Batch size must be between 1 and 50 documents.")


def test_put_settings_stores_the_batch_size(client: TestClient) -> None:
    response = client.put("/api/v1/settings", json={**VALID_PAYLOAD, "proposal_batch_size": 3})

    assert response.status_code == 200
    assert response.json()["proposal_batch_size"] == 3


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


NEXTCLOUD = {
    "base_url": "https://cloud.example.com/remote.php/dav/files/me/",
    "username": "me",
    "password": "app-password-canary",
}


class TestNextcloud:
    def test_starts_unconnected(self, client: TestClient) -> None:
        assert client.get("/api/v1/settings/nextcloud").json() == {
            "base_url": "",
            "username": "",
            "password_set": False,
        }

    def test_saving_never_returns_the_password(self, client: TestClient) -> None:
        response = client.put("/api/v1/settings/nextcloud", json=NEXTCLOUD)

        assert response.status_code == 200
        assert response.json() == {
            "base_url": "https://cloud.example.com/remote.php/dav/files/me",
            "username": "me",
            "password_set": True,
        }
        assert "app-password-canary" not in client.get("/api/v1/settings/nextcloud").text
        assert "app-password-canary" not in client.get("/api/v1/audit").text

    def test_a_blank_password_keeps_the_stored_one(self, client: TestClient) -> None:
        client.put("/api/v1/settings/nextcloud", json=NEXTCLOUD)

        client.put(
            "/api/v1/settings/nextcloud", json={**NEXTCLOUD, "username": "me2", "password": ""}
        )

        with Session(db.engine) as session:
            connection = settings_service.connection_for(signed_in_settings(session))
        assert connection is not None
        assert (connection.username, connection.password) == ("me2", "app-password-canary")

    def test_the_first_save_needs_a_password(self, client: TestClient) -> None:
        response = client.put("/api/v1/settings/nextcloud", json={**NEXTCLOUD, "password": None})

        assert response.status_code == 422

    def test_rejects_plain_http_to_a_public_host(self, client: TestClient) -> None:
        response = client.put(
            "/api/v1/settings/nextcloud", json={**NEXTCLOUD, "base_url": "http://cloud.example.com"}
        )

        assert response.status_code == 422

    def test_test_signs_in_with_the_stored_password_when_left_blank(
        self, client: TestClient, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        client.put("/api/v1/settings/nextcloud", json=NEXTCLOUD)
        tried: list[webdav.Connection] = []
        monkeypatch.setattr(webdav, "check_connection", tried.append)

        response = client.post(
            "/api/v1/settings/nextcloud/test", json={**NEXTCLOUD, "password": ""}
        )

        assert response.status_code == 204
        assert [c.password for c in tried] == ["app-password-canary"]

    def test_filing_before_connecting_says_what_to_do(self, client: TestClient) -> None:
        response = client.get("/api/v1/folders/tree")

        assert response.status_code == 409
        assert response.json()["error"]["code"] == "nextcloud_not_configured"
