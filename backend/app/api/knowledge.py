"""Archive knowledge: the name template, filing rules, senders and accounts (SPEC 6.3c)."""

from fastapi import APIRouter, status

from app.deps import AppSettingsDep, CurrentUserDep, SessionDep
from app.models import AppSettings
from app.schemas import (
    ArchiveKnowledgeRead,
    ArchiveKnowledgeWrite,
    KnownAccountRead,
    KnownSenderRead,
    KnownSenderWrite,
)
from app.services import knowledge
from app.services.audit import Actor

router = APIRouter()


@router.get("/knowledge")
def read_knowledge(app_settings: AppSettingsDep) -> ArchiveKnowledgeRead:
    return _read(app_settings)


@router.put("/knowledge")
def update_knowledge(
    payload: ArchiveKnowledgeWrite,
    session: SessionDep,
    user: CurrentUserDep,
    app_settings: AppSettingsDep,
) -> ArchiveKnowledgeRead:
    updated = knowledge.update_knowledge(
        session,
        app_settings,
        payload.name_template,
        payload.filing_rules,
        actor=Actor.from_user(user),
    )
    return _read(updated)


@router.get("/knowledge/senders")
def list_senders(session: SessionDep, user: CurrentUserDep) -> list[KnownSenderRead]:
    return [_sender(entry) for entry in knowledge.list_senders(session, user.id)]


@router.put("/knowledge/senders/{sender_id}")
def update_sender(
    sender_id: str, payload: KnownSenderWrite, session: SessionDep, user: CurrentUserDep
) -> KnownSenderRead:
    entry = knowledge.update_sender(
        session,
        user.id,
        sender_id,
        payload.short_name,
        payload.aliases,
        actor=Actor.from_user(user),
    )
    return _sender(entry)


@router.delete("/knowledge/senders/{sender_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_sender(sender_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    knowledge.delete_sender(session, user.id, sender_id, actor=Actor.from_user(user))


@router.delete("/knowledge/accounts/{account_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_account(account_id: str, session: SessionDep, user: CurrentUserDep) -> None:
    knowledge.delete_account(session, user.id, account_id, actor=Actor.from_user(user))


def _read(app_settings: AppSettings) -> ArchiveKnowledgeRead:
    return ArchiveKnowledgeRead(
        name_template=knowledge.template_of(app_settings),
        filing_rules=app_settings.filing_rules,
    )


def _sender(entry: knowledge.SenderWithAccounts) -> KnownSenderRead:
    return KnownSenderRead(
        id=entry.sender.id,
        short_name=entry.sender.short_name,
        aliases=list(entry.sender.aliases),
        accounts=[KnownAccountRead(id=a.id, value=a.value) for a in entry.accounts],
    )
