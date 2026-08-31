"""Tests for turning a document's pages into markdown.

SPEC 6.3a stage one. Pages are rendered and sent in batches, and the batches are stitched
back into one document -- so the things worth pinning are the batching and the stitching.
"""

from typing import Any

import pytest

from app.services import ai, extraction, transcribe
from app.services.ai import AIUnavailable
from app.services.ai_tasks import ResolvedStep


def step(name: str = "local") -> ResolvedStep:
    return ResolvedStep(
        endpoint_name=name, endpoint_url=f"https://{name}/v1", api_key=None, model_name="qwen-vl"
    )


@pytest.fixture
def pages(monkeypatch: pytest.MonkeyPatch) -> list[bytes]:
    rendered = [b"page-1", b"page-2", b"page-3"]
    monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: rendered)
    return rendered


class TestTranscribe:
    def test_stitches_the_batches_into_one_document(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        sent: list[list[bytes]] = []

        def completion(**kwargs: Any) -> str:
            sent.append(kwargs["images"])
            return f"# Page {len(sent)}"

        monkeypatch.setattr(ai, "request_completion", completion)

        result = transcribe.transcribe(b"%PDF", [step()])

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

        assert transcribe.transcribe(b"%PDF", [step()]).markdown == "# Invoice\n\nTotal"

    def test_falls_through_to_the_next_endpoint_when_the_first_is_down(
        self, pages: list[bytes], monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: [b"page-1"])

        def completion(**kwargs: Any) -> str:
            if kwargs["endpoint_url"] == "https://local/v1":
                raise AIUnavailable("Connection refused.")
            return "# Read by the fallback"

        monkeypatch.setattr(ai, "request_completion", completion)

        result = transcribe.transcribe(b"%PDF", [step("local"), step("cloud")])

        assert result.markdown == "# Read by the fallback"
        assert result.model_label == "cloud · qwen-vl"

    def test_a_file_with_no_renderable_pages_says_so(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(extraction, "render_pages", lambda data, max_pages: [])

        with pytest.raises(extraction.RenderUnavailable):
            transcribe.transcribe(b"not a pdf", [step()])
