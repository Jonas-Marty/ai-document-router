"""Configurations: the API and the rules around the default (SPEC 6.3a, 8.13).

Every user has at most one default, it is never left without one while configurations
exist, and someone else's configuration is as missing as a deleted one.
"""

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session, select

from app import db
from app.models import (
    AiConfiguration,
    AppSettings,
    AuditAction,
    AuditEvent,
    Document,
    FilingStrategy,
    ReadingMethod,
)
from app.services import ai, configurations, transcribe
from app.services.times import utc_now
from tests.conftest import signed_in_user_id
from tests.test_documents_api import make_document

BASE = "/api/v1/configurations"


def endpoint(client: TestClient, name: str = "Local") -> str:
    response = client.post(
        "/api/v1/ai/endpoints",
        json={"name": name, "base_url": f"https://{name.lower()}.example.com/v1"},
    )
    assert response.status_code == 200, response.text
    return str(response.json()["id"])


def body(endpoint_id: str, name: str = "Daily", **overrides: Any) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "name": name,
        "reading_method": "text_layer",
        "reading": {"temperature": 0.1, "max_tokens": 4096},
        "filing_strategy": "one_call",
        "filing": {
            "endpoint_id": endpoint_id,
            "model_name": "qwen3",
            "temperature": 0.1,
            "max_tokens": 2048,
        },
        "max_examples": 8,
        "max_folders": 250,
        "max_text_chars": 6000,
    }
    payload.update(overrides)
    return payload


def create(client: TestClient, endpoint_id: str, name: str = "Daily", **overrides: Any) -> Any:
    response = client.post(BASE, json=body(endpoint_id, name, **overrides))
    assert response.status_code == 201, response.text
    return response.json()


class TestCreate:
    def test_the_first_configuration_becomes_the_default(self, client: TestClient) -> None:
        local = endpoint(client)

        first = create(client, local, "Daily")
        second = create(client, local, "Careful")

        assert first["is_default"] is True
        assert second["is_default"] is False
        assert first["filing"]["endpoint_name"] == "Local"

    def test_the_list_puts_the_default_first_then_sorts_by_name(self, client: TestClient) -> None:
        local = endpoint(client)
        create(client, local, "Zebra")
        create(client, local, "beta")
        create(client, local, "Alpha")

        assert [row["name"] for row in client.get(BASE).json()] == ["Zebra", "Alpha", "beta"]

    def test_a_blank_prompt_is_stored_as_the_built_in_one(self, client: TestClient) -> None:
        """Saved as null so the configuration follows improvements to the built-in prompt."""
        local = endpoint(client)
        created = create(
            client,
            local,
            filing={
                "endpoint_id": local,
                "model_name": "qwen3",
                "system_prompt": "   ",
                "instructions": "  Swisscom goes to Telecom.  ",
                "temperature": 0.1,
                "max_tokens": 2048,
            },
        )

        assert created["filing"]["system_prompt"] is None
        assert created["filing"]["instructions"] == "Swisscom goes to Telecom."

    def test_a_reading_without_a_model_drops_any_connection_sent_with_it(
        self, client: TestClient
    ) -> None:
        local = endpoint(client)
        created = create(
            client,
            local,
            reading={
                "endpoint_id": local,
                "model_name": "v",
                "temperature": 0.1,
                "max_tokens": 4096,
            },
        )

        assert created["reading"]["endpoint_id"] is None
        assert created["reading"]["model_name"] is None

    def test_is_audited(self, client: TestClient) -> None:
        create(client, endpoint(client))

        with Session(db.engine) as session:
            actions = session.exec(select(AuditEvent.action)).all()
        assert AuditAction.configuration_created in actions

    @pytest.mark.parametrize(
        ("overrides", "message"),
        [
            ({"name": "  "}, "name"),
            ({"reading_method": "vision"}, "Reading: choose a connection"),
            ({"max_text_chars": 500}, "Document text"),
            ({"max_folders": 5}, "Folders listed"),
            ({"max_examples": 21}, "Examples"),
        ],
    )
    def test_rejects_an_incomplete_or_out_of_range_configuration(
        self, client: TestClient, overrides: dict[str, Any], message: str
    ) -> None:
        response = client.post(BASE, json=body(endpoint(client), **overrides))

        assert response.status_code == 422
        assert message in response.json()["error"]["message"]

    def test_rejects_a_filing_step_without_a_model(self, client: TestClient) -> None:
        local = endpoint(client)
        filing = {"endpoint_id": local, "model_name": " ", "temperature": 0.1, "max_tokens": 2048}

        response = client.post(BASE, json=body(local, filing=filing))

        assert response.status_code == 422
        assert "Filing: choose a model" in response.json()["error"]["message"]

    def test_rejects_a_temperature_out_of_range(self, client: TestClient) -> None:
        local = endpoint(client)
        filing = {"endpoint_id": local, "model_name": "m", "temperature": 3, "max_tokens": 2048}

        assert client.post(BASE, json=body(local, filing=filing)).status_code == 422

    def test_rejects_a_duplicate_name(self, client: TestClient) -> None:
        local = endpoint(client)
        create(client, local, "Daily")

        response = client.post(BASE, json=body(local, "Daily"))

        assert response.status_code == 422
        assert "already" in response.json()["error"]["message"]

    def test_a_connection_that_does_not_exist_is_a_404(self, client: TestClient) -> None:
        endpoint(client)
        assert client.post(BASE, json=body("gone")).status_code == 404


class TestChange:
    def test_an_update_replaces_the_settings(self, client: TestClient) -> None:
        local = endpoint(client)
        tower = endpoint(client, "Tower")
        created = create(client, local)

        response = client.put(
            f"{BASE}/{created['id']}",
            json=body(
                local,
                "Daily",
                reading_method="vision",
                reading={
                    "endpoint_id": tower,
                    "model_name": "qwen2.5vl",
                    "temperature": 0,
                    "max_tokens": 8192,
                    "thinking": "off",
                },
                filing_strategy="two_calls",
                folder_system_prompt="Pick the folder.",
            ),
        )

        assert response.status_code == 200, response.text
        updated = response.json()
        assert updated["reading_method"] == "vision"
        assert updated["reading"]["endpoint_name"] == "Tower"
        assert updated["reading"]["thinking"] == "off"
        assert updated["filing_strategy"] == "two_calls"
        assert updated["folder_system_prompt"] == "Pick the folder."
        assert updated["is_default"] is True

    def test_a_copy_is_named_as_one_and_is_never_the_default(self, client: TestClient) -> None:
        local = endpoint(client)
        created = create(client, local)

        first = client.post(f"{BASE}/{created['id']}/duplicate").json()
        second = client.post(f"{BASE}/{created['id']}/duplicate").json()

        assert (first["name"], first["is_default"]) == ("Daily (copy)", False)
        assert second["name"] == "Daily (copy 2)"
        assert first["filing"]["model_name"] == "qwen3"

    def test_making_another_the_default_moves_the_flag(self, client: TestClient) -> None:
        local = endpoint(client)
        daily = create(client, local, "Daily")
        careful = create(client, local, "Careful")

        response = client.post(f"{BASE}/{careful['id']}/default")

        assert response.json()["is_default"] is True
        listed = {row["name"]: row["is_default"] for row in client.get(BASE).json()}
        assert listed == {"Careful": True, "Daily": False}
        assert daily["id"] != careful["id"]

    def test_the_default_cannot_be_deleted(self, client: TestClient) -> None:
        created = create(client, endpoint(client))

        response = client.delete(f"{BASE}/{created['id']}")

        assert response.status_code == 422
        assert "default" in response.json()["error"]["message"]

    def test_deleting_one_sends_the_documents_that_picked_it_back_to_the_default(
        self, client: TestClient
    ) -> None:
        local = endpoint(client)
        create(client, local, "Daily")
        careful = create(client, local, "Careful")
        with Session(db.engine) as session:
            document = make_document("scan.pdf")
            document.configuration_id = careful["id"]
            session.add(document)
            session.commit()
            document_id = document.id

        assert client.delete(f"{BASE}/{careful['id']}").status_code == 204

        with Session(db.engine) as session:
            stored = session.get(Document, document_id)
            assert stored is not None
            assert stored.configuration_id is None

    def test_someone_elses_configuration_is_a_404(self, client: TestClient) -> None:
        local = endpoint(client)
        with Session(db.engine) as session:
            theirs = AiConfiguration(user_id="someone-else", name="Theirs", created_at=utc_now())
            session.add(theirs)
            session.commit()
            theirs_id = theirs.id

        assert client.put(f"{BASE}/{theirs_id}", json=body(local)).status_code == 404
        assert client.delete(f"{BASE}/{theirs_id}").status_code == 404
        assert client.post(f"{BASE}/{theirs_id}/default").status_code == 404
        assert client.post(f"{BASE}/{theirs_id}/duplicate").status_code == 404
        assert client.get(BASE).json() == []


class TestPrompts:
    def test_the_built_in_prompts_are_the_ones_sent(self, client: TestClient) -> None:
        prompts = client.get(f"{BASE}/prompts").json()

        assert prompts == {
            "reading": transcribe.SYSTEM_PROMPT,
            "filing_one_call": ai.SYSTEM_PROMPT,
            "filing_fields": ai.FIELDS_SYSTEM_PROMPT,
            "filing_folder": ai.FOLDER_SYSTEM_PROMPT,
        }


class FakeWebDav:
    def list_dir(self, path: str) -> list[Any]:
        return []


class TestPreview:
    def preview(self, client: TestClient, **changes: Any) -> list[configurations.PreviewCall]:
        create(client, endpoint(client))
        with Session(db.engine) as session:
            configuration = session.exec(select(AiConfiguration)).one()
            for key, value in changes.items():
                setattr(configuration, key, value)
            app_settings = session.exec(
                select(AppSettings).where(AppSettings.user_id == signed_in_user_id())
            ).first() or AppSettings(user_id=signed_in_user_id(), allowed_root_folders=["/D"])
            return configurations.preview(session, FakeWebDav(), app_settings, configuration)  # type: ignore[arg-type]

    def test_a_text_layer_and_one_call_show_two_steps(self, client: TestClient) -> None:
        reading, filing = self.preview(client)

        assert reading.model_label is None
        assert filing.system_prompt == ai.SYSTEM_PROMPT
        assert filing.user_prompt is not None and "first 6,000 characters" in filing.user_prompt

    def test_two_calls_show_both_messages(self, client: TestClient) -> None:
        steps = self.preview(client, filing_strategy=FilingStrategy.two_calls)

        assert [step.step for step in steps] == ["Reading", "Fields", "Folder"]
        assert steps[2].system_prompt == ai.FOLDER_SYSTEM_PROMPT

    def test_a_vision_reading_shows_its_prompt(self, client: TestClient) -> None:
        reading, _ = self.preview(
            client, reading_method=ReadingMethod.vision, reading_instructions="Keep umlauts."
        )

        assert reading.system_prompt == transcribe.SYSTEM_PROMPT
        assert reading.user_prompt is not None and "Keep umlauts." in reading.user_prompt
