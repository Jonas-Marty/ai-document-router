"""Tests for task chains and the fallback walk down them.

SPEC 6.3a. The behaviour worth pinning is which failures move to the next endpoint: an
unreachable server should, a model that answered with something unusable should not.
"""

from collections.abc import Iterator

import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine, select

from app.config import settings as config
from app.models import AiEndpoint, AiTask, AiTaskStep
from app.services import ai_tasks, crypto
from app.services.ai import AIUnavailable, ProposalRejected
from app.services.ai_tasks import NoStepsConfigured, ResolvedStep, replace_chain, run_chain
from app.services.errors import NotFoundError, ValidationError
from app.services.times import utc_now


@pytest.fixture
def session() -> Iterator[Session]:
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def endpoint(session: Session, name: str, *, key: bytes | None = None) -> AiEndpoint:
    row = AiEndpoint(
        name=name,
        base_url=f"https://{name}.example.com/v1",
        api_key_encrypted=key,
        created_at=utc_now(),
    )
    session.add(row)
    session.commit()
    return row


def step(name: str) -> ResolvedStep:
    return ResolvedStep(
        endpoint_name=name, endpoint_url=f"https://{name}/v1", api_key=None, model_name="m"
    )


class TestRunChain:
    def test_uses_the_first_step_and_leaves_the_rest_alone(self) -> None:
        tried: list[str] = []

        def call(s: ResolvedStep) -> str:
            tried.append(s.endpoint_name)
            return "answer"

        assert run_chain([step("local"), step("cloud")], call, AiTask.filing) == "answer"
        assert tried == ["local"]

    def test_an_unreachable_endpoint_falls_through_to_the_next(self) -> None:
        tried: list[str] = []

        def call(s: ResolvedStep) -> str:
            tried.append(s.endpoint_name)
            if s.endpoint_name == "local":
                raise AIUnavailable("Connection refused.")
            return "answer"

        assert run_chain([step("local"), step("cloud")], call, AiTask.filing) == "answer"
        assert tried == ["local", "cloud"]

    def test_a_model_that_answered_badly_stops_the_chain(self) -> None:
        """A reply that failed validation would fail the same way everywhere. Trying the next
        endpoint would hide the problem and spend a second call doing it."""
        tried: list[str] = []

        def call(s: ResolvedStep) -> str:
            tried.append(s.endpoint_name)
            raise ProposalRejected("The model chose a folder outside the allowed roots.")

        with pytest.raises(ProposalRejected):
            run_chain([step("local"), step("cloud")], call, AiTask.filing)
        assert tried == ["local"]

    def test_every_step_failing_names_all_of_them(self) -> None:
        def call(s: ResolvedStep) -> str:
            raise AIUnavailable(f"{s.endpoint_name} is down.")

        with pytest.raises(AIUnavailable) as caught:
            run_chain([step("local"), step("cloud")], call, AiTask.filing)

        assert "local · m" in caught.value.message
        assert "cloud · m" in caught.value.message

    def test_an_empty_chain_says_so_rather_than_failing_obscurely(self) -> None:
        with pytest.raises(NoStepsConfigured) as caught:
            run_chain([], lambda s: "answer", AiTask.extraction)

        assert "extraction" in caught.value.message
        assert "Settings" in caught.value.message


class TestResolveChain:
    def test_returns_steps_in_position_order_with_the_key_decrypted(self, session: Session) -> None:
        local = endpoint(session, "local", key=crypto.encrypt(config.secret_key, "secret-key"))
        cloud = endpoint(session, "cloud")
        replace_chain(session, AiTask.filing, [(local.id, "qwen"), (cloud.id, "gpt-4o")])

        resolved = ai_tasks.resolve_chain(session, AiTask.filing)

        assert [s.endpoint_name for s in resolved] == ["local", "cloud"]
        assert resolved[0].api_key == "secret-key"
        assert resolved[1].api_key is None
        assert resolved[0].model_name == "qwen"

    def test_ignores_steps_belonging_to_the_other_task(self, session: Session) -> None:
        local = endpoint(session, "local")
        replace_chain(session, AiTask.extraction, [(local.id, "got-ocr")])

        assert ai_tasks.resolve_chain(session, AiTask.filing) == []
        assert ai_tasks.resolve_chain(session, AiTask.extraction)[0].model_name == "got-ocr"


class TestReplaceChain:
    def test_replaces_the_whole_chain_rather_than_appending(self, session: Session) -> None:
        local = endpoint(session, "local")
        cloud = endpoint(session, "cloud")
        replace_chain(session, AiTask.filing, [(local.id, "a"), (cloud.id, "b")])
        replace_chain(session, AiTask.filing, [(cloud.id, "c")])

        rows = session.exec(select(AiTaskStep)).all()
        assert [(r.position, r.model_name) for r in rows] == [(0, "c")]

    def test_rejects_an_endpoint_that_does_not_exist(self, session: Session) -> None:
        with pytest.raises(NotFoundError):
            replace_chain(session, AiTask.filing, [("missing", "m")])

    def test_rejects_a_blank_model_name(self, session: Session) -> None:
        local = endpoint(session, "local")
        with pytest.raises(ValidationError):
            replace_chain(session, AiTask.filing, [(local.id, "   ")])

    def test_an_empty_list_clears_the_chain(self, session: Session) -> None:
        local = endpoint(session, "local")
        replace_chain(session, AiTask.filing, [(local.id, "a")])
        replace_chain(session, AiTask.filing, [])

        assert ai_tasks.resolve_chain(session, AiTask.filing) == []


class TestEndpointInUse:
    def test_names_every_task_that_would_lose_a_step(self, session: Session) -> None:
        local = endpoint(session, "local")
        replace_chain(session, AiTask.filing, [(local.id, "a")])
        replace_chain(session, AiTask.extraction, [(local.id, "b")])

        assert ai_tasks.endpoint_in_use(session, local.id) == [AiTask.extraction, AiTask.filing]

    def test_is_empty_for_an_endpoint_assigned_to_nothing(self, session: Session) -> None:
        assert ai_tasks.endpoint_in_use(session, endpoint(session, "spare").id) == []
