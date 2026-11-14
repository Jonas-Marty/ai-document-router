"""The Inbox: every document in four groups, searchable, with what needs a check (SPEC 6.4b)."""

import enum
import io
import logging
import re
from collections import defaultdict
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, UnidentifiedImageError
from sqlalchemy import ColumnElement, and_, func, or_
from sqlmodel import Session, select

from app.config import settings as config
from app.models import (
    AiCall,
    AppSettings,
    Document,
    DocumentStatus,
    HistoryAction,
    HistoryEntry,
    KnownAccount,
    KnownSender,
    OcrStatus,
    Proposal,
    ProposalStatus,
    Reading,
    ReadingMethod,
)
from app.services import configurations, documents, knowledge, progress, router, searchable
from app.services.audit import Actor
from app.services.connections import ResolvedStep
from app.services.errors import AppError, NotFoundError
from app.services.extraction import PDF_MIME, PDFIUM_LOCK, extension_of
from app.services.paths import normalize_path
from app.services.webdav import WebDavService

logger = logging.getLogger(__name__)

MAX_PAGE = 100
THUMBNAIL_WIDTH = 320
# Two edits covers one misread digit and one transposition; more and unrelated accounts of
# the same bank start to look alike.
NEAR_MATCH_EDITS = 2
_YEAR = re.compile(r"(19|20)\d\d")


class Group(enum.StrEnum):
    analyse = "analyse"
    ready = "ready"
    done = "done"
    trashed = "trashed"


class Sort(enum.StrEnum):
    newest = "newest"
    document_date = "document_date"
    sender = "sender"
    needs_check = "needs_check"


_OPEN = (DocumentStatus.pending, DocumentStatus.skipped)


def _group_filter(group: Group) -> ColumnElement[bool]:
    is_open = Document.status.in_(_OPEN)  # type: ignore[attr-defined]
    not_ready: ColumnElement[bool] = Document.proposal_status != ProposalStatus.ready  # type: ignore[assignment]
    ready: ColumnElement[bool] = Document.proposal_status == ProposalStatus.ready  # type: ignore[assignment]
    failed: ColumnElement[bool] = Document.status == DocumentStatus.failed  # type: ignore[assignment]
    if group is Group.analyse:
        return or_(
            and_(is_open, not_ready),
            # Never set today, but a document in it would otherwise be in no group at all.
            failed,
        )
    if group is Group.ready:
        return and_(is_open, ready)
    if group is Group.done:
        return Document.status == DocumentStatus.moved  # type: ignore[return-value]
    return Document.status == DocumentStatus.trashed  # type: ignore[return-value]


# --- cards -------------------------------------------------------------------------------


@dataclass
class Card:
    id: str
    original_filename: str
    name: str | None
    folder: str | None
    sender: str | None
    document_date: date | None
    scanned_at: datetime
    configuration_name: str | None
    proposal_status: ProposalStatus
    later: bool
    flags: list[str] = field(default_factory=list)
    analysis_stage: str | None = None
    waiting_reason: str | None = None
    error: str | None = None
    history_entry_id: str | None = None
    revertible: bool = False
    processed_at: datetime | None = None
    # For the folder check, not sent: the proposal's account and whether it had fields.
    account: str | None = None
    has_fields: bool = False
    title: str | None = None


@dataclass
class Page:
    items: list[Card]
    total: int
    counts: dict[str, int]
    senders: list[str]
    folders: list[str]
    # To analyse only: how many of the group are being worked on right now.
    analysing: int = 0


def counts(session: Session, user_id: str) -> dict[str, int]:
    return {
        group.value: int(
            session.exec(
                select(func.count())
                .select_from(Document)
                .where(Document.user_id == user_id)
                .where(_group_filter(group))
            ).one()
        )
        for group in Group
    }


def _search_filter(user_id: str, query: str) -> ColumnElement[bool]:
    escaped = query.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    pattern = f"%{escaped}%"
    return or_(
        Document.original_filename.ilike(pattern, escape="\\"),  # type: ignore[attr-defined]
        Document.id.in_(  # type: ignore[attr-defined]
            select(Proposal.document_id).where(
                Proposal.suggested_name.ilike(pattern, escape="\\")  # type: ignore[attr-defined]
            )
        ),
        Document.id.in_(  # type: ignore[attr-defined]
            select(HistoryEntry.document_id).where(
                HistoryEntry.final_filename.ilike(pattern, escape="\\")  # type: ignore[attr-defined]
            )
        ),
        Document.reading_id.in_(  # type: ignore[union-attr]
            select(Reading.id).where(
                Reading.user_id == user_id,
                Reading.text.ilike(pattern, escape="\\"),  # type: ignore[attr-defined]
            )
        ),
    )


def page(
    session: Session,
    webdav: WebDavService | None,
    user_id: str,
    *,
    group: Group,
    query: str = "",
    sender: str | None = None,
    folder: str | None = None,
    sort: Sort = Sort.newest,
    offset: int = 0,
    limit: int = 48,
) -> Page:
    """One group's cards, searched, filtered, sorted and paged.

    Search runs in SQL because it reaches into reading text; the rest runs on the group's
    cards in Python, because sender, folder and date come from the proposal or the history
    entry depending on the group, and one user's documents are thousands at most.
    """
    statement = select(Document).where(Document.user_id == user_id).where(_group_filter(group))
    if query.strip():
        statement = statement.where(_search_filter(user_id, query.strip()))
    cards = _cards(session, user_id, group, list(session.exec(statement).all()))

    senders = sorted({card.sender for card in cards if card.sender}, key=str.casefold)
    folders = sorted({card.folder for card in cards if card.folder}, key=str.casefold)
    if sender:
        cards = [card for card in cards if card.sender == sender]
    if folder:
        cards = [card for card in cards if card.folder == folder]

    if group is Group.ready:
        checks = Checks.load(session, user_id)
        for card in cards:
            card.flags = checks.flags(card)
    cards = _sorted(cards, sort)
    analysing = 0
    if group is Group.analyse:
        # What is happening now is what someone opening this tab looks for; a stable sort
        # keeps the chosen order within both halves.
        cards.sort(key=lambda card: card.analysis_stage is None)
        analysing = sum(card.analysis_stage is not None for card in cards)

    shown = cards[offset : offset + min(limit, MAX_PAGE)]
    if group is Group.ready and webdav is not None:
        _flag_taken_names(webdav, shown)
    return Page(
        items=shown,
        total=len(cards),
        counts=counts(session, user_id),
        senders=senders,
        folders=folders,
        analysing=analysing,
    )


def _cards(session: Session, user_id: str, group: Group, found: Sequence[Document]) -> list[Card]:
    ids = [document.id for document in found]
    proposals = {
        proposal.document_id: proposal
        for proposal in session.exec(
            select(Proposal).where(Proposal.document_id.in_(ids))  # type: ignore[attr-defined]
        ).all()
    }
    entries: dict[str, HistoryEntry] = {}
    if group in (Group.done, Group.trashed):
        action = HistoryAction.moved if group is Group.done else HistoryAction.trashed
        for filed_entry in session.exec(
            select(HistoryEntry)
            .where(HistoryEntry.document_id.in_(ids))  # type: ignore[attr-defined]
            .where(HistoryEntry.action == action)
            .order_by(HistoryEntry.processed_at)  # type: ignore[arg-type]
        ).all():
            entries[filed_entry.document_id] = filed_entry  # the latest wins
    names = {
        configuration.id: configuration.name
        for configuration in configurations.list_configurations(session, user_id)
    }
    default = configurations.default_configuration(session, user_id)

    cards = []
    for document in found:
        proposal = proposals.get(document.id)
        entry = entries.get(document.id)
        ready = proposal is not None and document.proposal_status is ProposalStatus.ready
        filed_fields = (entry.name_fields or {}) if entry else {}
        name: str | None = None
        folder: str | None = None
        if entry is not None:
            name, folder = entry.final_filename, entry.final_folder_path
        elif ready and proposal is not None:
            name = f"{proposal.suggested_name}{extension_of(document.original_filename)}"
            folder = proposal.target_folder_path
        if ready and proposal is not None:
            produced_by = proposal.configuration_name
        else:
            picked = names.get(document.configuration_id or "")
            produced_by = picked or (default.name if default else None)
        has_fields = proposal is not None and proposal.title is not None
        cards.append(
            Card(
                id=document.id,
                original_filename=document.original_filename,
                name=name,
                folder=folder,
                sender=_text(filed_fields.get("sender"))
                or (proposal.sender if proposal and (ready or entry) else None),
                document_date=proposal.document_date if proposal else None,
                scanned_at=document.scanned_at,
                configuration_name=produced_by,
                proposal_status=document.proposal_status,
                later=document.status is DocumentStatus.skipped,
                analysis_stage=progress.stage_of(document.id) if group is Group.analyse else None,
                waiting_reason=document.waiting_reason if group is Group.analyse else None,
                error=(document.proposal_error or document.error_message)
                if group is Group.analyse
                else None,
                history_entry_id=entry.id if entry else None,
                revertible=entry.revertible if entry else False,
                processed_at=entry.processed_at if entry else None,
                account=proposal.account if proposal else None,
                has_fields=has_fields,
                title=proposal.title if proposal else None,
            )
        )
    return cards


def _text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None


def _sorted(cards: list[Card], sort: Sort) -> list[Card]:
    newest = sorted(cards, key=lambda card: (card.scanned_at, card.id), reverse=True)
    if sort is Sort.document_date:
        # Stable sorts on the newest-first list: ties keep the newest upload first.
        dated = [card for card in newest if card.document_date]
        dated.sort(key=lambda card: card.document_date or date.min, reverse=True)
        return dated + [card for card in newest if not card.document_date]
    if sort is Sort.sender:
        named = [card for card in newest if card.sender]
        named.sort(key=lambda card: (card.sender or "").casefold())
        return named + [card for card in newest if not card.sender]
    if sort is Sort.needs_check:
        return sorted(newest, key=lambda card: len(card.flags), reverse=True)
    return newest


# --- needs a check -----------------------------------------------------------------------


def folder_pattern(path: str) -> str:
    """The folder with every year segment generalised, so /Bank/2025 and /Bank/2026 are one."""
    segments = normalize_path(path).split("/")
    return "/".join("{year}" if _YEAR.fullmatch(part) else part.casefold() for part in segments)


def edit_distance(a: str, b: str, cap: int = NEAR_MATCH_EDITS) -> int:
    """Levenshtein distance, giving up past `cap` -- only "close or not" matters here."""
    if abs(len(a) - len(b)) > cap:
        return cap + 1
    previous = list(range(len(b) + 1))
    for i, char_a in enumerate(a, 1):
        current = [i]
        for j, char_b in enumerate(b, 1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (char_a != char_b))
            )
        if min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


@dataclass
class Checks:
    """What the flags compare against, loaded once per request."""

    # Normalised short name or alias -> sender id.
    sender_ids: dict[str, str]
    account_keys: dict[str, set[str]]
    # Normalised sender -> folder patterns; (sender, account key) -> folder patterns.
    sender_folders: dict[str, set[str]]
    account_folders: dict[tuple[str, str], set[str]]

    @classmethod
    def load(cls, session: Session, user_id: str) -> "Checks":
        sender_ids: dict[str, str] = {}
        for known in session.exec(select(KnownSender).where(KnownSender.user_id == user_id)):
            for written in (known.short_name, *known.aliases):
                sender_ids.setdefault(knowledge.normalise(written), known.id)
        account_keys: dict[str, set[str]] = defaultdict(set)
        for account in session.exec(select(KnownAccount).where(KnownAccount.user_id == user_id)):
            account_keys[account.sender_id].add(knowledge.account_key(account.value))

        sender_folders: dict[str, set[str]] = defaultdict(set)
        account_folders: dict[tuple[str, str], set[str]] = defaultdict(set)
        filed = session.exec(
            select(HistoryEntry)
            .join(Document, Document.id == HistoryEntry.document_id)  # type: ignore[arg-type]
            .where(Document.user_id == user_id)
            .where(HistoryEntry.action == HistoryAction.moved)
            .where(HistoryEntry.revertible == True)  # noqa: E712 - SQL comparison
        ).all()
        for entry in filed:
            fields = entry.name_fields or {}
            sender = _text(fields.get("sender"))
            if not sender:
                continue
            pattern = folder_pattern(entry.final_folder_path)
            sender_key = knowledge.normalise(sender)
            sender_folders[sender_key].add(pattern)
            filed_account = _text(fields.get("account"))
            if filed_account:
                account_folders[(sender_key, knowledge.account_key(filed_account))].add(pattern)
        return cls(sender_ids, dict(account_keys), dict(sender_folders), dict(account_folders))

    def flags(self, card: Card) -> list[str]:
        if not card.has_fields:
            # A free name from before fields existed; no name at all is no proposal to check.
            return ["missing_date"] if card.name and not card.document_date else []
        flags: list[str] = []
        if not card.sender:
            flags.append("missing_sender")
        if not card.title:
            flags.append("missing_title")
        if not card.document_date:
            flags.append("missing_date")
        if card.sender:
            flags.extend(self._sender_flags(card.sender, card.account))
            if card.folder and self._unusual_folder(card.sender, card.account, card.folder):
                flags.append("unusual_folder")
        return flags

    def _sender_flags(self, sender: str, account: str | None) -> list[str]:
        sender_id = self.sender_ids.get(knowledge.normalise(sender))
        if sender_id is None:
            return ["new_sender"]
        if not account:
            return []
        key = knowledge.account_key(account)
        known = self.account_keys.get(sender_id, set())
        if key in known:
            return []
        if any(edit_distance(key, other) <= NEAR_MATCH_EDITS for other in known):
            return ["near_match_account"]
        return ["new_account"]

    def _unusual_folder(self, sender: str, account: str | None, folder: str) -> bool:
        sender_key = knowledge.normalise(sender)
        usual = self.sender_folders.get(sender_key)
        if not usual:
            return False
        if account:
            usual = self.account_folders.get((sender_key, knowledge.account_key(account)), usual)
        return folder_pattern(folder) not in usual


def _flag_taken_names(webdav: WebDavService, cards: Iterable[Card]) -> None:
    listings: dict[str, set[str] | None] = {}
    for card in cards:
        if not card.folder or not card.name:
            continue
        if card.folder not in listings:
            try:
                listings[card.folder] = {
                    entry.name for entry in webdav.list_dir(card.folder) if not entry.is_dir
                }
            except AppError:
                # A folder that does not exist yet holds nothing; an unreachable server is
                # a reason to say nothing, not to fail the whole page.
                listings[card.folder] = None
        names = listings[card.folder]
        if names is not None and card.name in names:
            card.flags.append("name_taken")


# --- thumbnails --------------------------------------------------------------------------


def thumbnail(
    session: Session, webdav: WebDavService | None, user_id: str, document_id: str
) -> bytes:
    """The first page as a small JPEG, rendered once per file content."""
    document = documents.get_document(session, user_id, document_id)
    is_image = document.mime_type.startswith("image/")
    if document.mime_type != PDF_MIME and not is_image:
        raise NotFoundError("There is no preview for this kind of file.")
    path = Path(config.thumbnail_cache_dir) / f"{document.content_hash}.jpg"
    if path.exists():
        return path.read_bytes()

    data = None
    if document.ocr_status is OcrStatus.ready:
        data = searchable.load(document.content_hash)
    if data is None:
        if webdav is None:
            raise NotFoundError("The Nextcloud isn't connected, so there is no preview.")
        data = b"".join(webdav.read_stream(document.webdav_path))
    image = _first_page(data, is_image)
    if image is None:
        raise NotFoundError("This file couldn't be rendered.")
    image.thumbnail((THUMBNAIL_WIDTH, THUMBNAIL_WIDTH * 2))
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=80)
    rendered = buffer.getvalue()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(rendered)
    except OSError as exc:
        logger.warning("Couldn't cache the thumbnail of %s: %s", document.id, exc)
    return rendered


def _first_page(data: bytes, is_image: bool) -> Image.Image | None:
    if is_image:
        try:
            with Image.open(io.BytesIO(data)) as opened:
                opened.load()
                copied: Image.Image = opened.copy()
                return copied
        except (UnidentifiedImageError, OSError):
            return None
    with PDFIUM_LOCK:
        try:
            pdf = pdfium.PdfDocument(data)
        except Exception:  # noqa: BLE001 - pdfium raises assorted types on a bad file
            return None
        try:
            if len(pdf) == 0:
                return None
            page_ = pdf[0]
            # Rendered at the width wanted, not at a dpi: a thumbnail of an A3 scan should not
            # cost an A3's worth of pixels.
            scale = THUMBNAIL_WIDTH * 2 / max(page_.get_width(), 1)
            # to_pil() may share PDFium's bitmap memory; the copy outlives the bitmap.
            rendered: Image.Image = page_.render(scale=scale).to_pil().copy()
            return rendered
        finally:
            pdf.close()


# --- bulk actions ------------------------------------------------------------------------


def _open_documents(session: Session, user_id: str, document_ids: Sequence[str]) -> list[Document]:
    """The caller's open documents among the ids, in the order asked. Others are skipped."""
    found = {
        document.id: document
        for document in session.exec(
            select(Document)
            .where(Document.user_id == user_id)
            .where(Document.id.in_(document_ids))  # type: ignore[attr-defined]
            .where(Document.status.in_(_OPEN))  # type: ignore[attr-defined]
        ).all()
    }
    wanted = dict.fromkeys(document_ids)
    return [found[document_id] for document_id in wanted if document_id in found]


def analyse(
    session: Session,
    user_id: str,
    document_ids: Sequence[str],
    *,
    actor: Actor,
    configuration_id: str | None = None,
) -> int:
    """Queue a fresh analysis for each open document; returns how many were queued."""
    if configuration_id is not None:
        # Checked once up front, so an unknown id is a 404 and not a silent no-op.
        configurations.get_configuration(session, user_id, configuration_id)
    chosen = _open_documents(session, user_id, document_ids)
    for document in chosen:
        documents.request_regenerate(
            session, user_id, document.id, actor=actor, configuration_id=configuration_id
        )
    return len(chosen)


@dataclass(frozen=True)
class TrashFailure:
    id: str
    message: str


def trash(
    session: Session,
    webdav: WebDavService,
    app_settings: AppSettings,
    document_ids: Sequence[str],
    *,
    actor: Actor,
) -> tuple[int, list[TrashFailure]]:
    """Trash one at a time: each is its own move, and one failure says nothing about the rest."""
    trashed = 0
    failures: list[TrashFailure] = []
    for document in _open_documents(session, app_settings.user_id, document_ids):
        try:
            router.trash(session, webdav, app_settings, document.id, actor=actor)
            trashed += 1
        except AppError as exc:
            session.rollback()
            failures.append(TrashFailure(document.id, exc.message))
    return trashed, failures


@dataclass(frozen=True)
class Estimate:
    documents: int
    readings_needed: int
    prompt_tokens: int | None
    completion_tokens: int | None
    cost: float | None
    connections: list[str]


def estimate(
    session: Session, user_id: str, document_ids: Sequence[str], configuration_id: str
) -> Estimate:
    """What analysing these documents with a configuration would roughly cost (SPEC 6.4b)."""
    configuration = configurations.get_configuration(session, user_id, configuration_id)
    plan = configurations.plan(session, configuration)
    chosen = _open_documents(session, user_id, document_ids)

    readings_needed = 0
    if plan.reading_method is ReadingMethod.vision:
        for document in chosen:
            key = plan.reading_setup(searchable=document.ocr_status is OcrStatus.ready).key
            stored = session.exec(
                select(Reading.id).where(
                    Reading.document_id == document.id, Reading.setup_key == key
                )
            ).first()
            if stored is None:
                readings_needed += 1

    stages: list[tuple[str, ResolvedStep | None, int]] = [("filing", plan.filing_step, len(chosen))]
    if plan.reading_method is ReadingMethod.vision:
        stages.append(("extraction", plan.reading_step, readings_needed))

    prompt = completion = 0
    cost: float | None = 0.0
    known = True
    for feature, step, count in stages:
        if count == 0:
            continue
        average = _average_tokens(session, user_id, feature, step.model_name if step else None)
        if average is None:
            known = False
            continue
        prompt += round(average[0] * count)
        completion += round(average[1] * count)
        # One side may be unpriced on purpose (a provider billing only output); neither is
        # simply not known.
        if step is None or (step.price_input is None and step.price_output is None):
            cost = None
        elif cost is not None:
            per_document = average[0] * (step.price_input or 0) + average[1] * (
                step.price_output or 0
            )
            cost += per_document * count / 1e6

    connections = sorted(
        {step.endpoint_name for step in (plan.reading_step, plan.filing_step) if step is not None}
    )
    return Estimate(
        documents=len(chosen),
        readings_needed=readings_needed,
        prompt_tokens=prompt if known else None,
        completion_tokens=completion if known else None,
        cost=round(cost, 4) if known and cost is not None else None,
        connections=connections,
    )


def _average_tokens(
    session: Session, user_id: str, feature: str, model: str | None
) -> tuple[float, float] | None:
    """Mean tokens per analysis for a feature, with this model when it has been used."""
    for narrowed in (True, False):
        if narrowed and model is None:
            continue
        statement = select(
            func.sum(AiCall.prompt_tokens),
            func.sum(AiCall.completion_tokens),
            func.count(func.distinct(AiCall.run_id)),
        ).where(AiCall.user_id == user_id, AiCall.feature == feature)
        if narrowed:
            statement = statement.where(AiCall.model == model)
        prompt, completion, runs = session.exec(statement).one()
        if runs:
            return (prompt or 0) / runs, (completion or 0) / runs
    return None
