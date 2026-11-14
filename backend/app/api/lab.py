"""The Lab: test sets, runs and their scores (SPEC 6.4a, 8.13a)."""

from fastapi import APIRouter, Response, status

from app.deps import AppSettingsDep, CurrentUserDep, SessionDep, WebDavDep
from app.jobs import poller
from app.models import LabResult, LabRun, TestCase, TestSet
from app.schemas import (
    AddCasesRequest,
    AddedCasesResponse,
    LabConfigurationSummaryRead,
    LabFieldsRead,
    LabResultRead,
    LabRunDetailRead,
    LabRunRead,
    StartRunRequest,
    TestCaseRead,
    TestSetDetailRead,
    TestSetRead,
    TestSetWrite,
    VerdictRequest,
)
from app.services import lab
from app.services.errors import ValidationError

router = APIRouter(prefix="/lab")


@router.get("/test-sets")
def list_test_sets(session: SessionDep, user: CurrentUserDep) -> list[TestSetRead]:
    return [_set(row, count) for row, count in lab.list_test_sets(session, user.id)]


@router.post("/test-sets", status_code=status.HTTP_201_CREATED)
def create_test_set(
    payload: TestSetWrite, session: SessionDep, user: CurrentUserDep
) -> TestSetRead:
    return _set(lab.create_test_set(session, user.id, payload.name), 0)


@router.get("/test-sets/{test_set_id}")
def read_test_set(test_set_id: str, session: SessionDep, user: CurrentUserDep) -> TestSetDetailRead:
    test_set = lab.get_test_set(session, user.id, test_set_id)
    return _detail(test_set, lab.cases_of(session, test_set))


@router.put("/test-sets/{test_set_id}")
def rename_test_set(
    test_set_id: str, payload: TestSetWrite, session: SessionDep, user: CurrentUserDep
) -> TestSetRead:
    test_set = lab.rename_test_set(session, user.id, test_set_id, payload.name)
    return _set(test_set, len(lab.cases_of(session, test_set)))


@router.delete("/test-sets/{test_set_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_test_set(test_set_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    lab.delete_test_set(session, user.id, test_set_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/test-sets/{test_set_id}/cases")
def add_cases(
    test_set_id: str,
    payload: AddCasesRequest,
    session: SessionDep,
    app_settings: AppSettingsDep,
    webdav: WebDavDep,
) -> AddedCasesResponse:
    if (payload.document_ids is None) == (payload.folder_path is None):
        raise ValidationError("Add either Done documents or one folder.")
    if payload.document_ids is not None:
        added = lab.add_documents(session, app_settings, test_set_id, payload.document_ids)
    else:
        assert payload.folder_path is not None
        added = lab.add_folder(session, webdav, app_settings, test_set_id, payload.folder_path)
    return AddedCasesResponse(added=added.added, skipped=added.skipped)


@router.delete("/test-cases/{case_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_case(case_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    lab.delete_case(session, user.id, case_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/runs")
def list_runs(session: SessionDep, user: CurrentUserDep) -> list[LabRunRead]:
    return [_run(run, lab.results_of(session, run)) for run in lab.list_runs(session, user.id)]


@router.post("/runs", status_code=status.HTTP_201_CREATED)
def start_run(payload: StartRunRequest, session: SessionDep, user: CurrentUserDep) -> LabRunRead:
    run = lab.start_run(session, user.id, payload.test_set_id, payload.configuration_ids)
    # Someone is watching the progress bar; the next tick should not be a minute away.
    poller.kick()
    return _run(run, lab.results_of(session, run))


@router.get("/runs/{run_id}")
def read_run(run_id: str, session: SessionDep, user: CurrentUserDep) -> LabRunDetailRead:
    run = lab.get_run(session, user.id, run_id)
    results = lab.results_of(session, run)
    return LabRunDetailRead(
        **_run(run, results).model_dump(), results=[_result(row) for row in results]
    )


@router.post("/runs/{run_id}/cancel")
def cancel_run(run_id: str, session: SessionDep, user: CurrentUserDep) -> LabRunRead:
    run = lab.cancel_run(session, user.id, run_id)
    return _run(run, lab.results_of(session, run))


@router.delete("/runs/{run_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_run(run_id: str, session: SessionDep, user: CurrentUserDep) -> Response:
    lab.delete_run(session, user.id, run_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.put("/results/{result_id}/verdict")
def set_verdict(
    result_id: str, payload: VerdictRequest, session: SessionDep, user: CurrentUserDep
) -> LabResultRead:
    return _result(lab.set_verdict(session, user.id, result_id, payload.field, payload.score))


def _set(test_set: TestSet, count: int) -> TestSetRead:
    return TestSetRead(
        id=test_set.id, name=test_set.name, case_count=count, created_at=test_set.created_at
    )


def _detail(test_set: TestSet, cases: list[TestCase]) -> TestSetDetailRead:
    return TestSetDetailRead(
        **_set(test_set, len(cases)).model_dump(),
        cases=[
            TestCaseRead(
                id=case.id,
                source=case.source,
                path=case.path,
                original_filename=case.original_filename,
                expected_name=case.expected_name,
                expected_date=case.expected_date,
                expected_fields=_fields(case.expected_fields),
                expected_folder=case.expected_folder,
            )
            for case in cases
        ],
    )


def _fields(values: dict[str, object] | None) -> LabFieldsRead | None:
    return LabFieldsRead.model_validate(values) if values is not None else None


def _run(run: LabRun, results: list[LabResult]) -> LabRunRead:
    summary = lab.summarise(run, results)
    return LabRunRead(
        id=run.id,
        test_set_id=run.test_set_id,
        test_set_name=run.test_set_name,
        status=summary.status,
        total=summary.total,
        finished=summary.finished,
        waiting_reason=summary.waiting_reason,
        stage=summary.stage,
        remaining_seconds=summary.remaining_seconds,
        created_at=run.created_at,
        finished_at=run.finished_at,
        summaries=[
            LabConfigurationSummaryRead(
                configuration_id=item.configuration_id,
                configuration_name=item.configuration_name,
                documents=item.documents,
                finished=item.finished,
                failed=item.failed,
                accuracy=item.accuracy,
                fully_correct=item.fully_correct,
                average_ms=item.average_ms,
                prompt_tokens=item.prompt_tokens,
                completion_tokens=item.completion_tokens,
                cost=item.cost,
            )
            for item in summary.summaries
        ],
    )


def _result(result: LabResult) -> LabResultRead:
    return LabResultRead(
        id=result.id,
        test_case_id=result.test_case_id,
        position=result.position,
        configuration_id=result.configuration_id,
        configuration_name=result.configuration_name,
        original_filename=result.original_filename,
        expected_name=result.expected_name,
        expected_date=result.expected_date,
        expected_folder=result.expected_folder,
        expected_fields=_fields(result.expected_fields),
        status=result.status,
        error=result.error,
        predicted_name=result.predicted_name,
        predicted_date=result.predicted_date,
        predicted_fields=_fields(result.predicted_fields),
        predicted_folder=result.predicted_folder,
        reading_label=result.reading_label,
        duration_ms=result.duration_ms,
        verdicts=result.verdicts,
        overrides=result.overrides,
    )
