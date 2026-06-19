"""Business logic for the reading log.

Routes call into this module so the rules around status transitions,
rereads, validation, and the one-time CWN import live in one place that
can be tested without spinning up the request layer.

Each :class:`ReadingLog` row represents *one read attempt*. The UI form
on ``/book/<id>/edit`` always inserts a new row (each save is a fresh
attempt) — the per-row edit icon on the reading-activity table is what
amends an existing attempt. ``current_status_for`` returns the
most-recently-created row's status (older edits never reshuffle "what
am I reading now").

The is_reread / reread_count columns are kept for backward compat with
the JSON API, but they no longer drive status queries — the first row
gets ``is_reread=False`` and subsequent rows get ``is_reread=True``
purely as a record of insertion order.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..extensions import db
from .models import NOTE_TYPES, READING_STATUSES, Note, Quote, ReadingLog, User


def _utcnow_naive() -> datetime:
    """Naive UTC ``datetime`` — matches what SQLite hands back on read."""
    return datetime.now(UTC).replace(tzinfo=None)


class ReadingLogValidationError(ValueError):
    """Raised when an inbound reading-log payload fails validation.

    Routes catch this and translate it to HTTP 400.
    """


@dataclass(frozen=True)
class LogPayload:
    """Sanitised reading-log payload after validation."""

    status: str | None
    started_at: datetime | None
    finished_at: datetime | None
    rating: int | None
    review: str | None
    is_reread: bool


# ── Validation ──────────────────────────────────────────────────────────────


def _parse_datetime(raw: object, field: str) -> datetime | None:
    """Accept ISO-8601 strings or :class:`datetime`; reject everything else."""
    if raw is None or raw == "":
        return None
    if isinstance(raw, datetime):
        return raw
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw)
        except ValueError as exc:
            raise ReadingLogValidationError(f"{field} is not a valid ISO date") from exc
    raise ReadingLogValidationError(f"{field} must be a datetime or ISO string")


def _parse_rating(raw: object) -> int | None:
    """Rating is a 1–10 integer or ``None``."""
    if raw is None or raw == "":
        return None
    try:
        rating = int(raw)
    except (TypeError, ValueError) as exc:
        raise ReadingLogValidationError("rating must be an integer 1-10") from exc
    if rating < 1 or rating > 10:
        raise ReadingLogValidationError("rating must be between 1 and 10")
    return rating


def _parse_status(raw: object) -> str | None:
    """Reject any status outside :data:`READING_STATUSES`."""
    if raw is None or raw == "":
        return None
    if not isinstance(raw, str) or raw not in READING_STATUSES:
        allowed = ", ".join(sorted(READING_STATUSES))
        raise ReadingLogValidationError(f"status must be one of: {allowed}")
    return raw


def validate_payload(data: dict) -> LogPayload:
    """Validate a route-supplied dict and return a normalised payload."""
    status = _parse_status(data.get("status"))
    started_at = _parse_datetime(data.get("started_at"), "started_at")
    finished_at = _parse_datetime(data.get("finished_at"), "finished_at")
    rating = _parse_rating(data.get("rating"))
    review_raw = data.get("review")
    review = review_raw.strip() if isinstance(review_raw, str) and review_raw.strip() else None
    is_reread = bool(data.get("is_reread"))

    if started_at and finished_at and finished_at < started_at:
        raise ReadingLogValidationError("finished_at must be >= started_at")

    return LogPayload(
        status=status,
        started_at=started_at,
        finished_at=finished_at,
        rating=rating,
        review=review,
        is_reread=is_reread,
    )


# ── Mutations ───────────────────────────────────────────────────────────────


def _apply_auto_transitions(log: ReadingLog, now: datetime | None = None) -> None:
    """Fill ``started_at`` / ``finished_at`` based on status transitions.

    * ``status = reading`` and no ``started_at`` → set to ``now``.
    * ``status = read``    and no ``finished_at`` → set to ``now``.
    """
    now = now or _utcnow_naive()
    if log.status == "reading" and log.started_at is None:
        log.started_at = now
    if log.status == "read" and log.finished_at is None:
        log.finished_at = now


def upsert_reading_log(
    user: User,
    calibre_book_id: int,
    data: dict,
    *,
    now: datetime | None = None,
) -> ReadingLog:
    """Create or update the user's reading-log row for a book.

    Rereads (``is_reread=True``) always insert a *new* row so the
    original first-read row is preserved. Non-rereads update the
    current row (or create one if none exists).
    """
    payload = validate_payload(data)

    if payload.is_reread:
        log = ReadingLog(
            user_id=user.id,
            calibre_book_id=calibre_book_id,
            status=payload.status or "reading",
            started_at=payload.started_at,
            finished_at=payload.finished_at,
            rating=payload.rating,
            review=payload.review,
            is_reread=True,
            reread_count=_next_reread_count(user.id, calibre_book_id),
        )
        db.session.add(log)
    else:
        log = (
            db.session.query(ReadingLog)
            .filter_by(user_id=user.id, calibre_book_id=calibre_book_id, is_reread=False)
            .first()
        )
        if log is None:
            log = ReadingLog(
                user_id=user.id,
                calibre_book_id=calibre_book_id,
                status=payload.status or "want_to_read",
            )
            db.session.add(log)
        # Apply only fields the caller explicitly provided.
        if payload.status is not None:
            log.status = payload.status
        if payload.started_at is not None:
            log.started_at = payload.started_at
        if payload.finished_at is not None:
            log.finished_at = payload.finished_at
        if payload.rating is not None:
            log.rating = payload.rating
        if payload.review is not None:
            log.review = payload.review

    _apply_auto_transitions(log, now=now)
    db.session.commit()
    return log


def quick_status_change(
    user: User,
    calibre_book_id: int,
    status: str,
    *,
    now: datetime | None = None,
) -> ReadingLog:
    """Single-field status update — used by the dashboard's quick controls."""
    if status not in READING_STATUSES:
        raise ReadingLogValidationError("invalid status")
    return upsert_reading_log(user, calibre_book_id, {"status": status}, now=now)


def create_read_attempt(
    user: User,
    calibre_book_id: int,
    data: dict,
    *,
    now: datetime | None = None,
) -> ReadingLog:
    """Insert a new read-attempt row — always a new row, never an update.

    Used by the UI form on ``/book/<id>/edit``. Every save the user makes
    on that page is a fresh attempt with its own dates / rating / status;
    edits to past attempts go through :func:`update_read_attempt`.

    First row for a book/user gets ``is_reread=False, reread_count=0``;
    subsequent rows get ``is_reread=True`` and a 1-indexed reread_count
    so the JSON API and any historical SQL can still distinguish "first
    read" from "reread N" if needed.
    """
    payload = validate_payload(data)
    existing_count = (
        db.session.query(ReadingLog)
        .filter_by(user_id=user.id, calibre_book_id=calibre_book_id)
        .count()
    )
    log = ReadingLog(
        user_id=user.id,
        calibre_book_id=calibre_book_id,
        # No explicit fallback for second+ attempts — every attempt is a
        # fresh "Currently reading" cycle now, and the form requires the
        # user to pick a status anyway.
        status=payload.status or "want_to_read",
        started_at=payload.started_at,
        finished_at=payload.finished_at,
        rating=payload.rating,
        review=payload.review,
        is_reread=existing_count > 0,
        reread_count=existing_count,
    )
    db.session.add(log)
    _apply_auto_transitions(log, now=now)
    db.session.commit()
    return log


def update_read_attempt(
    user: User,
    log_id: int,
    data: dict,
    *,
    now: datetime | None = None,
) -> ReadingLog:
    """Update an existing read-attempt row by primary key.

    Authorises by ``user_id`` — raises if the row doesn't belong to the
    caller (or doesn't exist). Used by the per-row edit pencil on the
    reading-activity table.
    """
    log = db.session.get(ReadingLog, log_id)
    if log is None or log.user_id != user.id:
        raise ReadingLogValidationError("read entry not found")

    payload = validate_payload(data)
    if payload.status is not None:
        log.status = payload.status
    if payload.started_at is not None:
        log.started_at = payload.started_at
    if payload.finished_at is not None:
        log.finished_at = payload.finished_at
    if payload.rating is not None:
        log.rating = payload.rating
    if payload.review is not None:
        log.review = payload.review

    _apply_auto_transitions(log, now=now)
    db.session.commit()
    return log


def delete_reading_log(user: User, calibre_book_id: int) -> bool:
    """Remove a book from the tracker — drops every read attempt for it.

    Used by the trash icon on the detail page, which reads as "remove
    this book from my tracker" rather than "remove one read attempt".
    Returns ``True`` if at least one row was deleted.
    """
    rows = (
        db.session.query(ReadingLog)
        .filter_by(user_id=user.id, calibre_book_id=calibre_book_id)
        .all()
    )
    if not rows:
        return False
    for row in rows:
        db.session.delete(row)
    db.session.commit()
    return True


def _next_reread_count(user_id: int, calibre_book_id: int) -> int:
    """Compute the next reread number for a book (1-indexed)."""
    existing = (
        db.session.query(ReadingLog)
        .filter_by(user_id=user_id, calibre_book_id=calibre_book_id, is_reread=True)
        .count()
    )
    return existing + 1


# ── Quotes & Notes (Phase 7) ────────────────────────────────────────────────


class AnnotationValidationError(ValueError):
    """Raised when an inbound quote or note payload fails validation."""


def _clean_text(raw: object, field: str, *, required: bool = False) -> str | None:
    """Strip a free-text field. Return ``None`` if blank (unless required)."""
    if raw is None:
        text = ""
    elif isinstance(raw, str):
        text = raw.strip()
    else:
        raise AnnotationValidationError(f"{field} must be text")
    if not text:
        if required:
            raise AnnotationValidationError(f"{field} is required")
        return None
    return text


def _clean_short(raw: object, field: str, *, max_len: int) -> str | None:
    """Strip + length-cap a short reference field (page, chapter)."""
    text = _clean_text(raw, field, required=False)
    if text is not None and len(text) > max_len:
        raise AnnotationValidationError(f"{field} must be ≤ {max_len} characters")
    return text


def _parse_note_type(raw: object) -> str:
    """Reject anything outside :data:`NOTE_TYPES`. Defaults to ``'general'``."""
    if raw is None or raw == "":
        return "general"
    if not isinstance(raw, str) or raw not in NOTE_TYPES:
        allowed = ", ".join(sorted(NOTE_TYPES))
        raise AnnotationValidationError(f"note_type must be one of: {allowed}")
    return raw


def create_quote(user: User, calibre_book_id: int, data: dict) -> Quote:
    """Insert a :class:`Quote` for the user. ``quote_text`` is required."""
    quote = Quote(
        user_id=user.id,
        calibre_book_id=calibre_book_id,
        quote_text=_clean_text(data.get("quote_text"), "quote_text", required=True),
        page_reference=_clean_short(data.get("page_reference"), "page_reference", max_len=64),
        chapter_reference=_clean_short(data.get("chapter_reference"), "chapter_reference", max_len=128),
        context_note=_clean_text(data.get("context_note"), "context_note"),
        is_favourite=bool(data.get("is_favourite")),
    )
    db.session.add(quote)
    db.session.commit()
    return quote


def update_quote(user: User, quote_id: int, data: dict) -> Quote:
    """Amend an existing quote. Authorises by ``user_id``."""
    quote = db.session.get(Quote, quote_id)
    if quote is None or quote.user_id != user.id:
        raise AnnotationValidationError("quote not found")
    quote.quote_text = _clean_text(data.get("quote_text"), "quote_text", required=True)
    quote.page_reference = _clean_short(data.get("page_reference"), "page_reference", max_len=64)
    quote.chapter_reference = _clean_short(
        data.get("chapter_reference"), "chapter_reference", max_len=128
    )
    quote.context_note = _clean_text(data.get("context_note"), "context_note")
    # The favourite flag has its own toggle endpoint; only honour it here
    # when the form explicitly includes the checkbox (so editing a quote
    # never silently un-favourites it).
    if "is_favourite" in data:
        quote.is_favourite = bool(data.get("is_favourite"))
    db.session.commit()
    return quote


def toggle_quote_favourite(user: User, quote_id: int) -> Quote:
    """Flip a quote's ``is_favourite`` flag. Authorises by ``user_id``."""
    quote = db.session.get(Quote, quote_id)
    if quote is None or quote.user_id != user.id:
        raise AnnotationValidationError("quote not found")
    quote.is_favourite = not quote.is_favourite
    db.session.commit()
    return quote


def delete_quote(user: User, quote_id: int) -> bool:
    """Delete a quote owned by ``user``. Returns ``False`` if it doesn't exist."""
    quote = db.session.get(Quote, quote_id)
    if quote is None or quote.user_id != user.id:
        return False
    db.session.delete(quote)
    db.session.commit()
    return True


def create_note(user: User, calibre_book_id: int, data: dict) -> Note:
    """Insert a :class:`Note` for the user. ``note_text`` is required."""
    note = Note(
        user_id=user.id,
        calibre_book_id=calibre_book_id,
        note_text=_clean_text(data.get("note_text"), "note_text", required=True),
        note_type=_parse_note_type(data.get("note_type")),
        page_reference=_clean_short(data.get("page_reference"), "page_reference", max_len=64),
        is_spoiler=bool(data.get("is_spoiler")),
    )
    db.session.add(note)
    db.session.commit()
    return note


def update_note(user: User, note_id: int, data: dict) -> Note:
    """Amend an existing note. Authorises by ``user_id``."""
    note = db.session.get(Note, note_id)
    if note is None or note.user_id != user.id:
        raise AnnotationValidationError("note not found")
    note.note_text = _clean_text(data.get("note_text"), "note_text", required=True)
    note.note_type = _parse_note_type(data.get("note_type"))
    note.page_reference = _clean_short(data.get("page_reference"), "page_reference", max_len=64)
    # Same logic as ``update_quote.is_favourite`` — only honour the flag
    # when the form explicitly includes it.
    if "is_spoiler" in data:
        note.is_spoiler = bool(data.get("is_spoiler"))
    db.session.commit()
    return note


def delete_note(user: User, note_id: int) -> bool:
    """Delete a note owned by ``user``. Returns ``False`` if it doesn't exist."""
    note = db.session.get(Note, note_id)
    if note is None or note.user_id != user.id:
        return False
    db.session.delete(note)
    db.session.commit()
    return True


# ── CWN import ──────────────────────────────────────────────────────────────


def maybe_run_cwn_import(user: User) -> int | None:
    """Run the one-time CWN read-status import iff it hasn't run yet.

    Returns the count of newly imported books, or ``None`` if the
    import has already happened for this user. Dashboard view calls
    this in Phase 6 and flashes a message when the return is not None.
    """
    if user.cwn_import_completed:
        return None

    from ..auth.cwa_bridge import import_cwn_read_status

    count = import_cwn_read_status(user, db.session)
    user.cwn_import_completed = True
    db.session.commit()
    return count
