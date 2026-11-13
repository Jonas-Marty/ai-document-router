"""Tests for turning a document's pages into markdown.

SPEC 6.3a stage one. Pages are rendered and sent in batches, and the batches are stitched
back into one document -- so the things worth pinning are the batching and the stitching.
"""

from typing import Any

import pytest

from app.models import AiThinking
from app.services import ai, extraction, transcribe
from app.services.ai import Unreachable
from app.services.connections import ResolvedStep


def step(name: str = "local") -> ResolvedStep:
    return ResolvedStep(
        endpoint_name=name, endpoint_url=f"https://{name}/v1", api_key=None, model_name="qwen-vl"
    )


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    rendered = [b"page-1", b"page-2", b"page-3"]
    monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: rendered)
    return rendered


class TestInstructions:
    def test_are_sent_with_every_batch(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        prompts: list[str] = []

        def completion(**kwargs: Any) -> str:
            prompts.append(kwargs["prompt"])
            return "text"

        monkeypatch.setattr(ai, "request_completion", completion)

        transcribe.transcribe(b"%PDF", step(), instructions="Mostly German. Keep the umlauts.")

        assert len(prompts) == 2
        assert all(p.startswith(transcribe.USER_PROMPT) for p in prompts)
        assert all("Mostly German. Keep the umlauts." in p for p in prompts)

    def test_leave_the_prompt_alone_when_there_are_none(self) -> None:
        assert transcribe.user_prompt(None) == transcribe.USER_PROMPT


class TestTranscribe:
    def test_stitches_the_batches_into_one_document(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[list[bytes]] = []

        def completion(**kwargs: Any) -> str:
            sent.append(kwargs["images"])
            return f"# Page {len(sent)}"

        monkeypatch.setattr(ai, "request_completion", completion)

        result = transcribe.transcribe(b"%PDF", step())

        # Three pages, two per request: one full batch and one remainder.
        assert sent == [[b"page-1", b"page-2"], [b"page-3"]]
        assert result.markdown == "# Page 1\n\n# Page 2"
        assert result.model_label == "local · qwen-vl"

    def test_unwraps_a_code_fence_the_model_added_anyway(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: [b"page-1"])
        monkeypatch.setattr(
            ai, "request_completion", lambda **kwargs: "```markdown\n# Invoice\n\nTotal\n```"
        )

        assert transcribe.transcribe(b"%PDF", step()).markdown == "# Invoice\n\nTotal"

    def test_an_unreachable_connection_is_not_replaced_by_another(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: [b"page-1"])

        def completion(**kwargs: Any) -> str:
            raise Unreachable("Connection refused.")

        monkeypatch.setattr(ai, "request_completion", completion)

        with pytest.raises(Unreachable):
            transcribe.transcribe(b"%PDF", step("local"))

    def test_sends_the_steps_own_prompt_and_parameters(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        seen: list[dict[str, Any]] = []

        def completion(**kwargs: Any) -> str:
            seen.append(kwargs)
            return "text"

        monkeypatch.setattr(ai, "request_completion", completion)
        parameters = ai.EXTRACTION_PARAMETERS.with_settings(0.3, 1000, AiThinking.off)

        transcribe.transcribe(b"%PDF", step(), system_prompt="Read it.", parameters=parameters)

        assert {call["system_prompt"] for call in seen} == {"Read it."}
        assert {call["parameters"] for call in seen} == {parameters}

    def test_a_file_with_no_renderable_pages_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: [])

        with pytest.raises(extraction.RenderUnavailable):
            transcribe.transcribe(b"not a pdf", step())
