"""Read-only bridge to Calibre-Web NextGen's ``app.db``.

Used for two things:

* Validating CWN-signed session cookies so an already-logged-in CWN user is
  transparently authenticated against the tracker.
* The one-time read-status import that seeds ``reading_log`` from CWN's
  ``book_read_link`` table.

Nothing in this module ever writes to ``app.db`` — the SQLite connection is
opened with ``mode=ro`` so writes would fail at the driver layer anyway.

────────────────────────────────────────────────────────────────────────────
Findings from a live ``new-usemame/calibre-web-nextgen`` container
(verified 2026-06-14 against the running image):

* Cookie name:  ``f"{COOKIE_PREFIX}session"``  (empty prefix → ``"session"``).
* Cookie format: standard Flask ``SecureCookieSessionInterface`` —
  itsdangerous ``URLSafeTimedSerializer`` with salt ``"cookie-session"``,
  ``TaggedJSONSerializer`` payload, and HMAC-SHA1 key derivation.
* Session payload keys:
    - ``_user_id``  — string form of ``user.id``
    - ``_random``   — random token bound to a ``user_session`` row
    - ``_id``       — the per-session ``session_key`` bound to that row
* CWN's secret key precedence (``cps/__init__.py:282``):
    1. env var ``SECRET_KEY``
    2. otherwise auto-generated and stored in ``app.db`` →
       ``flask_settings.flask_session_key`` (BLOB).
  The tracker only supports option 1: set ``SECRET_KEY`` explicitly in CWN
  and mirror it as ``CWA_SECRET_KEY`` in the tracker's env.
* ``user_session`` schema confirmed: ``(id, user_id, session_key, random, expiry)``.
  ``expiry`` is a Unix timestamp; ``0`` means "no expiry" (remember-me).
* CWN's own ``load_user`` (``cps/usermanagement.py``) filters
  ``user_session`` on ``(random, session_key)`` and then verifies the
  ``user_id`` matches — it does NOT enforce ``expiry``. The tracker is
  intentionally a touch stricter: we filter on all three columns *and*
  reject rows whose ``expiry`` is non-zero and in the past.
"""

from __future__ import annotations

import hashlib
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

import bcrypt
from flask import current_app
from flask.json.tag import TaggedJSONSerializer
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

if TYPE_CHECKING:
    from sqlalchemy.orm import Session as SqlaSession

    from ..tracker.models import User

# 30 days — matches the default Flask permanent-session lifetime.
_MAX_COOKIE_AGE_SECONDS = 86400 * 30


@contextmanager
def cwa_db_connection() -> Iterator[sqlite3.Connection]:
    """Yield a read-only connection to CWN's ``app.db``.

    The connection uses SQLite URI mode (``file:{path}?mode=ro``) so the
    SQLite driver itself rejects any write attempt — defence in depth on
    top of "we just don't issue writes from this module".
    """
    db_path = current_app.config["CWA_DB_PATH"]
    conn = sqlite3.connect(f"file:{db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    try:
        yield conn
    finally:
        conn.close()


def _signing_serializer(secret_key: str) -> URLSafeTimedSerializer:
    """Build a serializer with the same parameters Flask uses for sessions.

    These knobs match ``flask.sessions.SecureCookieSessionInterface``:
    salt ``"cookie-session"``, tagged-JSON payload, HMAC-SHA1 key
    derivation. Use the same shape or signature verification fails.
    """
    return URLSafeTimedSerializer(
        secret_key,
        salt="cookie-session",
        serializer=TaggedJSONSerializer(),
        signer_kwargs={"key_derivation": "hmac", "digest_method": hashlib.sha1},
    )


def decode_cwa_session(session_cookie: str) -> dict | None:
    """Decode a CWN-signed Flask session cookie.

    Returns the deserialized session dict, or ``None`` if the signature is
    invalid, the cookie has expired, or the configured secret is empty.
    """
    secret_key = current_app.config.get("CWA_SECRET_KEY") or ""
    if not secret_key:
        return None
    serializer = _signing_serializer(secret_key)
    try:
        data = serializer.loads(session_cookie, max_age=_MAX_COOKIE_AGE_SECONDS)
    except (BadSignature, SignatureExpired):
        return None
    return data if isinstance(data, dict) else None


def encode_cwa_session(session: dict, secret_key: str | None = None) -> str:
    """Encode a session dict the same way CWN/Flask does — test helper.

    Production code never calls this. It exists so tests can mint a
    valid-looking cookie without spinning up a real CWN.
    """
    secret = secret_key if secret_key is not None else current_app.config["CWA_SECRET_KEY"]
    return _signing_serializer(secret).dumps(session)


def get_cwa_user_by_id(cwa_user_id: int) -> dict | None:
    """Fetch a CWN user record by ID, or ``None`` if missing."""
    with cwa_db_connection() as conn:
        row = conn.execute(
            "SELECT id, name, email, role FROM user WHERE id = ?",
            (cwa_user_id,),
        ).fetchone()
    return dict(row) if row else None


def check_cwa_user_session(
    cwa_user_id: int,
    session_key: str | None,
    random_token: str | None = None,
    *,
    now: int | None = None,
) -> bool:
    """Confirm an active ``user_session`` row exists for these credentials.

    Matches CWN's own check on ``(user_id, session_key, random)`` and adds
    an explicit expiry guard (``expiry = 0`` means remember-me / never
    expires; otherwise the row must not be in the past).
    """
    if not session_key:
        return False
    import time

    current_ts = now if now is not None else int(time.time())
    with cwa_db_connection() as conn:
        if random_token is not None:
            row = conn.execute(
                """SELECT id FROM user_session
                   WHERE user_id = ?
                     AND session_key = ?
                     AND random = ?
                     AND (expiry = 0 OR expiry > ?)""",
                (cwa_user_id, session_key, random_token, current_ts),
            ).fetchone()
        else:
            row = conn.execute(
                """SELECT id FROM user_session
                   WHERE user_id = ?
                     AND session_key = ?
                     AND (expiry = 0 OR expiry > ?)""",
                (cwa_user_id, session_key, current_ts),
            ).fetchone()
    return row is not None


def validate_cwa_session(session_cookie: str) -> dict | None:
    """Full pipeline: decode → cross-check ``user_session`` → fetch user.

    Returns the CWN user dict (``id``, ``name``, ``email``, ``role``) when
    every stage passes, otherwise ``None``.
    """
    session_data = decode_cwa_session(session_cookie)
    if not session_data:
        return None

    raw_user_id = session_data.get("_user_id") or session_data.get("user_id")
    if raw_user_id is None:
        return None
    try:
        user_id = int(raw_user_id)
    except (TypeError, ValueError):
        return None

    session_key = session_data.get("_id")
    random_token = session_data.get("_random")
    if not check_cwa_user_session(user_id, session_key, random_token):
        return None

    return get_cwa_user_by_id(user_id)


def authenticate_cwa_credentials(username: str, password: str) -> dict | None:
    """Bcrypt-check a username/password against CWN's ``user`` table.

    Used only as the Scenario-B fallback when same-domain cookie sharing
    isn't possible. Returns the user dict on success, ``None`` otherwise.
    """
    if not username or not password:
        return None
    with cwa_db_connection() as conn:
        row = conn.execute(
            "SELECT id, name, email, password, role FROM user WHERE name = ?",
            (username,),
        ).fetchone()
    if not row:
        return None

    stored = (row["password"] or "").encode("utf-8")
    if not stored:
        return None
    try:
        if bcrypt.checkpw(password.encode("utf-8"), stored):
            return {k: row[k] for k in ("id", "name", "email", "role")}
    except ValueError:
        return None
    return None


def import_cwn_read_status(user: User, db_session: SqlaSession) -> int:
    """One-time import of CWN's read flags into the tracker.

    Reads every ``book_read_link`` row where ``read_status=1`` for this
    user and inserts a ``ReadingLog`` row with ``status='read'`` for any
    book that isn't already logged. Never overwrites existing tracker
    data. Caller is responsible for setting
    ``user.cwn_import_completed = True`` and committing.

    Returns the number of new rows inserted.
    """
    from ..tracker.models import ReadingLog  # local import — avoid Phase 3 circular

    with cwa_db_connection() as conn:
        rows = conn.execute(
            """SELECT book_id FROM book_read_link
               WHERE user_id = ? AND read_status = 1""",
            (user.cwa_user_id,),
        ).fetchall()

    imported = 0
    for row in rows:
        already_logged = (
            db_session.query(ReadingLog)
            .filter_by(user_id=user.id, calibre_book_id=row["book_id"])
            .first()
        )
        if already_logged:
            continue
        db_session.add(
            ReadingLog(
                user_id=user.id,
                calibre_book_id=row["book_id"],
                status="read",
            )
        )
        imported += 1
    return imported
