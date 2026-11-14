"""The Lab: test sets of filed documents, runs of configurations over them, scores (SPEC 6.4a).

A run is measured, not trusted: every result is scored field by field against an answer the
owner already approved, and the owner can overrule any verdict. Runs go through the poller
after the day's own documents, on the same thread, so the Lab never competes with daily
filing for a connection that can only do one thing at a time.

The Lab only reads the archive. Every path it touches was checked against the owner's allowed
folders when it was added, and is checked again before it is read.
"""

import logging
import re
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import date

from sqlmodel import Session, col, select

from app.models import (
    AiConfiguration,
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    LabReading,
    LabResult,
    LabResultStatus,
    LabRun,
    Reading,
    TestCase,
    TestCaseSource,
    TestSet,
)
from app.services import (
    ai,
    ai_usage,
    analysis,
    configurations,
    connections,
    extraction,
    folders,
    knowledge,
    progress,
)
from app.services.ai import Unreachable
from app.services.configurations import Plan
from app.services.connections import ResolvedStep
from app.services.errors import AppError, NotFoundError, ValidationError
from app.services.paths import assert_within_allowed_roots, normalize_path
from app.services.times import to_storage, utc_now
from app.services.webdav import WebDavService, parent_of

logger = logging.getLogger(__name__)

MAX_NAME_CHARS = 80
MAX_CASES = 200
MAX_FOLDER_FILES = 100
MAX_RUN_CONFIGURATIONS = 6

NAME_FIELDS = ("sender", "account", "title", "period", "number")
_DATE_PREFIX = re.compile(r"^(\d{4})\.(\d{2})\.(\d{2})\b")
_YEAR = re.compile(r"(?<!\d)(19|20)\d{2}(?!\d)")

Prober = Callable[[ResolvedStep], bool]


# --- test sets --------------------------------------------------------------------------


def list_test_sets(session: Session, user_id: str) -> list[tuple[TestSet, int]]:
    sets = session.exec(
        select(TestSet).where(TestSet.user_id == user_id).order_by(col(TestSet.name))
    ).all()
    counts: dict[str, int] = {}
    for case in session.exec(select(TestCase).where(TestCase.user_id == user_id)).all():
        counts[case.test_set_id] = counts.get(case.test_set_id, 0) + 1
    return [(test_set, counts.get(test_set.id, 0)) for test_set in sets]


def get_test_set(session: Session, user_id: str, test_set_id: str) -> TestSet:
    test_set = session.get(TestSet, test_set_id)
    if test_set is None or test_set.user_id != user_id:
        raise NotFoundError("That test set no longer exists.")
    return test_set


def cases_of(session: Session, test_set: TestSet) -> list[TestCase]:
    return list(
        session.exec(
            select(TestCase)
            .where(TestCase.test_set_id == test_set.id)
            .order_by(col(TestCase.created_at), col(TestCase.path))
        ).all()
    )


def _clean_name(session: Session, user_id: str, name: str, own_id: str | None = None) -> str:
    cleaned = " ".join(name.split())
    if not cleaned:
        raise ValidationError("Give the test set a name.")
    if len(cleaned) > MAX_NAME_CHARS:
        raise ValidationError(f"A test set's name can be at most {MAX_NAME_CHARS} characters.")
    for other in session.exec(select(TestSet).where(TestSet.user_id == user_id)).all():
        if other.id != own_id and other.name.casefold() == cleaned.casefold():
            raise ValidationError(f"There is already a test set called '{cleaned}'.")
    return cleaned


def create_test_set(session: Session, user_id: str, name: str) -> TestSet:
    test_set = TestSet(
        user_id=user_id,
        name=_clean_name(session, user_id, name),
        created_at=to_storage(utc_now()),
    )
    session.add(test_set)
    session.commit()
    session.refresh(test_set)
    return test_set


def rename_test_set(session: Session, user_id: str, test_set_id: str, name: str) -> TestSet:
    test_set = get_test_set(session, user_id, test_set_id)
    test_set.name = _clean_name(session, user_id, name, own_id=test_set.id)
    session.add(test_set)
    session.commit()
    session.refresh(test_set)
    return test_set


def delete_test_set(session: Session, user_id: str, test_set_id: str) -> None:
    """Runs stay: they copied what they need, and their scores are the point of having run."""
    test_set = get_test_set(session, user_id, test_set_id)
    for case in cases_of(session, test_set):
        _delete_case(session, case)
    session.delete(test_set)
    session.commit()


def delete_case(session: Session, user_id: str, case_id: str) -> None:
    case = session.get(TestCase, case_id)
    if case is None or case.user_id != user_id:
        raise NotFoundError("That document is no longer in the test set.")
    _delete_case(session, case)
    session.commit()


def _delete_case(session: Session, case: TestCase) -> None:
    for reading in session.exec(select(LabReading).where(LabReading.test_case_id == case.id)).all():
        session.delete(reading)
    session.flush()
    session.delete(case)


def date_of(stem: str) -> date | None:
    """The date a name starts with (`YYYY.MM.DD`, SPEC 7.1a), or None."""
    match = _DATE_PREFIX.match(stem)
    if match is None:
        return None
    try:
        return date(int(match[1]), int(match[2]), int(match[3]))
    except ValueError:
        return None


def _split(filename: str) -> str:
    extension = extraction.extension_of(filename)
    return filename[: -len(extension)] if extension else filename


@dataclass
class Added:
    added: int = 0
    skipped: int = 0


def add_documents(
    session: Session,
    app_settings: AppSettings,
    test_set_id: str,
    document_ids: Sequence[str],
) -> Added:
    """Done documents, each with the answer it was approved with. Anything else -- another
    user's, not filed, filed outside the allowed folders -- is skipped, not refused: the
    list was picked from a page that may be out of date."""
    user_id = app_settings.user_id
    test_set = get_test_set(session, user_id, test_set_id)
    taken = {case.path for case in cases_of(session, test_set)}
    result = Added()
    for document_id in dict.fromkeys(document_ids):
        case = _case_from_document(session, app_settings, test_set, document_id)
        if case is None or case.path in taken:
            result.skipped += 1
            continue
        if len(taken) >= MAX_CASES:
            raise ValidationError(f"A test set holds at most {MAX_CASES} documents.")
        taken.add(case.path)
        session.add(case)
        result.added += 1
    session.commit()
    return result


def _case_from_document(
    session: Session, app_settings: AppSettings, test_set: TestSet, document_id: str
) -> TestCase | None:
    document = session.get(Document, document_id)
    if (
        document is None
        or document.user_id != app_settings.user_id
        or document.status != DocumentStatus.moved
    ):
        return None
    entry = session.exec(
        select(HistoryEntry)
        .where(
            HistoryEntry.document_id == document.id,
            HistoryEntry.action == HistoryAction.moved,
        )
        .order_by(col(HistoryEntry.processed_at).desc())
    ).first()
    if entry is None:
        return None
    try:
        path = assert_within_allowed_roots(document.webdav_path, app_settings.allowed_root_folders)
    except AppError:
        return None
    stem = _split(entry.final_filename)
    fields = entry.name_fields
    return TestCase(
        user_id=app_settings.user_id,
        test_set_id=test_set.id,
        source=TestCaseSource.done,
        document_id=document.id,
        path=path,
        original_filename=document.original_filename,
        mime_type=document.mime_type,
        expected_name=stem,
        expected_date=date_of(stem),
        expected_fields={key: fields.get(key) for key in NAME_FIELDS} if fields else None,
        expected_folder=normalize_path(entry.final_folder_path),
        created_at=to_storage(utc_now()),
    )


def add_folder(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    test_set_id: str,
    folder_path: str,
) -> Added:
    """The PDFs and images directly in an archive folder, their current name and folder as
    the answer. Not recursive: one folder is one deliberate choice, a tree could be hundreds
    of files nobody looked at."""
    user_id = app_settings.user_id
    test_set = get_test_set(session, user_id, test_set_id)
    try:
        folder = assert_within_allowed_roots(folder_path, app_settings.allowed_root_folders)
    except ValueError as exc:
        raise ValidationError(str(exc)) from exc
    taken = {case.path for case in cases_of(session, test_set)}
    result = Added()
    entries = [entry for entry in service.list_dir(folder) if not entry.is_dir]
    for entry in sorted(entries, key=lambda item: item.name)[:MAX_FOLDER_FILES]:
        mime_type = extraction.guess_mime_type(entry.name, entry.content_type)
        if mime_type != extraction.PDF_MIME and not mime_type.startswith("image/"):
            result.skipped += 1
            continue
        path = normalize_path(entry.path)
        if path in taken:
            result.skipped += 1
            continue
        if len(taken) >= MAX_CASES:
            raise ValidationError(f"A test set holds at most {MAX_CASES} documents.")
        stem = _split(entry.name)
        taken.add(path)
        session.add(
            TestCase(
                user_id=user_id,
                test_set_id=test_set.id,
                source=TestCaseSource.archive,
                path=path,
                original_filename=entry.name,
                mime_type=mime_type,
                expected_name=stem,
                expected_date=date_of(stem),
                expected_fields=None,
                expected_folder=folder,
                created_at=to_storage(utc_now()),
            )
        )
        result.added += 1
    session.commit()
    return result


# --- runs -------------------------------------------------------------------------------


def start_run(
    session: Session, user_id: str, test_set_id: str, configuration_ids: Sequence[str]
) -> LabRun:
    test_set = get_test_set(session, user_id, test_set_id)
    wanted = list(dict.fromkeys(configuration_ids))
    if not wanted:
        raise ValidationError("Choose at least one configuration to run.")
    if len(wanted) > MAX_RUN_CONFIGURATIONS:
        raise ValidationError(f"A run compares at most {MAX_RUN_CONFIGURATIONS} configurations.")
    chosen = [configurations.get_configuration(session, user_id, item) for item in wanted]
    cases = cases_of(session, test_set)
    if not cases:
        raise ValidationError("This test set has no documents yet.")

    run = LabRun(
        user_id=user_id,
        test_set_id=test_set.id,
        test_set_name=test_set.name,
        configurations=[{"id": item.id, "name": item.name} for item in chosen],
        created_at=to_storage(utc_now()),
    )
    session.add(run)
    session.flush()
    for configuration in chosen:
        for position, case in enumerate(cases):
            session.add(_pending(run, case, position, configuration))
    session.commit()
    session.refresh(run)
    return run


def _pending(
    run: LabRun, case: TestCase, position: int, configuration: AiConfiguration
) -> LabResult:
    return LabResult(
        user_id=run.user_id,
        run_id=run.id,
        test_case_id=case.id,
        position=position,
        configuration_id=configuration.id,
        configuration_name=configuration.name,
        original_filename=case.original_filename,
        expected_name=case.expected_name,
        expected_date=case.expected_date,
        expected_fields=case.expected_fields,
        expected_folder=case.expected_folder,
    )


def get_run(session: Session, user_id: str, run_id: str) -> LabRun:
    run = session.get(LabRun, run_id)
    if run is None or run.user_id != user_id:
        raise NotFoundError("That run no longer exists.")
    return run


def list_runs(session: Session, user_id: str) -> list[LabRun]:
    return list(
        session.exec(
            select(LabRun).where(LabRun.user_id == user_id).order_by(col(LabRun.created_at).desc())
        ).all()
    )


def results_of(session: Session, run: LabRun) -> list[LabResult]:
    order = {item["id"]: index for index, item in enumerate(run.configurations)}
    rows = session.exec(select(LabResult).where(LabResult.run_id == run.id)).all()
    return sorted(rows, key=lambda row: (row.position, order.get(row.configuration_id, 0)))


def cancel_run(session: Session, user_id: str, run_id: str) -> LabRun:
    run = get_run(session, user_id, run_id)
    for result in results_of(session, run):
        if result.status == LabResultStatus.pending:
            result.status = LabResultStatus.cancelled
            result.waiting_reason = None
            session.add(result)
    run.cancelled = True
    run.finished_at = run.finished_at or to_storage(utc_now())
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def delete_run(session: Session, user_id: str, run_id: str) -> None:
    run = get_run(session, user_id, run_id)
    for result in results_of(session, run):
        session.delete(result)
    session.flush()
    session.delete(run)
    session.commit()


# --- scoring ----------------------------------------------------------------------------


def _same(predicted: object, expected: object) -> float:
    left = knowledge.normalise(str(predicted)) if predicted else ""
    right = knowledge.normalise(str(expected)) if expected else ""
    return 1.0 if left == right else 0.0


def folder_score(predicted: str | None, expected: str) -> float:
    """1 when equal; 0.5 for only a year apart, the same parent, or stopping one level
    short (SPEC 6.4a); else 0."""
    if not predicted:
        return 0.0
    left, right = normalize_path(predicted).casefold(), normalize_path(expected).casefold()
    if left == right:
        return 1.0
    if _YEAR.sub("{year}", left) == _YEAR.sub("{year}", right):
        return 0.5
    if parent_of(left) == parent_of(right) and parent_of(right) != "/":
        return 0.5
    if left == parent_of(right) and left != "/":
        return 0.5
    return 0.0


def scored_fields(result: LabResult, template: Sequence[str]) -> list[str]:
    if result.expected_fields is None:
        return ["name", "folder"]
    return ["date", *[key for key in template if key in NAME_FIELDS], "folder"]


def score(result: LabResult, template: Sequence[str]) -> dict[str, float]:
    """The automatic verdict per field. A failed result is wrong everywhere."""
    names = scored_fields(result, template)
    if result.status != LabResultStatus.done:
        return dict.fromkeys(names, 0.0)
    verdicts: dict[str, float] = {}
    predicted = result.predicted_fields or {}
    expected = result.expected_fields or {}
    for name in names:
        if name == "folder":
            verdicts[name] = folder_score(result.predicted_folder, result.expected_folder)
        elif name == "name":
            verdicts[name] = _same(result.predicted_name, result.expected_name)
        elif name == "date":
            verdicts[name] = 1.0 if result.predicted_date == result.expected_date else 0.0
        else:
            verdicts[name] = _same(predicted.get(name), expected.get(name))
    return verdicts


def effective(result: LabResult) -> dict[str, float]:
    return {**result.verdicts, **result.overrides}


def set_verdict(
    session: Session, user_id: str, result_id: str, name: str, value: float | None
) -> LabResult:
    result = session.get(LabResult, result_id)
    if result is None or result.user_id != user_id:
        raise NotFoundError("That result no longer exists.")
    if result.status in (LabResultStatus.pending, LabResultStatus.cancelled):
        raise ValidationError("This result has not been scored.")
    if name not in result.verdicts:
        raise ValidationError(f"'{name}' is not scored for this document.")
    overrides = dict(result.overrides)
    if value is None:
        overrides.pop(name, None)
    else:
        allowed = (0.0, 0.5, 1.0) if name == "folder" else (0.0, 1.0)
        if value not in allowed:
            raise ValidationError(
                "A verdict is right (1), wrong (0), or for the folder partly (0.5)."
            )
        overrides[name] = float(value)
    # A new dict: SQLAlchemy does not see a JSON column change in place.
    result.overrides = overrides
    session.add(result)
    session.commit()
    session.refresh(result)
    return result


# --- summaries --------------------------------------------------------------------------


@dataclass
class ConfigurationSummary:
    configuration_id: str
    configuration_name: str
    documents: int = 0
    finished: int = 0
    failed: int = 0
    accuracy: dict[str, float] = field(default_factory=dict)
    fully_correct: float = 0.0
    average_ms: float | None = None
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost: float | None = None


@dataclass
class RunStatus:
    run: LabRun
    status: str
    total: int
    finished: int
    waiting_reason: str | None
    stage: str | None
    remaining_seconds: float | None
    summaries: list[ConfigurationSummary]


def summarise(run: LabRun, results: Sequence[LabResult]) -> RunStatus:
    summaries = [
        ConfigurationSummary(configuration_id=item["id"], configuration_name=item["name"])
        for item in run.configurations
    ]
    by_id = {summary.configuration_id: summary for summary in summaries}
    totals: dict[str, dict[str, list[float]]] = {key: {} for key in by_id}
    fully: dict[str, int] = dict.fromkeys(by_id, 0)
    timings: dict[str, list[int]] = {key: [] for key in by_id}
    for result in results:
        summary = by_id.get(result.configuration_id)
        if summary is None:
            continue
        summary.documents += 1
        if result.status not in (LabResultStatus.done, LabResultStatus.failed):
            continue
        summary.finished += 1
        summary.failed += result.status == LabResultStatus.failed
        summary.prompt_tokens += result.prompt_tokens
        summary.completion_tokens += result.completion_tokens
        if result.cost is not None:
            summary.cost = (summary.cost or 0.0) + result.cost
        if result.duration_ms is not None:
            timings[result.configuration_id].append(result.duration_ms)
        verdicts = effective(result)
        for name, value in verdicts.items():
            totals[result.configuration_id].setdefault(name, []).append(value)
        fully[result.configuration_id] += bool(verdicts) and all(
            value == 1.0 for value in verdicts.values()
        )
    for summary in summaries:
        values = totals[summary.configuration_id]
        summary.accuracy = {name: sum(items) / len(items) for name, items in values.items()}
        if summary.finished:
            summary.fully_correct = fully[summary.configuration_id] / summary.finished
        timed = timings[summary.configuration_id]
        summary.average_ms = sum(timed) / len(timed) if timed else None
    durations = [value for timed in timings.values() for value in timed]

    pending = [result for result in results if result.status == LabResultStatus.pending]
    finished = sum(
        1 for result in results if result.status in (LabResultStatus.done, LabResultStatus.failed)
    )
    if run.cancelled:
        status = "cancelled"
    elif pending:
        status = "running"
    else:
        status = "done"
    waiting = next((result.waiting_reason for result in pending if result.waiting_reason), None)
    stage = next((stage for result in pending if (stage := progress.stage_of(result.id))), None)
    remaining = (
        sum(durations) / len(durations) * len(pending) / 1000 if durations and pending else None
    )
    return RunStatus(
        run=run,
        status=status,
        total=len(results),
        finished=finished,
        waiting_reason=waiting,
        stage=stage,
        remaining_seconds=remaining,
        summaries=summaries,
    )


# --- running ----------------------------------------------------------------------------


def _next_run(session: Session, user_id: str) -> LabRun | None:
    return session.exec(
        select(LabRun)
        .where(
            LabRun.user_id == user_id,
            col(LabRun.cancelled).is_(False),
            col(LabRun.finished_at).is_(None),
        )
        .order_by(col(LabRun.created_at))
    ).first()


def work(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    *,
    probe: Prober | None = None,
    deadline: float | None = None,
) -> int:
    """Pending results of the user's oldest unfinished run, until `deadline` (a
    time.monotonic() value) passes; checked before each result, so one slow reading can
    overrun it. Returns how many results finished. Results waiting for a connection are
    passed over and stay pending."""
    run = _next_run(session, app_settings.user_id)
    if run is None:
        return 0
    pending = [
        result for result in results_of(session, run) if result.status == LabResultStatus.pending
    ]
    # One configuration after another: on a GPU that holds one model, each loads once.
    order = {item["id"]: index for index, item in enumerate(run.configurations)}
    pending.sort(key=lambda result: (order.get(result.configuration_id, 0), result.position))

    reachability = analysis.Reachability(probe or connections.is_reachable)
    plans: dict[str, Plan | None] = {}
    context: tuple[list[str], list[str]] | None = None
    template = knowledge.template_of(app_settings)
    done = 0
    for result in pending:
        if deadline is not None and time.monotonic() >= deadline:
            break
        if result.configuration_id not in plans:
            configuration = session.get(AiConfiguration, result.configuration_id)
            plans[result.configuration_id] = (
                configurations.plan(session, configuration)
                if configuration is not None and configuration.user_id == run.user_id
                else None
            )
        plan = plans[result.configuration_id]
        case = session.get(TestCase, result.test_case_id)
        if plan is None or case is None or case.user_id != run.user_id:
            _finish(
                session,
                result,
                template,
                error="The configuration was deleted."
                if plan is None
                else "The document was removed from the test set.",
            )
            done += 1
            continue
        waiting = _unreachable(plan, reachability)
        if waiting is not None:
            _wait(session, result, waiting)
            continue
        if context is None:
            context = folders.prompt_context(service, app_settings)
        done += 1
        _run_one(
            session, service, app_settings, result, case, plan, context, reachability, template
        )

    if not any(result.status == LabResultStatus.pending for result in results_of(session, run)):
        run.finished_at = to_storage(utc_now())
        session.add(run)
        session.commit()
    return done


def _unreachable(plan: Plan, reachability: analysis.Reachability) -> str | None:
    for step, verb in ((plan.reading_step, "read"), (plan.filing_step, "file")):
        if step is not None and not reachability.check(step):
            return f"Waiting for {step.endpoint_name} to {verb}"
    return None


def _run_one(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    result: LabResult,
    case: TestCase,
    plan: Plan,
    context: tuple[list[str], list[str]],
    reachability: analysis.Reachability,
    template: Sequence[str],
) -> None:
    meter = ai.UsageMeter()
    with progress.tracking(result.id):
        try:
            progress.report(f"Reading {case.original_filename}…")
            label, text = _reading(session, service, app_settings, case, plan, meter)
            progress.report(f"Filing {case.original_filename} with {plan.configuration_name}…")
            archive = knowledge.for_prompt(
                session,
                app_settings,
                text[: plan.max_text_chars],
                plan.max_examples,
                exclude_document_ids=_same_file(session, case),
            )
            filed = analysis.file_text(plan, text, context, app_settings, meter, archive)
        except Unreachable as exc:
            for step in (plan.reading_step, plan.filing_step):
                if step is not None:
                    reachability.lost(step)
            _account(session, result, case, meter, keep_waiting=True)
            _wait(session, result, f"Waiting: {exc.message}")
            return
        except analysis.ReadingFailed as exc:
            _account(session, result, case, meter)
            _finish(session, result, template, error=exc.reason, meter=meter)
            return
        except ai.ProposalRejected as exc:
            _account(session, result, case, meter)
            _finish(session, result, template, error=exc.reason, meter=meter)
            return
        except AppError as exc:
            _account(session, result, case, meter)
            _finish(session, result, template, error=exc.message, meter=meter)
            return
    proposal = filed.proposal
    result.reading_label = label
    result.predicted_name = proposal.suggested_name
    result.predicted_date = proposal.document_date
    result.predicted_fields = dict(proposal.fields.to_json()) if proposal.fields else None
    result.predicted_folder = normalize_path(proposal.target_folder_path)
    _account(session, result, case, meter)
    _finish(session, result, template, meter=meter)


def _same_file(session: Session, case: TestCase) -> set[str]:
    """The document under test and any filed document at the same path: neither may be
    one of its own examples."""
    ids = {case.document_id} if case.document_id else set()
    ids |= set(
        session.exec(
            select(Document.id).where(
                Document.user_id == case.user_id, Document.webdav_path == case.path
            )
        ).all()
    )
    return ids


def _reading(
    session: Session,
    service: WebDavService,
    app_settings: AppSettings,
    case: TestCase,
    plan: Plan,
    meter: ai.UsageMeter,
) -> tuple[str, str]:
    setup = plan.reading_setup(searchable=False)
    if case.document_id is not None:
        own = session.exec(
            select(Reading).where(
                Reading.document_id == case.document_id, Reading.setup_key == setup.key
            )
        ).first()
        if own is not None:
            return own.label, own.text
    stored = session.exec(
        select(LabReading).where(
            LabReading.test_case_id == case.id, LabReading.setup_key == setup.key
        )
    ).first()
    if stored is not None:
        return stored.label, stored.text

    try:
        path = assert_within_allowed_roots(case.path, app_settings.allowed_root_folders)
        data = b"".join(service.read_stream(path))
    except AppError as exc:
        raise analysis.ReadingFailed(f"Couldn't read the file: {exc.message}") from exc
    text = analysis.read_data(
        data, case.mime_type, setup.method, plan, meter, case.original_filename
    )
    session.add(
        LabReading(
            user_id=case.user_id,
            test_case_id=case.id,
            setup_key=setup.key,
            label=setup.label,
            text=text,
            created_at=to_storage(utc_now()),
        )
    )
    session.commit()
    return setup.label, text


def _account(
    session: Session,
    result: LabResult,
    case: TestCase,
    meter: ai.UsageMeter,
    *,
    keep_waiting: bool = False,
) -> None:
    """Tokens and cost add up across attempts: a call that was paid for stays paid for."""
    for call in meter.calls:
        if call.usage is not None:
            result.prompt_tokens += call.usage.prompt_tokens
            result.completion_tokens += call.usage.completion_tokens
        if call.cost is not None:
            result.cost = (result.cost or 0.0) + call.cost
    if meter.calls:
        ai_usage.store(session, [meter], user_id=result.user_id, document_id=case.document_id)
    if not keep_waiting:
        result.duration_ms = (result.duration_ms or 0) + meter.elapsed_ms()


def _wait(session: Session, result: LabResult, reason: str) -> None:
    result.waiting_reason = reason
    session.add(result)
    session.commit()


def _finish(
    session: Session,
    result: LabResult,
    template: Sequence[str],
    *,
    error: str | None = None,
    meter: ai.UsageMeter | None = None,
) -> None:
    result.status = LabResultStatus.failed if error else LabResultStatus.done
    result.error = error
    result.waiting_reason = None
    if result.duration_ms is None and meter is not None:
        result.duration_ms = meter.elapsed_ms()
    result.verdicts = score(result, template)
    result.finished_at = to_storage(utc_now())
    session.add(result)
    session.commit()
    logger.info(
        "Lab result for %s with %s: %s",
        result.original_filename,
        result.configuration_name,
        error or "done",
    )
