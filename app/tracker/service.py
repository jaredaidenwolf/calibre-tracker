"""Business logic for the reading log.

Routes call into this module so the rules around status transitions,
rereads, validation, and the one-time CWN import live in one place that
can be tested without spinning up the request layer.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from ..extensions import db
from .models import READING_STATUSES, ReadingLog, User


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
            status=payload.status or "re_reading",
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


def delete_reading_log(user: User, calibre_book_id: int) -> bool:
    """Soft-friendly delete: removes the canonical (non-reread) row.

    Reread rows are deliberately kept; ``current_status_for`` ignores
    them anyway, and they preserve the history of past reads. Returns
    ``True`` if a row was deleted.
    """
    log = (
        db.session.query(ReadingLog)
        .filter_by(user_id=user.id, calibre_book_id=calibre_book_id, is_reread=False)
        .first()
    )
    if log is None:
        return False
    db.session.delete(log)
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
