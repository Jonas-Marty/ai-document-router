"""GET /ai/context: what each task is sent, built from the current settings."""

from collections.abc import Iterator
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session

from app import db
from app.deps import get_webdav
from app.main import app
from app.models import AiEndpoint, AiTask, AppSettings
from app.services import ai, ai_context, ai_tasks, transcribe
from app.services.audit import Actor
from app.services.webdav import WebDavEntry

TEST_ACTOR = Actor(user_id=None, label="test@example.com")


class FakeWebDav:
    def list_dir(self, path: str) -> list[WebDavEntry]:
        if path == "/Documents":
            return [
                WebDavEntry(path="/Documents/Finance", name="Finance", is_dir=True),
                WebDavEntry(
                    path="/Documents/2026.01.05 Acme Invoice.pdf",
                    name="2026.01.05 Acme Invoice.pdf",
                    is_dir=False,
                ),
            ]
        return []


@pytest.fixture
def configured(client: TestClient) -> Iterator[TestClient]:
    with Session(db.engine) as session:
        settings = session.get(AppSettings, 1)
        assert settings is not None
        settings.allowed_root_folders = ["/Documents"]
        settings.trash_folder_path = "/Trash"
        settings.filename_pattern_hint = "YYYY.MM.DD Sender Subject"
        settings.filing_instructions = "Insurance goes to /Documents/Versicherung."
        settings.extraction_instructions = "Keep the umlauts."
        session.add(settings)
        session.add(
            AiEndpoint(
                id="ep",
                name="Workshop PC",
                base_url="http://192.168.1.5/v1",
                created_at=datetime(2026, 9, 1),
            )
        )
        session.commit()
        ai_tasks.replace_chain(session, AiTask.filing, [("ep", "qwen3")], actor=TEST_ACTOR)

    app.dependency_overrides[get_webdav] = lambda: FakeWebDav()
    yield client
    app.dependency_overrides.pop(get_webdav, None)


def test_reports_both_tasks_as_they_would_be_sent(configured: TestClient) -> None:
    response = configured.get("/api/v1/ai/context")

    assert response.status_code == 200
    extraction, filing = response.json()

    assert extraction["task"] == "extraction"
    assert extraction["chain"] == []
    assert extraction["system_prompt"] == transcribe.SYSTEM_PROMPT
    assert "Keep the umlauts." in extraction["user_prompt"]

    assert filing["task"] == "filing"
    assert filing["chain"] == ["Workshop PC · qwen3"]
    assert filing["system_prompt"] == ai.SYSTEM_PROMPT
    prompt = filing["user_prompt"]
    assert "/Documents/Finance" in prompt
    assert "2026.01.05 Acme Invoice.pdf" in prompt
    assert "YYYY.MM.DD Sender Subject" in prompt
    assert "Insurance goes to /Documents/Versicherung." in prompt
    assert ai_context.DOCUMENT_TEXT_PLACEHOLDER in prompt


def test_needs_a_signed_in_user(anonymous_client: TestClient) -> None:
    assert anonymous_client.get("/api/v1/ai/context").status_code == 401
