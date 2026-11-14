"""Archive knowledge: what is true of the owner's archive whatever the model (SPEC 6.3c).

The name template, filing rules, sender short names, accounts and approved examples, shared
by every configuration. Senders and accounts are only ever learned from an approval the
owner ticked "remember" on; this module never adds one on its own.
"""

import re
import unicodedata
from collections.abc import Collection, Sequence
from dataclasses import dataclass

from sqlmodel import Session, col, select

from app.models import (
    AppSettings,
    AuditAction,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    KnownAccount,
    KnownSender,
    Reading,
)
from app.services import audit, naming
from app.services.audit import Actor
from app.services.errors import NotFoundError, ValidationError
from app.services.times import to_storage, utc_now

MAX_FILING_RULES = 4000
MAX_SHORT_NAME = 80
MAX_ALIASES = 10
MAX_ALIAS = 200
# Without a sender found in the text, every short name is offered; past this many the list
# costs more context than it is worth.
MAX_LISTED_SENDERS = 100
EXAMPLE_TEXT_CHARS = 400
# Fewer digits than this is too common in a document to identify an account by.
MIN_KEY_DIGITS = 5

_UMLAUTS = str.maketrans({"ü": "ue", "ö": "oe", "ä": "ae", "ß": "ss"})
_DIGIT_RUNS = re.compile(r"\d+")


def normalise(value: str) -> str:
    """Letters and digits only, case folded, umlauts spelled out (SPEC 6.3c).

    So "Zürcher Kantonalbank", "ZUERCHER KANTONALBANK" and "zuercher-kantonalbank" are one.
    """
    folded = unicodedata.normalize("NFC", value).casefold().translate(_UMLAUTS)
    return "".join(char for char in folded if char.isalnum())


def account_key(value: str) -> str:
    """What identifies an account in a text: its longest digit run, or all of it.

    An account in a name often carries a label ("frankly 3a CH33 0070 …") that a document
    prints elsewhere or not at all; the number is what the document is sure to contain.
    """
    normalised = normalise(value)
    longest = max(_DIGIT_RUNS.findall(normalised), key=len, default="")
    return longest if len(longest) >= MIN_KEY_DIGITS else normalised


# --- template and rules -----------------------------------------------------------------


def validate_template(template: Sequence[str]) -> list[str]:
    keys = list(template)
    unknown = [key for key in keys if key not in naming.TEMPLATE_FIELDS]
    if unknown:
        raise ValidationError(f"The name template has an unknown field: {unknown[0]}.")
    if len(set(keys)) != len(keys):
        raise ValidationError("The name template lists a field more than once.")
    missing = [key for key in naming.ALWAYS_IN_TEMPLATE if key not in keys]
    if missing:
        raise ValidationError(f"The name template must include {missing[0]}.")
    return keys


def update_knowledge(
    session: Session,
    app_settings: AppSettings,
    name_template: Sequence[str],
    filing_rules: str | None,
    *,
    actor: Actor,
) -> AppSettings:
    template = validate_template(name_template)
    rules = (filing_rules or "").strip() or None
    if rules is not None and len(rules) > MAX_FILING_RULES:
        raise ValidationError(f"Filing rules must be {MAX_FILING_RULES:,} characters or fewer.")
    changed = [
        label
        for label, before, after in (
            ("name template", app_settings.name_template, template),
            ("filing rules", app_settings.filing_rules, rules),
        )
        if before != after
    ]
    app_settings.name_template = template
    app_settings.filing_rules = rules
    session.add(app_settings)
    audit.record(
        session,
        AuditAction.knowledge_updated,
        actor=actor,
        summary=f"Changed {' and '.join(changed)}" if changed else "Saved archive knowledge",
        details={"name_template": template, "filing_rules_changed": "filing rules" in changed},
    )
    session.commit()
    session.refresh(app_settings)
    return app_settings


def template_of(app_settings: AppSettings) -> list[str]:
    """The stored template, or the default if it was somehow left unusable."""
    try:
        return validate_template(app_settings.name_template or [])
    except ValidationError:
        return list(naming.DEFAULT_TEMPLATE)


# --- senders and accounts ---------------------------------------------------------------


@dataclass(frozen=True)
class SenderWithAccounts:
    sender: KnownSender
    accounts: list[KnownAccount]


def list_senders(session: Session, user_id: str) -> list[SenderWithAccounts]:
    senders = session.exec(select(KnownSender).where(KnownSender.user_id == user_id)).all()
    accounts = session.exec(select(KnownAccount).where(KnownAccount.user_id == user_id)).all()
    by_sender: dict[str, list[KnownAccount]] = {}
    for account in sorted(accounts, key=lambda a: a.value.casefold()):
        by_sender.setdefault(account.sender_id, []).append(account)
    return [
        SenderWithAccounts(sender, by_sender.get(sender.id, []))
        for sender in sorted(senders, key=lambda s: s.short_name.casefold())
    ]


def _get_sender(session: Session, user_id: str, sender_id: str) -> KnownSender:
    sender = session.get(KnownSender, sender_id)
    if sender is None or sender.user_id != user_id:
        raise NotFoundError("That sender isn't known.")
    return sender


def _find_sender(session: Session, user_id: str, short_name: str) -> KnownSender | None:
    wanted = short_name.strip().casefold()
    senders = session.exec(select(KnownSender).where(KnownSender.user_id == user_id)).all()
    return next((s for s in senders if s.short_name.casefold() == wanted), None)


def _clean_short_name(value: str) -> str:
    name = value.strip()
    if not name:
        raise ValidationError("A sender needs a short name.")
    if len(name) > MAX_SHORT_NAME:
        raise ValidationError(f"A short name must be {MAX_SHORT_NAME} characters or fewer.")
    found = naming.FORBIDDEN_CHARS & set(name)
    if found:
        raise ValidationError(f"A short name can't contain {' '.join(sorted(found))}")
    return name


def _clean_aliases(values: Sequence[str]) -> list[str]:
    aliases: list[str] = []
    for value in values:
        alias = value.strip()
        if not alias or any(normalise(alias) == normalise(a) for a in aliases):
            continue
        if len(alias) > MAX_ALIAS:
            raise ValidationError(
                f"How a sender is written must be {MAX_ALIAS} characters or fewer."
            )
        aliases.append(alias)
    if len(aliases) > MAX_ALIASES:
        raise ValidationError(f"A sender can be written at most {MAX_ALIASES} ways.")
    return aliases


def update_sender(
    session: Session,
    user_id: str,
    sender_id: str,
    short_name: str,
    aliases: Sequence[str],
    *,
    actor: Actor,
) -> SenderWithAccounts:
    sender = _get_sender(session, user_id, sender_id)
    name = _clean_short_name(short_name)
    same = _find_sender(session, user_id, name)
    if same is not None and same.id != sender.id:
        raise ValidationError(f"'{same.short_name}' is already a known sender.")
    before = sender.short_name
    sender.short_name = name
    sender.aliases = _clean_aliases(aliases)
    session.add(sender)
    audit.record(
        session,
        AuditAction.knowledge_updated,
        actor=actor,
        summary=(
            f"Renamed sender {before} to {name}" if before != name else f"Edited sender {name}"
        ),
        details={"sender": name, "aliases": sender.aliases},
    )
    session.commit()
    session.refresh(sender)
    accounts = session.exec(select(KnownAccount).where(KnownAccount.sender_id == sender.id)).all()
    return SenderWithAccounts(sender, sorted(accounts, key=lambda a: a.value.casefold()))


def delete_sender(session: Session, user_id: str, sender_id: str, *, actor: Actor) -> None:
    sender = _get_sender(session, user_id, sender_id)
    for account in session.exec(select(KnownAccount).where(KnownAccount.sender_id == sender.id)):
        session.delete(account)
    session.delete(sender)
    audit.record(
        session,
        AuditAction.knowledge_updated,
        actor=actor,
        summary=f"Forgot sender {sender.short_name} and its accounts",
        details={"sender": sender.short_name},
    )
    session.commit()


def delete_account(session: Session, user_id: str, account_id: str, *, actor: Actor) -> None:
    account = session.get(KnownAccount, account_id)
    if account is None or account.user_id != user_id:
        raise NotFoundError("That account isn't known.")
    session.delete(account)
    audit.record(
        session,
        AuditAction.knowledge_updated,
        actor=actor,
        summary=f"Forgot account {account.value}",
        details={"account": account.value},
    )
    session.commit()


def remember(
    session: Session,
    user_id: str,
    fields: naming.NameFields,
    *,
    sender: bool,
    account: bool,
    actor: Actor,
) -> list[str]:
    """Learn the approved sender and account, as the owner asked (SPEC 6.4 step 5).

    Adds to the session without committing: the caller's approval commits it, so nothing is
    learned from an approval that did not happen. Returns what was learned, for the log.
    """
    learned: list[str] = []
    remember_account = account and fields.account is not None
    if not (sender or remember_account):
        return learned
    known = _find_sender(session, user_id, fields.sender)
    if known is None:
        known = KnownSender(
            user_id=user_id,
            short_name=_clean_short_name(fields.sender),
            aliases=[],
            created_at=to_storage(utc_now()),
        )
        session.add(known)
        session.flush()
        learned.append(f"sender {known.short_name}")
    if remember_account:
        assert fields.account is not None
        key = normalise(fields.account)
        accounts = session.exec(select(KnownAccount).where(KnownAccount.sender_id == known.id))
        if not any(normalise(a.value) == key for a in accounts):
            session.add(
                KnownAccount(
                    user_id=user_id,
                    sender_id=known.id,
                    value=fields.account,
                    created_at=to_storage(utc_now()),
                )
            )
            learned.append(f"account {fields.account} for {known.short_name}")
    if learned:
        audit.record(
            session,
            AuditAction.knowledge_learned,
            actor=actor,
            summary=f"Remembered {' and '.join(learned)}",
            details={"learned": learned},
        )
    return learned


# --- what the filing call is given ------------------------------------------------------


@dataclass(frozen=True)
class PromptKnowledge:
    """The archive-knowledge part of a filing prompt, and whether it holds examples."""

    text: str
    has_examples: bool


def for_prompt(
    session: Session,
    app_settings: AppSettings,
    document_text: str,
    max_examples: int,
    *,
    exclude_document_ids: Collection[str] = (),
) -> PromptKnowledge:
    """What the filing call is given about the archive for this text (SPEC 6.3c).

    `exclude_document_ids` keeps documents out of the examples: the Lab tests filed
    documents, and one shown its own approved answer would score itself."""
    haystack = normalise(document_text)
    user_id = app_settings.user_id
    senders = list_senders(session, user_id)
    sections: list[str] = []

    found = [
        entry
        for entry in senders
        if any(
            (needle := normalise(name)) and needle in haystack
            for name in [entry.sender.short_name, *entry.sender.aliases]
        )
    ]
    if found:
        sections += ["Known senders — use the short name exactly:"]
        sections += [_describe_sender(entry.sender) for entry in found]
    elif senders:
        sections += ["Senders seen before — use one of these short names if it is the sender:"]
        sections += [f"  {entry.sender.short_name}" for entry in senders[:MAX_LISTED_SENDERS]]

    accounts = [
        (entry.sender.short_name, account.value)
        for entry in senders
        for account in entry.accounts
        if account_key(account.value) in haystack
    ]
    if accounts:
        sections += ["", "Known accounts in this document — copy the value exactly:"]
        sections += [f"  {sender}: {value}" for sender, value in accounts]

    examples = _examples(
        session, user_id, haystack, found, accounts, max_examples, exclude_document_ids
    )
    if examples:
        sections += ["", "Documents filed before, with the fields and folder the owner approved:"]
        sections += examples

    if app_settings.filing_rules:
        sections += ["", "Filing rules for this archive:", app_settings.filing_rules]

    return PromptKnowledge("\n".join(sections).strip("\n"), has_examples=bool(examples))


def preview_text(app_settings: AppSettings) -> str:
    """For the Lab's preview, which has no document: what goes here, and the rules as they are."""
    sections = [
        "[Known senders and accounts found in the document, and documents filed before, go here]"
    ]
    if app_settings.filing_rules:
        sections += ["", "Filing rules for this archive:", app_settings.filing_rules]
    return "\n".join(sections)


def _describe_sender(sender: KnownSender) -> str:
    if not sender.aliases:
        return f"  {sender.short_name}"
    return f"  {sender.short_name} (written as: {'; '.join(sender.aliases)})"


def _examples(
    session: Session,
    user_id: str,
    haystack: str,
    found: list[SenderWithAccounts],
    accounts: list[tuple[str, str]],
    limit: int,
    exclude_document_ids: Collection[str] = (),
) -> list[str]:
    if limit <= 0:
        return []
    excluded = set(exclude_document_ids)
    rows = session.exec(
        select(HistoryEntry, Document, Reading)
        .join(Document, col(Document.id) == HistoryEntry.document_id)
        .join(Reading, col(Reading.id) == Document.reading_id)
        .where(
            Document.user_id == user_id,
            Document.status == DocumentStatus.moved,
            HistoryEntry.action == HistoryAction.moved,
            col(HistoryEntry.revertible).is_(True),
            col(HistoryEntry.name_fields).is_not(None),
        )
        .order_by(col(HistoryEntry.processed_at).desc())
        .limit(200)
    ).all()
    related_senders = {normalise(entry.sender.short_name) for entry in found}
    related_accounts = {normalise(value) for _, value in accounts}

    def related(entry: HistoryEntry) -> bool:
        fields = entry.name_fields or {}
        sender = fields.get("sender")
        account = fields.get("account")
        return (isinstance(sender, str) and normalise(sender) in related_senders) or (
            isinstance(account, str) and normalise(account) in related_accounts
        )

    # Newest first within each group; sorted() keeps that order, related ones go first.
    rows = [row for row in rows if row[1].id not in excluded]
    chosen = sorted(rows, key=lambda row: not related(row[0]))[:limit]
    return [_describe_example(entry, reading) for entry, _, reading in chosen]


def _describe_example(entry: HistoryEntry, reading: Reading) -> str:
    excerpt = " ".join(reading.text.split())[:EXAMPLE_TEXT_CHARS]
    fields = entry.name_fields or {}
    described = [
        f"{naming.FIELD_LABELS[key].lower()} {value}"
        for key, value in fields.items()
        if key in naming.FIELD_LABELS and isinstance(value, str) and value
    ]
    return "\n".join(
        [
            f"- Text: {excerpt}",
            f"  Fields: {' · '.join(described)}",
            f"  Folder: {entry.final_folder_path}",
        ]
    )
