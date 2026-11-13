"""Calling one model on one connection, and which connections are in use (SPEC 6.3a).

What is pinned here is what the chains used to do and no longer may: a failure passes
straight through to the caller, which decides whether the document waits or fails. Nothing
in this module tries a second connection.
"""

from collections.abc import Iterator

import httpx
import pytest
from sqlalchemy.pool import StaticPool
from sqlmodel import Session, SQLModel, create_engine

from app.config import settings as config
from app.models import AiConfiguration, AiEndpoint, AiTask, ReadingMethod
from app.services import ai, connections, crypto, progress
from app.services.ai import AIUnavailable, ProposalRejected, Unreachable
from app.services.audit import Actor
from app.services.connections import ResolvedStep, call_step
from app.services.errors import ValidationError
from app.services.times import utc_now

# Rows need an owner; foreign keys are not enforced here, so no user row is needed.
OWNER = "owner-1"

TEST_ACTOR = Actor(user_id=None, label="test@example.com")


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
        user_id=OWNER,
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
        endpoint_name=name,
        endpoint_url=f"https://{name}/v1",
        api_key=None,
        model_name="m",
        endpoint_id=f"ep-{name}",
    )


class TestCallStep:
    def test_says_which_model_it_is_waiting_on(self) -> None:
        """For the review screen, while it shows "Waiting for the AI proposal"."""
        seen: list[str | None] = []

        def call(s: ResolvedStep) -> str:
            seen.append(progress.stage_of("doc"))
            return "answer"

        with progress.tracking("doc"):
            assert call_step(step("local"), call, AiTask.filing) == "answer"

        assert seen == ["Choosing a name and folder with local · m…"]
        assert progress.stage_of("doc") is None  # cleared once the analysis is over

    @pytest.mark.parametrize(
        "failure",
        [
            Unreachable("Connection refused."),
            AIUnavailable("500 from the server."),
            ProposalRejected("Not JSON."),
        ],
    )
    def test_every_failure_reaches_the_caller_unchanged(self, failure: Exception) -> None:
        def call(s: ResolvedStep) -> str:
            raise failure

        with pytest.raises(type(failure)) as raised:
            call_step(step("local"), call, AiTask.filing)
        assert raised.value is failure


class TestResolvedStep:
    def test_carries_the_decrypted_key_and_the_connection_capabilities(
        self, session: Session
    ) -> None:
        row = endpoint(session, "local", key=crypto.encrypt(config.secret_key, "secret-key"))
        row.context_limit = 16384

        resolved = ResolvedStep.of(row, "qwen3:8b")

        assert resolved.api_key == "secret-key"
        assert resolved.context_limit == 16384
        assert resolved.label == "local · qwen3:8b"


class TestReachability:
    def test_any_http_answer_counts_as_reachable(self) -> None:
        """A wrong key is for the call to fail on with its reason, not for waiting."""
        transport = httpx.MockTransport(lambda request: httpx.Response(401))
        with httpx.Client(transport=transport) as client:
            assert ai.probe(endpoint_url="https://h/v1", api_key=None, client=client)

    def test_no_connection_is_unreachable(self) -> None:
        def refuse(request: httpx.Request) -> httpx.Response:
            raise httpx.ConnectError("Connection refused", request=request)

        with httpx.Client(transport=httpx.MockTransport(refuse)) as client:
            assert not ai.probe(endpoint_url="https://h/v1", api_key=None, client=client)


class TestEndpointInUse:
    def test_names_every_configuration_that_uses_it(self, session: Session) -> None:
        tower = endpoint(session, "tower")
        for name, method, reading, filing in [
            ("Reads on tower", ReadingMethod.vision, tower.id, None),
            ("Files on tower", ReadingMethod.text_layer, None, tower.id),
            ("Elsewhere", ReadingMethod.text_layer, None, "other"),
        ]:
            session.add(
                AiConfiguration(
                    user_id=OWNER,
                    name=name,
                    reading_method=method,
                    reading_endpoint_id=reading,
                    reading_model_name="v" if reading else None,
                    filing_endpoint_id=filing,
                    filing_model_name="m",
                    created_at=utc_now(),
                )
            )
        session.commit()

        assert connections.endpoint_in_use(session, tower.id) == [
            "Files on tower",
            "Reads on tower",
        ]

    def test_deleting_one_in_use_is_refused_by_name(self, session: Session) -> None:
        tower = endpoint(session, "tower")
        session.add(
            AiConfiguration(
                user_id=OWNER,
                name="Daily",
                filing_endpoint_id=tower.id,
                filing_model_name="m",
                created_at=utc_now(),
            )
        )
        session.commit()

        with pytest.raises(ValidationError, match="'Daily'"):
            connections.delete_endpoint(session, OWNER, tower.id, actor=TEST_ACTOR)

    def test_an_unused_endpoint_can_go(self, session: Session) -> None:
        tower = endpoint(session, "tower")
        connections.delete_endpoint(session, OWNER, tower.id, actor=TEST_ACTOR)
        assert session.get(AiEndpoint, tower.id) is None
