"""Call parameters, the context limit, JSON mode and cost (SPEC 6.3b).

The point of most of these: Ollama cuts an overlong prompt silently, from the start where
the instructions sit, so the app has to refuse before sending rather than learn afterwards.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.models import AiJsonMode, AiTask
from app.services import ai, ai_usage
from app.services.ai import AIUnavailable, PromptTooLong, ProposalRejected, ReplyCutOff
from app.services.ai_tasks import ResolvedStep, _is_cooling, run_chain

ROOTS = ["/Documents"]
FOLDERS = ["/Documents", "/Documents/Bank", "/Documents/Versicherung"]
REPLY = {
    "reasoning_text": "Letterhead says WIR Bank.",
    "document_date": "2026-05-13",
    "target_folder_path": "/Documents/Bank",
    "suggested_name": "2026.05.13 WIR Bank Kontoeröffnungsbestätigung",
    "confidence_score": 0.9,
}


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def reply_body(content: str, finish_reason: str = "stop", **extra: Any) -> dict[str, Any]:
    return {
        "choices": [{"message": {"content": content}, "finish_reason": finish_reason}],
        **extra,
    }


class Recorder:
    """A mock transport that keeps every request body it was sent."""

    def __init__(self, *responses: httpx.Response) -> None:
        self.bodies: list[dict[str, Any]] = []
        self.responses = list(responses)

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.bodies.append(json.loads(request.content))
        if len(self.responses) > 1:
            return self.responses.pop(0)
        return self.responses[0]

    def client(self) -> httpx.Client:
        return httpx.Client(transport=httpx.MockTransport(self))


def propose(recorder: Recorder, prompt: str = "p", **kwargs: Any) -> ai.Proposal:
    with recorder.client() as client:
        return ai.request_proposal(
            endpoint_url="http://tower:11434/v1",
            model_name="qwen3:8b",
            api_key=None,
            prompt=prompt,
            allowed_roots=ROOTS,
            client=client,
            **kwargs,
        )


def ok() -> httpx.Response:
    return httpx.Response(200, json=reply_body(json.dumps(REPLY)))


class TestParameters:
    def test_every_filing_call_sends_temperature_and_an_output_limit(self) -> None:
        recorder = Recorder(ok())
        propose(recorder)

        (body,) = recorder.bodies
        assert body["temperature"] == ai.FILING_PARAMETERS.temperature
        assert body["max_tokens"] == ai.FILING_PARAMETERS.max_tokens

    def test_every_reading_call_sends_them_too(self) -> None:
        recorder = Recorder(httpx.Response(200, json=reply_body("# Page")))
        with recorder.client() as client:
            ai.request_completion(
                endpoint_url="http://tower:11434/v1",
                model_name="qwen2.5vl",
                api_key=None,
                system_prompt="s",
                prompt="p",
                images=[b"png"],
                client=client,
            )

        (body,) = recorder.bodies
        assert body["temperature"] == ai.EXTRACTION_PARAMETERS.temperature
        assert body["max_tokens"] == ai.EXTRACTION_PARAMETERS.max_tokens


class TestContextLimit:
    def test_a_prompt_that_cannot_fit_is_never_sent(self) -> None:
        recorder = Recorder(ok())

        with pytest.raises(PromptTooLong, match="4,096"):
            propose(recorder, prompt="x" * 20_000, context_limit=4096)

        assert recorder.bodies == []

    def test_the_reply_limit_shrinks_to_what_the_context_leaves(self) -> None:
        recorder = Recorder(ok())
        prompt = "x" * 6000
        estimate = ai.estimate_prompt_tokens(ai.SYSTEM_PROMPT, prompt)

        propose(recorder, prompt=prompt, context_limit=estimate + 1000)

        assert recorder.bodies[0]["max_tokens"] == 1000

    def test_no_limit_means_no_check(self) -> None:
        recorder = Recorder(ok())
        propose(recorder, prompt="x" * 200_000)
        assert recorder.bodies[0]["max_tokens"] == ai.FILING_PARAMETERS.max_tokens

    def test_page_images_count_towards_the_estimate(self) -> None:
        recorder = Recorder(httpx.Response(200, json=reply_body("# Page")))
        with recorder.client() as client, pytest.raises(PromptTooLong):
            ai.request_completion(
                endpoint_url="http://tower:11434/v1",
                model_name="qwen2.5vl",
                api_key=None,
                system_prompt="s",
                prompt="p",
                images=[b"a", b"b"],
                client=client,
                context_limit=4096,
            )
        assert recorder.bodies == []

    def test_a_too_long_prompt_moves_on_without_cooling_the_endpoint(self) -> None:
        """The next document may well fit; skipping the endpoint for five minutes would send
        it to the fallback for nothing."""
        local = ResolvedStep(
            endpoint_name="Tower", endpoint_url="http://tower/v1", api_key=None, model_name="m"
        )
        hosted = ResolvedStep(
            endpoint_name="Hosted", endpoint_url="https://h/v1", api_key=None, model_name="m"
        )

        def call(step: ResolvedStep) -> str:
            if step is local:
                raise PromptTooLong("too long")
            return "answer"

        assert run_chain([local, hosted], call, AiTask.filing) == "answer"
        assert not _is_cooling(local)

    def test_the_failure_names_the_step_when_nothing_else_is_left(self) -> None:
        local = ResolvedStep(
            endpoint_name="Tower", endpoint_url="http://tower/v1", api_key=None, model_name="m"
        )

        def call(step: ResolvedStep) -> str:
            raise PromptTooLong("The prompt is about 9,000 tokens")

        with pytest.raises(AIUnavailable, match="Tower · m .The prompt is about 9,000"):
            run_chain([local], call, AiTask.filing)


class TestCutOff:
    def test_a_filing_reply_stopped_at_the_limit_is_rejected_by_name(self) -> None:
        recorder = Recorder(httpx.Response(200, json=reply_body('{"reasoning', "length")))

        with pytest.raises(ProposalRejected, match="cut off at the output limit"):
            propose(recorder)

    def test_a_reading_stopped_at_the_limit_is_not_a_reading(self) -> None:
        recorder = Recorder(httpx.Response(200, json=reply_body("# Half a page", "length")))
        with recorder.client() as client, pytest.raises(ReplyCutOff):
            ai.request_completion(
                endpoint_url="http://tower:11434/v1",
                model_name="qwen2.5vl",
                api_key=None,
                system_prompt="s",
                prompt="p",
                client=client,
            )


class TestJsonMode:
    def test_auto_asks_for_the_schema_first_with_the_folders_as_an_enum(self) -> None:
        recorder = Recorder(ok())
        propose(recorder, folder_choices=FOLDERS)

        response_format = recorder.bodies[0]["response_format"]
        assert response_format["type"] == "json_schema"
        schema = response_format["json_schema"]["schema"]
        assert schema["properties"]["target_folder_path"]["enum"] == FOLDERS
        assert schema["additionalProperties"] is False

    def test_a_fixed_mode_asks_only_that_way(self) -> None:
        recorder = Recorder(httpx.Response(400, json={"error": {"message": "no json_object"}}))

        with pytest.raises(AIUnavailable, match="no json_object"):
            propose(recorder, json_mode=AiJsonMode.json_object)

        assert [b["response_format"]["type"] for b in recorder.bodies] == ["json_object"]

    def test_none_sends_no_response_format(self) -> None:
        recorder = Recorder(ok())
        propose(recorder, json_mode=AiJsonMode.none)
        assert "response_format" not in recorder.bodies[0]

    def test_the_prompt_lists_exactly_the_folders_the_schema_allows(self) -> None:
        deep = [f"/Documents/{n}" for n in range(ai.MAX_TREE_FOLDERS + 5)]
        choices = ai.prompt_folders(["/Documents/a/b/c/d/e", *deep])

        assert len(choices) == ai.MAX_TREE_FOLDERS
        assert "/Documents/a/b/c/d/e" not in choices
        prompt = ai.build_prompt("text", ["/Documents/a/b/c/d/e", *deep], [], None)
        assert all(path in prompt for path in choices)


class TestCost:
    def test_priced_from_tokens(self) -> None:
        usage = ai.TokenUsage(prompt_tokens=2_000_000, completion_tokens=500_000)
        assert ai.call_cost(usage, 0.5, 2.0) == pytest.approx(2.0)

    def test_no_price_is_no_cost_rather_than_zero(self) -> None:
        assert ai.call_cost(ai.TokenUsage(10, 10), None, None) is None

    def test_no_token_counts_is_no_cost(self) -> None:
        assert ai.call_cost(None, 1.0, 1.0) is None

    def test_one_price_alone_still_prices_the_call(self) -> None:
        assert ai.call_cost(ai.TokenUsage(0, 1_000_000), None, 3.0) == pytest.approx(3.0)

    def test_the_cost_is_stored_with_the_call_and_summed(self, session: Session) -> None:
        priced = ResolvedStep(
            endpoint_name="Hosted",
            endpoint_url="https://h/v1",
            api_key=None,
            model_name="big",
            endpoint_id="hosted",
            price_input=1.0,
            price_output=4.0,
        )
        meter = ai.UsageMeter()
        recorder = priced.usage(meter, AiTask.filing)
        recorder.record(ai.TokenUsage(1_000_000, 250_000), 900)
        recorder.record(ai.TokenUsage(1_000_000, 0), 900)

        ai_usage.store(session, [meter], user_id="owner", document_id="doc")
        session.commit()

        today = datetime.now(UTC).date()
        report = ai_usage.report(
            session, "owner", today - timedelta(days=1), today + timedelta(days=1)
        )
        assert sum(row.cost or 0 for row in report.rows) == pytest.approx(3.0)
