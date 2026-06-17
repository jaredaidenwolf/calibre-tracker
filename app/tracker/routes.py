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
    delete_reading_log,
    maybe_run_cwn_import,
    quick_status_change,
    upsert_reading_log,
)

if TYPE_CHECKING:
    from flask import Flask, Response

tracker_bp = Blueprint("tracker", __name__)


# ── Dashboard helpers ───────────────────────────────────────────────────────


def _logs_by_status(user_id: int) -> dict[str, list[ReadingLog]]:
    """Group a user's non-reread logs by ``status``.

    Each list is freshest-first (most recently updated).
    """
    rows = (
        ReadingLog.query.filter_by(user_id=user_id, is_reread=False)
        .order_by(ReadingLog.updated_at.desc())
        .all()
    )
    grouped: dict[str, list[ReadingLog]] = {}
    for row in rows:
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
    "re_reading": "Re-reading",
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

    The special status ``"all"`` shows every logged book (excluding
    rereads), freshest-first. Otherwise ``status`` must be one of the
    keys in :data:`STATUS_LABELS`.

    No pagination yet — returns the full list. Real pagination can come
    later if libraries get big enough that this matters.
    """
    if status == "all":
        logs = (
            ReadingLog.query.filter_by(user_id=current_user.id, is_reread=False)
            .order_by(ReadingLog.updated_at.desc())
            .all()
        )
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


@tracker_bp.get("/book/<int:book_id>")
@login_required
def book_detail(book_id: int) -> Response:
    """Cover + metadata + the reading-log form for ``book_id``."""
    book = get_book(book_id)
    if book is None:
        abort(404)
    log = ReadingLog.query.filter_by(
        user_id=current_user.id, calibre_book_id=book_id, is_reread=False
    ).first()
    rereads = (
        ReadingLog.query.filter_by(user_id=current_user.id, calibre_book_id=book_id, is_reread=True)
        .order_by(ReadingLog.reread_count.asc())
        .all()
    )
    return render_template(
        "tracker/book_detail.html",
        book=book,
        log=log,
        rereads=rereads,
    )


@tracker_bp.post("/book/<int:book_id>")
@login_required
def book_detail_submit(book_id: int) -> Response:
    """Handle the reading-log form on the book-detail page.

    Plain form encoding — CSRF protection comes from Flask-WTF (the
    ``csrf_token`` hidden input rendered by the template). Bad input
    flashes the error and re-renders rather than throwing a 400 in
    the user's face.
    """
    book = get_book(book_id)
    if book is None:
        abort(404)

    payload = {
        "status": request.form.get("status") or None,
        "started_at": request.form.get("started_at") or None,
        "finished_at": request.form.get("finished_at") or None,
        "rating": request.form.get("rating") or None,
        "review": request.form.get("review") or None,
        "is_reread": bool(request.form.get("is_reread")),
    }
    # The "delete" button submits with action=delete; handle before validation
    # so a delete request never has to satisfy the rating/status rules.
    if request.form.get("action") == "delete":
        if delete_reading_log(current_user, book_id):
            flash("Removed from your reading log.", "success")
        else:
            flash("Nothing to remove — book wasn't in your log.", "warning")
        return redirect(url_for("tracker.book_detail", book_id=book_id))

    try:
        upsert_reading_log(current_user, book_id, payload)
    except ReadingLogValidationError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("tracker.book_detail", book_id=book_id))

    flash("Reading log updated.", "success")
    return redirect(url_for("tracker.book_detail", book_id=book_id))


# ── Search ──────────────────────────────────────────────────────────────────


@tracker_bp.get("/search")
@login_required
def search() -> Response:
    """Find a book in the Calibre library and let the user log it."""
    query = (request.args.get("q") or "").strip()
    results = search_books(query, limit=50) if query else []
    # Pull the current statuses for each hit so the badges render correctly.
    statuses: dict[int, str] = {}
    if results:
        rows = ReadingLog.query.filter(
            ReadingLog.user_id == current_user.id,
            ReadingLog.calibre_book_id.in_([b.id for b in results]),
            ReadingLog.is_reread.is_(False),
        ).all()
        statuses = {row.calibre_book_id: row.status for row in rows}
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
