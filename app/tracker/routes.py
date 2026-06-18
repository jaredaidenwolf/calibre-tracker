"""Reading-log routes + Phase 6 dashboard, book detail, search, covers.

Two API surfaces share this blueprint:

* JSON CRUD for the reading log (Phase 4) — kept ``csrf.exempt`` since the
  caller doesn't ship a WTForms token.
* Server-rendered pages (Phase 6) — dashboard, book detail, search, cover
  proxy. Form submissions carry CSRF tokens.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from flask import (
    Blueprint,
    abort,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    send_from_directory,
    url_for,
)
from flask_login import current_user, login_required

from ..calibre.repository import get_book, get_books, search_books
from ..extensions import csrf
from .models import ReadingLog
from .service import (
    ReadingLogValidationError,
    create_read_attempt,
    delete_reading_log,
    maybe_run_cwn_import,
    quick_status_change,
    update_read_attempt,
    upsert_reading_log,
)

if TYPE_CHECKING:
    from flask import Flask, Response

tracker_bp = Blueprint("tracker", __name__)


# ── Dashboard helpers ───────────────────────────────────────────────────────


def _latest_attempt_per_book(user_id: int) -> list[ReadingLog]:
    """Return one row per book — the user's most recent read attempt.

    Walks every reading-log row for the user in created_at-desc order
    and keeps the first row seen per ``calibre_book_id``. Cheap on the
    dataset sizes a personal library produces; no need for a window
    function here.
    """
    rows = (
        ReadingLog.query.filter_by(user_id=user_id)
        .order_by(ReadingLog.created_at.desc(), ReadingLog.id.desc())
        .all()
    )
    seen: dict[int, ReadingLog] = {}
    for row in rows:
        if row.calibre_book_id not in seen:
            seen[row.calibre_book_id] = row
    return list(seen.values())


def _logs_by_status(user_id: int) -> dict[str, list[ReadingLog]]:
    """Group books by their latest attempt's ``status``.

    Each book appears exactly once, in the bucket of its most-recent
    attempt. Lists are freshest-first (latest attempt's created_at).
    """
    grouped: dict[str, list[ReadingLog]] = {}
    for row in _latest_attempt_per_book(user_id):
        grouped.setdefault(row.status, []).append(row)
    return grouped


def _attach_books(logs: list[ReadingLog]) -> list[dict]:
    """Pair each log row with its Calibre book DTO (or a tombstone)."""
    if not logs:
        return []
    books = {b.id: b for b in get_books([log.calibre_book_id for log in logs])}
    return [
        {
            "log": log,
            "book": books.get(log.calibre_book_id),
        }
        for log in logs
    ]


def _quick_stats(user_id: int) -> dict:
    """Cheap stats strip for the dashboard — Phase 9 ships the real page."""
    from datetime import UTC, datetime

    from sqlalchemy import func as sa_func

    from ..extensions import db

    now = datetime.now(UTC)
    finished_this_year = (
        db.session.query(sa_func.count(ReadingLog.id))
        .filter(
            ReadingLog.user_id == user_id,
            ReadingLog.status == "read",
            ReadingLog.finished_at.isnot(None),
            sa_func.strftime("%Y", ReadingLog.finished_at) == str(now.year),
        )
        .scalar()
        or 0
    )
    total_read = (
        db.session.query(sa_func.count(ReadingLog.id))
        .filter(ReadingLog.user_id == user_id, ReadingLog.status == "read")
        .scalar()
        or 0
    )
    currently_reading = (
        db.session.query(sa_func.count(ReadingLog.id))
        .filter(ReadingLog.user_id == user_id, ReadingLog.status == "reading")
        .scalar()
        or 0
    )
    want_to_read = (
        db.session.query(sa_func.count(ReadingLog.id))
        .filter(ReadingLog.user_id == user_id, ReadingLog.status == "want_to_read")
        .scalar()
        or 0
    )
    avg_rating = (
        db.session.query(sa_func.avg(ReadingLog.rating))
        .filter(ReadingLog.user_id == user_id, ReadingLog.rating.isnot(None))
        .scalar()
    )
    return {
        "finished_this_year": finished_this_year,
        "total_read": total_read,
        "currently_reading": currently_reading,
        "want_to_read": want_to_read,
        "avg_rating": float(avg_rating) if avg_rating else None,
        "year": now.year,
    }


# ── Dashboard ───────────────────────────────────────────────────────────────


DASHBOARD_SECTION_LIMIT = 5
"""Each dashboard section shows up to this many books in a single row.
The full list per status lives at ``tracker.status_list``."""


STATUS_LABELS: dict[str, str] = {
    "reading": "Currently Reading",
    "want_to_read": "Want to Read",
    "read": "Finished",
    "dnf": "Did Not Finish",
}


@tracker_bp.get("/")
@login_required
def dashboard() -> Response:
    """The user's home page — sections per status + a quick-stats strip."""
    imported = maybe_run_cwn_import(current_user)
    if imported:
        flash(
            f"We found {imported} books you've already read in Calibre Web — "
            "they've been added to your reading log. Add dates and ratings whenever you like.",
            "info",
        )

    grouped = _logs_by_status(current_user.id)
    dashboard_order = ("reading", "want_to_read", "read", "dnf")
    sections = []
    for key in dashboard_order:
        all_logs = grouped.get(key, [])
        sections.append(
            {
                "title": STATUS_LABELS[key],
                "status_key": key,
                "entries": _attach_books(all_logs[:DASHBOARD_SECTION_LIMIT]),
                "total": len(all_logs),
                "limit": DASHBOARD_SECTION_LIMIT,
            }
        )

    return render_template(
        "tracker/dashboard.html",
        sections=sections,
        stats=_quick_stats(current_user.id),
    )


@tracker_bp.get("/list/<status>")
@login_required
def status_list(status: str) -> Response:
    """Show every book the user has logged under a single status.

    The special status ``"all"`` lists every book in the tracker
    (one row per book, using each book's latest read attempt). Other
    statuses must be one of the keys in :data:`STATUS_LABELS`.

    No pagination yet — returns the full list. Real pagination can come
    later if libraries get big enough that this matters.
    """
    if status == "all":
        logs = _latest_attempt_per_book(current_user.id)
        title = "All books"
    elif status in STATUS_LABELS:
        logs = _logs_by_status(current_user.id).get(status, [])
        title = STATUS_LABELS[status]
    else:
        abort(404)
    return render_template(
        "tracker/status_list.html",
        status=status,
        title=title,
        entries=_attach_books(logs),
        total=len(logs),
    )


# ── Book detail ─────────────────────────────────────────────────────────────


def _attempts_for(book_id: int) -> list[ReadingLog]:
    """Return all of the current user's read attempts for ``book_id``.

    Ordered oldest → newest so the reading-activity table reads
    chronologically (row 1 = first read, row 2 = first reread, …).
    """
    return (
        ReadingLog.query.filter_by(user_id=current_user.id, calibre_book_id=book_id)
        .order_by(ReadingLog.created_at.asc(), ReadingLog.id.asc())
        .all()
    )


def _form_payload() -> dict:
    """Pluck the reading-log fields out of ``request.form``."""
    return {
        "status": request.form.get("status") or None,
        "started_at": request.form.get("started_at") or None,
        "finished_at": request.form.get("finished_at") or None,
        "rating": request.form.get("rating") or None,
        "review": request.form.get("review") or None,
    }


@tracker_bp.get("/book/<int:book_id>")
@login_required
def book_detail(book_id: int) -> Response:
    """Read-only display of a book + reading-activity table.

    The reading-log form lives on :func:`book_edit` (new attempt) and
    :func:`book_edit_entry` (amend a specific past attempt). Keeping
    display and edit separate means multi-read history can be a
    first-class table here instead of a clutter of input fields.
    """
    book = get_book(book_id)
    if book is None:
        abort(404)
    attempts = _attempts_for(book_id)
    # Current row = most recently created. Drives the status chip up top
    # + the rating-under-author display.
    current = attempts[-1] if attempts else None
    return render_template(
        "tracker/book_detail.html",
        book=book,
        attempts=attempts,
        current=current,
    )


@tracker_bp.post("/book/<int:book_id>")
@login_required
def book_detail_submit(book_id: int) -> Response:
    """Handle the delete action from the detail page.

    Save/edit lives on :func:`book_edit_submit` / :func:`book_edit_entry_submit`;
    this endpoint only accepts ``action=delete`` so the detail page never
    has to ship a full form just to wire the trash icon. Delete removes
    every read attempt for the book, since the trash icon reads as
    "remove this book from my tracker".
    """
    if get_book(book_id) is None:
        abort(404)
    if request.form.get("action") == "delete":
        if delete_reading_log(current_user, book_id):
            flash("Removed from your reading log.", "success")
        else:
            flash("Nothing to remove — book wasn't in your log.", "warning")
    return redirect(url_for("tracker.book_detail", book_id=book_id))


@tracker_bp.get("/book/<int:book_id>/edit")
@login_required
def book_edit(book_id: int) -> Response:
    """Blank-by-default form for adding a NEW read attempt.

    Status / dates / rating start empty. Review is pre-filled from the
    user's most recent attempt (treating "my overall take on this
    book" as carrying forward — they can edit or clear it).

    To amend a past attempt instead, use :func:`book_edit_entry`.
    """
    book = get_book(book_id)
    if book is None:
        abort(404)
    attempts = _attempts_for(book_id)
    prefill_review = attempts[-1].review if attempts else None
    return render_template(
        "tracker/book_edit.html",
        book=book,
        log=None,                   # blank form
        prefill_review=prefill_review,
        mode="new",
        attempt_index=len(attempts) + 1,
    )


@tracker_bp.post("/book/<int:book_id>/edit")
@login_required
def book_edit_submit(book_id: int) -> Response:
    """Insert a NEW read attempt and bounce back to the detail page.

    Plain form encoding — CSRF protection comes from Flask-WTF (the
    ``csrf_token`` hidden input rendered by the template). Bad input
    flashes the error and re-renders the edit page rather than throwing
    a 400 in the user's face.
    """
    if get_book(book_id) is None:
        abort(404)

    try:
        create_read_attempt(current_user, book_id, _form_payload())
    except ReadingLogValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("tracker.book_edit", book_id=book_id))

    flash("Read entry added.", "success")
    return redirect(url_for("tracker.book_detail", book_id=book_id))


@tracker_bp.get("/book/<int:book_id>/edit/<int:entry_id>")
@login_required
def book_edit_entry(book_id: int, entry_id: int) -> Response:
    """Form pre-filled for amending a specific past read attempt."""
    book = get_book(book_id)
    if book is None:
        abort(404)
    log = ReadingLog.query.filter_by(
        id=entry_id, user_id=current_user.id, calibre_book_id=book_id
    ).first()
    if log is None:
        abort(404)
    attempts = _attempts_for(book_id)
    attempt_index = next(
        (i + 1 for i, row in enumerate(attempts) if row.id == log.id),
        len(attempts),
    )
    return render_template(
        "tracker/book_edit.html",
        book=book,
        log=log,
        mode="edit",
        attempt_index=attempt_index,
    )


@tracker_bp.post("/book/<int:book_id>/edit/<int:entry_id>")
@login_required
def book_edit_entry_submit(book_id: int, entry_id: int) -> Response:
    """Update the specific read-attempt row identified by ``entry_id``."""
    if get_book(book_id) is None:
        abort(404)

    try:
        update_read_attempt(current_user, entry_id, _form_payload())
    except ReadingLogValidationError as exc:
        flash(str(exc), "danger")
        return redirect(
            url_for("tracker.book_edit_entry", book_id=book_id, entry_id=entry_id)
        )

    flash("Read entry updated.", "success")
    return redirect(url_for("tracker.book_detail", book_id=book_id))


# ── Search ──────────────────────────────────────────────────────────────────


@tracker_bp.get("/search")
@login_required
def search() -> Response:
    """Find a book in the Calibre library and let the user log it."""
    query = (request.args.get("q") or "").strip()
    results = search_books(query, limit=50) if query else []
    # Each hit's badge shows the user's CURRENT status for that book,
    # i.e. the status of their most recent read attempt. We walk the log
    # in created_at-desc order and keep the first row seen per book.
    statuses: dict[int, str] = {}
    if results:
        rows = (
            ReadingLog.query.filter(
                ReadingLog.user_id == current_user.id,
                ReadingLog.calibre_book_id.in_([b.id for b in results]),
            )
            .order_by(ReadingLog.created_at.desc(), ReadingLog.id.desc())
            .all()
        )
        for row in rows:
            statuses.setdefault(row.calibre_book_id, row.status)
    return render_template(
        "tracker/search.html",
        query=query,
        results=results,
        statuses=statuses,
    )


# ── Covers ──────────────────────────────────────────────────────────────────


@tracker_bp.get("/cover/<int:book_id>")
@login_required
def cover(book_id: int) -> Response:
    """Stream a Calibre book cover from the read-only library mount.

    The filesystem is never exposed directly — :func:`send_from_directory`
    enforces that the resolved path stays under ``CALIBRE_LIBRARY_PATH``,
    so a crafted ``book_id`` can't reach files outside the library.
    """
    book = get_book(book_id)
    if book is None or not book.has_cover:
        abort(404)
    library_root = current_app.config.get("CALIBRE_LIBRARY_PATH") or str(
        Path(current_app.config["CALIBRE_DB_PATH"]).parent
    )
    relative = Path(book.path) / "cover.jpg"
    if not (Path(library_root) / relative).is_file():
        abort(404)
    return send_from_directory(library_root, str(relative), mimetype="image/jpeg")


# ── Phase 4 JSON CRUD (unchanged) ───────────────────────────────────────────


def _serialize_log(log: ReadingLog) -> dict:
    return {
        "id": log.id,
        "user_id": log.user_id,
        "calibre_book_id": log.calibre_book_id,
        "status": log.status,
        "started_at": log.started_at.isoformat() if log.started_at else None,
        "finished_at": log.finished_at.isoformat() if log.finished_at else None,
        "rating": log.rating,
        "review": log.review,
        "is_reread": log.is_reread,
        "reread_count": log.reread_count,
    }


def _read_payload() -> dict:
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


@tracker_bp.get("/book/<int:book_id>/log")
@login_required
def get_log(book_id: int) -> Response:
    rows = (
        ReadingLog.query.filter_by(user_id=current_user.id, calibre_book_id=book_id)
        .order_by(ReadingLog.is_reread.asc(), ReadingLog.created_at.asc())
        .all()
    )
    canonical = next((r for r in rows if not r.is_reread), None)
    rereads = [r for r in rows if r.is_reread]
    return jsonify(
        {
            "calibre_book_id": book_id,
            "status": canonical.status if canonical else None,
            "current": _serialize_log(canonical) if canonical else None,
            "rereads": [_serialize_log(r) for r in rereads],
        }
    )


@tracker_bp.post("/book/<int:book_id>/log")
@csrf.exempt
@login_required
def post_log(book_id: int) -> Response:
    try:
        log = upsert_reading_log(current_user, book_id, _read_payload())
    except ReadingLogValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_serialize_log(log))


@tracker_bp.post("/book/<int:book_id>/status")
@csrf.exempt
@login_required
def post_status(book_id: int) -> Response:
    status = _read_payload().get("status", "")
    try:
        log = quick_status_change(current_user, book_id, status)
    except ReadingLogValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_serialize_log(log))


@tracker_bp.delete("/book/<int:book_id>/log")
@csrf.exempt
@login_required
def delete_log(book_id: int) -> Response:
    removed = delete_reading_log(current_user, book_id)
    if not removed:
        return jsonify({"error": "no log entry to delete"}), 404
    return jsonify({"deleted": True, "calibre_book_id": book_id})


def register_tracker(app: Flask) -> None:
    """Mount the tracker blueprint on the application."""
    app.register_blueprint(tracker_bp)
