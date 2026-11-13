"""Prometheus metrics (SPEC 6.8).

Every metric the app exposes is defined here, on prometheus_client's default registry, which
already carries the process and garbage-collector collectors. Call sites bump them with one
line after the thing they count has actually happened -- after the commit, not before it.

One registry is enough because the app runs as a single uvicorn worker with the poller
in-process (deploy/entrypoint.sh). A second worker would need prometheus_client's
multiprocess mode; see DECISIONS.md.

Labels stay bounded: routes are templates, never raw paths; AI steps are labelled the way
Settings names them ("Workshop PC · qwen3"), a set the admin controls.
"""

import hmac
import logging
from collections.abc import Callable, Iterator

from prometheus_client import CONTENT_TYPE_LATEST, REGISTRY, Counter, Gauge, Histogram
from prometheus_client import generate_latest as _generate_latest
from prometheus_client.core import GaugeMetricFamily, Metric
from prometheus_client.registry import Collector
from sqlalchemy import Engine, func
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, select

from app.models import Document, DocumentStatus, ProposalStatus
from app.services.errors import AuthenticationRequired, NotFoundError

logger = logging.getLogger(__name__)

CONTENT_TYPE = CONTENT_TYPE_LATEST

# Model calls take seconds to minutes (a local vision model on a page batch); the default
# buckets top out at 10s and would put every transcription in +Inf.
_SLOW_BUCKETS = (0.5, 1, 2.5, 5, 10, 20, 30, 60, 120, 300, 600)

# --- HTTP ------------------------------------------------------------------------------------

http_requests = Counter(
    "docrouter_http_requests_total",
    "HTTP requests handled, by route template and status code.",
    ["method", "route", "status"],
)
http_request_duration = Histogram(
    "docrouter_http_request_duration_seconds",
    "Time to answer an HTTP request, streaming included.",
    ["method", "route"],
)

# --- AI --------------------------------------------------------------------------------------

ai_requests = Counter(
    "docrouter_ai_requests_total",
    "Model calls, by outcome: ok, rejected (answered unusably), unavailable, unreachable "
    "(no connection; the document waits), or too_long (not sent: the prompt would not fit "
    "the context limit).",
    ["task", "step", "outcome"],
)
ai_request_duration = Histogram(
    "docrouter_ai_request_duration_seconds",
    "Time a chain step took, including every page batch it sent.",
    ["task", "step"],
    buckets=_SLOW_BUCKETS,
)
ai_tokens = Counter(
    "docrouter_ai_tokens_total",
    "Tokens reported by model replies, by kind: prompt or completion.",
    ["task", "step", "kind"],
)
ai_unreported_calls = Counter(
    "docrouter_ai_unreported_usage_calls_total",
    "Model replies that carried no token counts, so the token total is a minimum.",
    ["task", "step"],
)
# --- Pipeline --------------------------------------------------------------------------------

analyses = Counter(
    "docrouter_analyses_total", "Documents analysed, by outcome: ready or failed.", ["outcome"]
)
documents_ingested = Counter(
    "docrouter_documents_ingested_total", "New documents found in the watch folder."
)
documents_routed = Counter(
    "docrouter_documents_routed_total",
    "Review decisions carried out, by action: moved, trashed, skipped or reverted.",
    ["action"],
)
ocr_runs = Counter(
    "docrouter_ocr_runs_total",
    "Searchable copies attempted, by outcome: ready or failed.",
    ["outcome"],
)
ocr_duration = Histogram(
    "docrouter_ocr_duration_seconds",
    "Time ocrmypdf took to produce a searchable copy.",
    buckets=_SLOW_BUCKETS,
)

# --- Poller ----------------------------------------------------------------------------------

poller_ticks = Counter(
    "docrouter_poller_ticks_total", "Poller runs, by outcome: ok, failed or skipped.", ["outcome"]
)
poller_tick_duration = Histogram(
    "docrouter_poller_tick_duration_seconds",
    "Time one poller run took, from listing the watch folder to the last proposal.",
    buckets=_SLOW_BUCKETS,
)
poller_last_tick = Gauge(
    "docrouter_poller_last_tick_timestamp_seconds",
    "Unix time the last successful poller run finished. Alert on it growing stale.",
)

# --- WebDAV ----------------------------------------------------------------------------------

webdav_operations = Counter(
    "docrouter_webdav_operations_total",
    "WebDAV operations, by outcome: ok, not_found, conflict, unreachable or error.",
    ["operation", "outcome"],
)
webdav_operation_duration = Histogram(
    "docrouter_webdav_operation_duration_seconds",
    "Time one WebDAV operation took.",
    ["operation"],
)
webdav_reachable = Gauge(
    "docrouter_webdav_reachable",
    "1 when the last contact with the WebDAV server got an answer, 0 when it could not connect.",
)

# --- Auth ------------------------------------------------------------------------------------

auth_logins = Counter(
    "docrouter_auth_logins_total",
    "Sign-in attempts, by method (password, oidc) and outcome (success, failure).",
    ["method", "outcome"],
)


def _prime() -> None:
    """Create the series an alert is likely to divide by, at zero.

    A counter with labels has no series until its first increment, so a ratio like "failed
    analyses / all analyses" would be empty rather than 0 on a quiet instance.
    """
    for outcome in ("ready", "failed"):
        analyses.labels(outcome)
        ocr_runs.labels(outcome)
    for action in ("moved", "trashed", "skipped", "reverted"):
        documents_routed.labels(action)
    for outcome in ("ok", "failed", "skipped"):
        poller_ticks.labels(outcome)
    for method in ("password", "oidc"):
        for outcome in ("success", "failure"):
            auth_logins.labels(method, outcome)


_prime()


# --- Scrape-time gauges ----------------------------------------------------------------------


class QueueCollector(Collector):
    """Document counts, read from the database when Prometheus scrapes.

    Counted at scrape time rather than kept as gauges, because documents change status in
    several places and a gauge updated by hand at each would drift. `engine` is a callable so
    the collector follows `db.engine` if it is swapped, as the tests do.
    """

    def __init__(self, engine: Callable[[], Engine]) -> None:
        self._engine = engine

    def collect(self) -> Iterator[Metric]:
        # Imported here: this module is imported by ai.py and webdav.py to count calls, and
        # documents.py sits above both (via audit), so a top-level import would be a cycle.
        from app.services.documents import QUEUED_STATUSES

        try:
            with Session(self._engine()) as session:
                by_status = dict(
                    session.exec(
                        select(Document.status, func.count()).group_by(col(Document.status))
                    ).all()
                )
                by_proposal = dict(
                    session.exec(
                        select(Document.proposal_status, func.count())
                        .where(col(Document.status).in_(QUEUED_STATUSES))
                        .group_by(col(Document.proposal_status))
                    ).all()
                )
        except SQLAlchemyError:
            # A scrape that fails outright loses every other metric with it, including the
            # ones that would explain why the database is unhappy.
            logger.warning("Could not count documents for /metrics.", exc_info=True)
            return

        documents = GaugeMetricFamily(
            "docrouter_documents", "Documents known to the app, by status.", labels=["status"]
        )
        for status in DocumentStatus:
            documents.add_metric([status.value], by_status.get(status, 0))
        yield documents

        queue = GaugeMetricFamily(
            "docrouter_queue_proposals",
            "Documents waiting for review, by the state of their AI proposal.",
            labels=["proposal_status"],
        )
        for proposal_status in ProposalStatus:
            queue.add_metric([proposal_status.value], by_proposal.get(proposal_status, 0))
        yield queue


_queue_collector: QueueCollector | None = None


def register_queue_collector(engine: Callable[[], Engine]) -> None:
    """Idempotent: the default registry refuses a second collector with the same names."""
    global _queue_collector
    if _queue_collector is None:
        _queue_collector = QueueCollector(engine)
        REGISTRY.register(_queue_collector)


# --- Exposition ------------------------------------------------------------------------------


def check_scrape_token(configured: str, authorization: str | None) -> None:
    """Gate /metrics. Off unless METRICS_TOKEN is set; then a bearer token must match.

    404 rather than 401 while disabled, so an instance that never opted in does not even
    advertise the endpoint. Compared in constant time, since this is a password in all but name.
    """
    if not configured:
        raise NotFoundError("Not Found")
    scheme, _, supplied = (authorization or "").partition(" ")
    if scheme.lower() != "bearer" or not hmac.compare_digest(
        supplied.strip().encode(), configured.encode()
    ):
        raise AuthenticationRequired("A valid metrics token is required.")


def generate_latest() -> bytes:
    return _generate_latest(REGISTRY)
