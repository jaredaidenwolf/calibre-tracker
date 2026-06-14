"""Reading-log routes.

Phase 4 ships the JSON CRUD surface — Phase 6 will swap callers to
server-rendered forms backed by these same endpoints. Every route is
``login_required`` and scoped to ``current_user``; cross-user access is
structurally impossible because the lookups are always filtered by
``user_id``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from flask import Blueprint, jsonify, request
from flask_login import current_user, login_required

from ..extensions import csrf
from .models import ReadingLog
from .service import (
    ReadingLogValidationError,
    delete_reading_log,
    quick_status_change,
    upsert_reading_log,
)

if TYPE_CHECKING:
    from flask import Flask, Response

tracker_bp = Blueprint("tracker", __name__)


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
    """Accept either JSON or form-encoded bodies — routes shouldn't care."""
    if request.is_json:
        return request.get_json(silent=True) or {}
    return request.form.to_dict()


@tracker_bp.get("/book/<int:book_id>/log")
@login_required
def get_log(book_id: int) -> Response:
    """Return the current (non-reread) log row plus any reread rows."""
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
@csrf.exempt  # JSON callers don't supply WTForms tokens; CSRF lands with HTML forms in Phase 6
@login_required
def post_log(book_id: int) -> Response:
    """Create or update the user's log entry for a book."""
    try:
        log = upsert_reading_log(current_user, book_id, _read_payload())
    except ReadingLogValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_serialize_log(log))


@tracker_bp.post("/book/<int:book_id>/status")
@csrf.exempt
@login_required
def post_status(book_id: int) -> Response:
    """Quick status-only update."""
    payload = _read_payload()
    status = payload.get("status", "")
    try:
        log = quick_status_change(current_user, book_id, status)
    except ReadingLogValidationError as exc:
        return jsonify({"error": str(exc)}), 400
    return jsonify(_serialize_log(log))


@tracker_bp.delete("/book/<int:book_id>/log")
@csrf.exempt
@login_required
def delete_log(book_id: int) -> Response:
    """Remove the canonical log row (rereads are preserved)."""
    removed = delete_reading_log(current_user, book_id)
    if not removed:
        return jsonify({"error": "no log entry to delete"}), 404
    return jsonify({"deleted": True, "calibre_book_id": book_id})


def register_tracker(app: Flask) -> None:
    """Mount the tracker blueprint on the application."""
    app.register_blueprint(tracker_bp)
