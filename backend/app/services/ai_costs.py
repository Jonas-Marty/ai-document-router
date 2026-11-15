"""What an analysis costs, for the Activity page (SPEC 8.11).

Built from `ai_call`, which records every model call with its cost, the run it belonged to,
where the run came from and how it ended. A run is one store of calls; an analysis is a
finished run (ready or failed) together with any runs before it that stopped to wait for a
connection, because the reading those paid for went into it.

Each analysis is a document's first, a re-analysis, or a failure; Lab runs, comparisons and
calls recorded before sources were kept are "other". Figures are in one currency -- the
one most of the period's priced calls were billed in -- since two currencies cannot be
added; what was spent in any other is listed beside them.
"""

from collections import Counter, defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta

from sqlmodel import Session, col, select

from app.models import (
    AiCall,
    AiConfiguration,
    Document,
    HistoryAction,
    HistoryEntry,
    Proposal,
)
from app.services import ai
from app.services.ai_usage import ANALYSIS, MAX_DAYS
from app.services.documents import QUEUED_STATUSES
from app.services.errors import ValidationError

FIRST = "first"
REANALYSIS = "reanalysis"
FAILED = "failed"
OTHER = "other"
KINDS = (FIRST, REANALYSIS, FAILED, OTHER)
EXPENSIVE = 5
_REMOVED: tuple[str, bool, int | None] = ("Removed document", False, None)

Cost = tuple[float | None, str | None]
# A cost in the report's currency, else None.
Main = Callable[[Cost], float | None]


@dataclass
class _Run:
    run_id: str
    occurred_at: datetime
    source: str | None
    document_id: str | None
    outcome: str | None
    configuration_id: str | None
    configuration_name: str | None
    calls: list[AiCall] = field(default_factory=list)
    kind: str = OTHER

    @property
    def finished(self) -> bool:
        return self.outcome in ("ready", "failed")


@dataclass
class Analysis:
    """One finished analysis, with the waiting runs whose readings went into it."""

    run: _Run
    calls: list[AiCall]

    def cost(self, feature: str | None = None) -> Cost:
        return ai.add_costs(
            (call.cost, call.currency)
            for call in self.calls
            if feature is None or call.feature == feature
        )

    @property
    def tokens(self) -> int:
        return sum(call.prompt_tokens + call.completion_tokens for call in self.calls)


@dataclass
class Spread:
    count: int = 0
    priced: int = 0
    average: float | None = None
    minimum: float | None = None
    maximum: float | None = None


@dataclass
class DayCost:
    day: str
    first: float = 0.0
    reanalysis: float = 0.0
    failed: float = 0.0
    other: float = 0.0


@dataclass
class ConfigurationCost:
    configuration_id: str | None
    name: str
    is_default: bool
    analyses: int
    failed: int
    priced: int
    cost: float | None
    average: float | None
    tokens: int
    approvals: int
    approved_unchanged: int


@dataclass
class DocumentCost:
    document_id: str
    name: str
    open: bool
    attempts: int
    failed: int
    page_count: int | None
    cost: float


@dataclass
class OtherCurrency:
    currency: str | None
    amount: float


@dataclass
class CostReport:
    # None both when nothing was priced and for costs from before currencies were kept;
    # `priced` tells the two apart.
    currency: str | None
    priced: bool
    spent: float
    split: dict[str, float]
    days: list[DayCost]
    others: list[OtherCurrency]
    analyses: Spread
    filed_documents: int
    filed: Spread
    configurations: list[ConfigurationCost]
    expensive: list[DocumentCost]


@dataclass
class AnalysisRow:
    run_id: str
    occurred_at: datetime
    document_id: str | None
    document_name: str
    document_open: bool
    kind: str
    configuration_name: str | None
    reading_cost: float | None
    filing_cost: float | None
    cost: float | None
    currency: str | None
    tokens: int


@dataclass
class AnalysisPage:
    items: list[AnalysisRow]
    total: int


def _window(start: date, end: date) -> tuple[datetime, datetime]:
    if end < start:
        raise ValidationError("The period ends before it starts.")
    if (end - start).days > MAX_DAYS:
        raise ValidationError("Choose a period of three years or less.")
    return datetime.combine(start, time.min), datetime.combine(end + timedelta(days=1), time.min)


def _runs(calls: list[AiCall]) -> list[_Run]:
    runs: dict[str, _Run] = {}
    for call in calls:
        run = runs.get(call.run_id)
        if run is None:
            run = runs[call.run_id] = _Run(
                run_id=call.run_id,
                occurred_at=call.occurred_at,
                source=call.source,
                document_id=call.document_id,
                outcome=call.outcome,
                configuration_id=call.configuration_id,
                configuration_name=call.configuration_name,
            )
        run.calls.append(call)
    return sorted(runs.values(), key=lambda run: (run.occurred_at, run.run_id))


def _classify(runs: list[_Run]) -> list[Analysis]:
    """Set each analysis run's kind and return the finished analyses, oldest first.

    A document's first finished analysis is its first; any later one a re-analysis, unless
    it failed. A run that stopped to wait takes the kind of the analysis it went into.
    """
    by_document: dict[str | None, list[_Run]] = defaultdict(list)
    for run in runs:
        if run.source == ANALYSIS:
            by_document[run.document_id].append(run)
    analyses: list[Analysis] = []
    for document_runs in by_document.values():
        finished_before = False
        waiting: list[_Run] = []
        for run in document_runs:
            if not run.finished:
                waiting.append(run)
                continue
            if run.outcome == "failed":
                run.kind = FAILED
            else:
                run.kind = REANALYSIS if finished_before else FIRST
            for earlier in waiting:
                earlier.kind = run.kind
            analyses.append(Analysis(run, [call for r in [*waiting, run] for call in r.calls]))
            finished_before = True
            waiting = []
        for still in waiting:
            still.kind = REANALYSIS if finished_before else FIRST
    return sorted(analyses, key=lambda item: (item.run.occurred_at, item.run.run_id))


def _load(
    session: Session, user_id: str, start: date, end: date
) -> tuple[datetime, datetime, list[_Run], list[Analysis], list[HistoryEntry]]:
    """The period's runs, every analysis of the documents it touched (earlier ones decide
    first or re-analysis), and the period's filings."""
    since, until = _window(start, end)
    period_calls = list(
        session.exec(
            select(AiCall).where(
                AiCall.user_id == user_id,
                col(AiCall.occurred_at) >= since,
                col(AiCall.occurred_at) < until,
            )
        ).all()
    )
    filings = list(
        session.exec(
            select(HistoryEntry)
            .join(Document, col(Document.id) == col(HistoryEntry.document_id))
            .where(
                Document.user_id == user_id,
                HistoryEntry.action == HistoryAction.moved,
                col(HistoryEntry.processed_at) >= since,
                col(HistoryEntry.processed_at) < until,
            )
        ).all()
    )
    documents = {
        call.document_id
        for call in period_calls
        if call.source == ANALYSIS and call.document_id is not None
    } | {entry.document_id for entry in filings}
    known = {call.id for call in period_calls}
    earlier = (
        session.exec(
            select(AiCall).where(
                AiCall.user_id == user_id,
                AiCall.source == ANALYSIS,
                col(AiCall.document_id).in_(documents),
                col(AiCall.occurred_at) < until,
            )
        ).all()
        if documents
        else []
    )
    runs = _runs(period_calls + [call for call in earlier if call.id not in known])
    analyses = _classify(runs)
    return since, until, runs, analyses, filings


def _main_currency(calls: list[AiCall]) -> tuple[str | None, bool]:
    counts = Counter(call.currency for call in calls if call.cost is not None)
    if not counts:
        return None, False
    # Most calls first; ties go to a named currency over an unrecorded one, then by name.
    best = max(counts.items(), key=lambda item: (item[1], item[0] is not None, item[0] or ""))
    return best[0], True


def _spread(values: list[float], count: int) -> Spread:
    if not values:
        return Spread(count=count)
    return Spread(
        count=count,
        priced=len(values),
        average=sum(values) / len(values),
        minimum=min(values),
        maximum=max(values),
    )


def _names(session: Session, document_ids: set[str]) -> dict[str, tuple[str, bool, int | None]]:
    """A document's name as filed, else as scanned; whether it is still open for review;
    its page count."""
    if not document_ids:
        return {}
    names: dict[str, tuple[str, bool, int | None]] = {}
    for document in session.exec(select(Document).where(col(Document.id).in_(document_ids))).all():
        names[document.id] = (
            document.original_filename,
            document.status in QUEUED_STATUSES,
            document.page_count,
        )
    for entry in session.exec(
        select(HistoryEntry)
        .where(
            col(HistoryEntry.document_id).in_(document_ids),
            HistoryEntry.action == HistoryAction.moved,
        )
        .order_by(col(HistoryEntry.processed_at))
    ).all():
        _, still_open, pages = names.get(entry.document_id, ("", False, None))
        names[entry.document_id] = (entry.final_filename, still_open, pages)
    return names


def report(session: Session, user_id: str, start: date, end: date) -> CostReport:
    since, until, runs, analyses, filings = _load(session, user_id, start, end)
    in_period = [run for run in runs if since <= run.occurred_at < until]
    period_calls = [call for run in in_period for call in run.calls]
    currency, priced = _main_currency(period_calls)

    split = dict.fromkeys(KINDS, 0.0)
    days: dict[str, DayCost] = {}
    others: dict[str | None, float] = defaultdict(float)
    for run in in_period:
        kind = run.kind if run.source == ANALYSIS else OTHER
        for call in run.calls:
            if call.cost is None:
                continue
            if call.currency != currency:
                others[call.currency] += call.cost
                continue
            split[kind] += call.cost
            day = run.occurred_at.date().isoformat()
            bucket = days.setdefault(day, DayCost(day))
            setattr(bucket, kind, getattr(bucket, kind) + call.cost)

    def main(cost: Cost) -> float | None:
        return cost[0] if cost[0] is not None and cost[1] == currency else None

    finished = [item for item in analyses if since <= item.run.occurred_at < until]
    costs = [main(item.cost()) for item in finished]
    analysis_spread = _spread([cost for cost in costs if cost is not None], len(finished))

    # A filed document's cost is every analysis it took, whenever it ran.
    filed_ids = {entry.document_id for entry in filings}
    per_document: dict[str, list[AiCall]] = defaultdict(list)
    for item in analyses:
        if item.run.document_id in filed_ids:
            per_document[item.run.document_id].extend(item.calls)
    filed_costs = [
        main(ai.add_costs((call.cost, call.currency) for call in calls))
        for calls in per_document.values()
    ]
    filed_spread = _spread([cost for cost in filed_costs if cost is not None], len(filed_ids))

    return CostReport(
        currency=currency,
        priced=priced,
        spent=sum(split.values()),
        split=split,
        days=sorted(days.values(), key=lambda bucket: bucket.day),
        others=[
            OtherCurrency(currency=other, amount=amount)
            for other, amount in sorted(others.items(), key=lambda item: -item[1])
        ],
        analyses=analysis_spread,
        filed_documents=len(filed_ids),
        filed=filed_spread,
        configurations=_by_configuration(session, user_id, finished, filings, main),
        expensive=_expensive(session, finished, main),
    )


def _by_configuration(
    session: Session,
    user_id: str,
    finished: list[Analysis],
    filings: list[HistoryEntry],
    main: Main,
) -> list[ConfigurationCost]:
    configurations = {
        row.id: row
        for row in session.exec(
            select(AiConfiguration).where(AiConfiguration.user_id == user_id)
        ).all()
    }

    def key(configuration_id: str | None, name: str | None) -> str:
        return configuration_id or f"name:{name or ''}"

    groups: dict[str, list[Analysis]] = defaultdict(list)
    names: dict[str, tuple[str | None, str | None]] = {}
    for item in finished:
        group = key(item.run.configuration_id, item.run.configuration_name)
        groups[group].append(item)
        names[group] = (item.run.configuration_id, item.run.configuration_name)

    # Approvals count against the configuration that wrote the approved proposal: the
    # snapshot names it since configurations were recorded there, the proposal before that.
    proposals = (
        {
            proposal.document_id: proposal
            for proposal in session.exec(
                select(Proposal).where(
                    col(Proposal.document_id).in_({entry.document_id for entry in filings})
                )
            ).all()
        }
        if filings
        else {}
    )
    approvals: dict[str, list[bool]] = defaultdict(list)
    for entry in filings:
        snapshot = entry.suggestion_snapshot or {}
        if not snapshot:
            continue  # filed without a proposal: nothing was approved
        configuration_id = snapshot.get("configuration_id")
        configuration_name = snapshot.get("configuration_name")
        proposal = proposals.get(entry.document_id)
        if configuration_id is None and configuration_name is None and proposal is not None:
            configuration_id = proposal.configuration_id
            configuration_name = proposal.configuration_name
        group = key(
            configuration_id if isinstance(configuration_id, str) else None,
            configuration_name if isinstance(configuration_name, str) else None,
        )
        if group not in groups:
            continue  # its analyses ran in an earlier period
        approvals[group].append(not entry.was_overridden)

    result: list[ConfigurationCost] = []
    for group, items in groups.items():
        configuration_id, stored_name = names[group]
        current = configurations.get(configuration_id) if configuration_id else None
        costs = [cost for cost in (main(item.cost()) for item in items) if cost is not None]
        verdicts = approvals.get(group, [])
        result.append(
            ConfigurationCost(
                configuration_id=configuration_id,
                name=current.name if current else (stored_name or "Not recorded"),
                is_default=bool(current and current.is_default),
                analyses=len(items),
                failed=sum(item.run.kind == FAILED for item in items),
                priced=len(costs),
                cost=sum(costs) if costs else None,
                average=sum(costs) / len(costs) if costs else None,
                tokens=sum(item.tokens for item in items),
                approvals=len(verdicts),
                approved_unchanged=sum(verdicts),
            )
        )
    return sorted(result, key=lambda row: (-(row.cost or 0.0), -row.analyses, row.name))


def _expensive(session: Session, finished: list[Analysis], main: Main) -> list[DocumentCost]:
    per_document: dict[str, list[Analysis]] = defaultdict(list)
    for item in finished:
        if item.run.document_id is not None:
            per_document[item.run.document_id].append(item)
    totals: list[tuple[str, float, list[Analysis]]] = []
    for document_id, items in per_document.items():
        cost = main(ai.add_costs(item.cost() for item in items))
        if cost:
            totals.append((document_id, cost, items))
    totals.sort(key=lambda item: -item[1])
    top = totals[:EXPENSIVE]
    names = _names(session, {document_id for document_id, _, _ in top})
    return [
        DocumentCost(
            document_id=document_id,
            name=names.get(document_id, _REMOVED)[0],
            open=names.get(document_id, ("", False, None))[1],
            attempts=len(items),
            failed=sum(item.run.kind == FAILED for item in items),
            page_count=names.get(document_id, ("", False, None))[2],
            cost=cost,
        )
        for document_id, cost, items in top
    ]


def analyses_page(
    session: Session, user_id: str, start: date, end: date, offset: int, limit: int
) -> AnalysisPage:
    """The period's analyses, newest first, each with what it cost in its own currency."""
    since, until, _, analyses, _ = _load(session, user_id, start, end)
    finished = [item for item in reversed(analyses) if since <= item.run.occurred_at < until]
    page = finished[offset : offset + limit]
    names = _names(session, {item.run.document_id for item in page if item.run.document_id})
    items: list[AnalysisRow] = []
    for item in page:
        cost, currency = item.cost()
        reading, _ = item.cost("extraction")
        filing, _ = item.cost("filing")
        name, still_open, _ = names.get(item.run.document_id or "", _REMOVED)
        items.append(
            AnalysisRow(
                run_id=item.run.run_id,
                occurred_at=item.run.occurred_at,
                document_id=item.run.document_id,
                document_name=name,
                document_open=still_open,
                kind=item.run.kind,
                configuration_name=item.run.configuration_name,
                reading_cost=reading,
                filing_cost=filing,
                cost=cost,
                currency=currency,
                tokens=item.tokens,
            )
        )
    return AnalysisPage(items=items, total=len(finished))
