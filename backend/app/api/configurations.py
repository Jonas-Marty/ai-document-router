"""Configurations: named ways of reading and filing a document (SPEC 6.3a, 8.13)."""

from fastapi import APIRouter, status

from app.deps import AppSettingsDep, CurrentUserDep, SessionDep, WebDavDep
from app.models import AiConfiguration, AiEndpoint
from app.schemas import (
    BuiltinPromptsRead,
    ConfigurationPreviewRead,
    ConfigurationRead,
    ConfigurationStepBody,
    ConfigurationWrite,
)
from app.services import configurations
from app.services.audit import Actor
from app.services.configurations import ConfigurationInput, StepInput

router = APIRouter()


@router.get("/configurations")
def list_configurations(session: SessionDep, user: CurrentUserDep) -> list[ConfigurationRead]:
    return [_read(session, row) for row in configurations.list_configurations(session, user.id)]


@router.get("/configurations/prompts")
def builtin_prompts(user: CurrentUserDep) -> BuiltinPromptsRead:
    """What a configuration without its own system prompt is sent."""
    builtin = configurations.BUILTIN
    return BuiltinPromptsRead(
        reading=builtin.reading,
        filing_one_call=builtin.filing_one_call,
        filing_fields=builtin.filing_fields,
        filing_folder=builtin.filing_folder,
    )


@router.post("/configurations", status_code=status.HTTP_201_CREATED)
def create_configuration(
    payload: ConfigurationWrite, session: SessionDep, user: CurrentUserDep
) -> ConfigurationRead:
    created = configurations.create(session, user.id, _input(payload), actor=Actor.from_user(user))
    return _read(session, created)


@router.put("/configurations/{configuration_id}")
def update_configuration(
    configuration_id: str, payload: ConfigurationWrite, session: SessionDep, user: CurrentUserDep
) -> ConfigurationRead:
    updated = configurations.update(
        session, user.id, configuration_id, _input(payload), actor=Actor.from_user(user)
    )
    return _read(session, updated)


@router.delete("/configurations/{configuration_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_configuration(configuration_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    configurations.delete(session, user.id, configuration_id, actor=Actor.from_user(user))


@router.post("/configurations/{configuration_id}/duplicate", status_code=status.HTTP_201_CREATED)
def duplicate_configuration(
    configuration_id: str, session: SessionDep, user: CurrentUserDep
) -> ConfigurationRead:
    copy = configurations.duplicate(session, user.id, configuration_id, actor=Actor.from_user(user))
    return _read(session, copy)


@router.post("/configurations/{configuration_id}/default")
def make_default(
    configuration_id: str, session: SessionDep, user: CurrentUserDep
) -> ConfigurationRead:
    chosen = configurations.make_default(
        session, user.id, configuration_id, actor=Actor.from_user(user)
    )
    return _read(session, chosen)


@router.get("/configurations/{configuration_id}/preview")
def preview_configuration(
    configuration_id: str,
    session: SessionDep,
    user: CurrentUserDep,
    webdav: WebDavDep,
    app_settings: AppSettingsDep,
) -> list[ConfigurationPreviewRead]:
    """What each call is sent. Walks WebDAV for the folder tree, so fetched on demand."""
    configuration = configurations.get_configuration(session, user.id, configuration_id)
    return [
        ConfigurationPreviewRead(
            step=call.step,
            model_label=call.model_label,
            system_prompt=call.system_prompt,
            user_prompt=call.user_prompt,
            note=call.note,
        )
        for call in configurations.preview(session, webdav, app_settings, configuration)
    ]


def _input(payload: ConfigurationWrite) -> ConfigurationInput:
    return ConfigurationInput(
        name=payload.name,
        reading_method=payload.reading_method,
        reading=_step_input(payload.reading),
        filing_strategy=payload.filing_strategy,
        filing=_step_input(payload.filing),
        folder_system_prompt=payload.folder_system_prompt,
        max_examples=payload.max_examples,
        max_folders=payload.max_folders,
        max_text_chars=payload.max_text_chars,
    )


def _step_input(body: ConfigurationStepBody) -> StepInput:
    return StepInput(
        endpoint_id=body.endpoint_id,
        model_name=body.model_name,
        system_prompt=body.system_prompt,
        instructions=body.instructions,
        temperature=body.temperature,
        max_tokens=body.max_tokens,
        thinking=body.thinking,
    )


def _read(session: SessionDep, configuration: AiConfiguration) -> ConfigurationRead:
    data = configurations.to_input(configuration)
    return ConfigurationRead(
        id=configuration.id,
        is_default=configuration.is_default,
        name=data.name,
        reading_method=data.reading_method,
        reading=_step_body(session, data.reading),
        filing_strategy=data.filing_strategy,
        filing=_step_body(session, data.filing),
        folder_system_prompt=data.folder_system_prompt,
        max_examples=data.max_examples,
        max_folders=data.max_folders,
        max_text_chars=data.max_text_chars,
    )


def _step_body(session: SessionDep, step: StepInput) -> ConfigurationStepBody:
    endpoint = session.get(AiEndpoint, step.endpoint_id) if step.endpoint_id else None
    return ConfigurationStepBody(
        endpoint_id=step.endpoint_id,
        endpoint_name=endpoint.name if endpoint else None,
        model_name=step.model_name,
        system_prompt=step.system_prompt,
        instructions=step.instructions,
        temperature=step.temperature,
        max_tokens=step.max_tokens,
        thinking=step.thinking,
    )
