"""Build a tiny ``app.db`` that mirrors Calibre-Web NextGen's schema.

The schema below was captured from a live ``new-usemame/calibre-web-nextgen``
container — only the columns and tables the tracker actually reads are
included. The fixture seeds two users (one admin-ish, one regular), a few
``user_session`` rows (one active, one expired), and a handful of
``book_read_link`` rows so the Phase 4 import path has something to import.
"""

from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

import bcrypt

FIXTURE_ROOT = Path(__file__).resolve().parent
CWA_ROOT = FIXTURE_ROOT / "cwa"
DB_PATH = CWA_ROOT / "app.db"


SCHEMA = """
CREATE TABLE user (
    id INTEGER NOT NULL PRIMARY KEY AUTOINCREMENT,
    name VARCHAR(64),
    email VARCHAR(120),
    role SMALLINT,
    password VARCHAR,
    kindle_mail VARCHAR(120),
    locale VARCHAR(2),
    sidebar_view INTEGER,
    default_language VARCHAR(3),
    denied_tags VARCHAR,
    allowed_tags VARCHAR,
    denied_column_value VARCHAR,
    allowed_column_value VARCHAR,
    view_settings JSON,
    UNIQUE (name),
    UNIQUE (email)
);

CREATE TABLE user_session (
    id INTEGER NOT NULL PRIMARY KEY,
    user_id INTEGER,
    session_key VARCHAR,
    random VARCHAR,
    expiry INTEGER,
    FOREIGN KEY(user_id) REFERENCES user(id)
);

CREATE TABLE book_read_link (
    id INTEGER NOT NULL PRIMARY KEY,
    book_id INTEGER,
    user_id INTEGER,
    read_status INTEGER NOT NULL,
    last_modified DATETIME,
    last_time_started_reading DATETIME,
    times_started_reading INTEGER NOT NULL DEFAULT 0,
    CONSTRAINT uq_book_read_link_user_book UNIQUE (user_id, book_id),
    FOREIGN KEY(user_id) REFERENCES user(id)
);

CREATE TABLE flask_settings (
    id INTEGER NOT NULL PRIMARY KEY,
    flask_session_key BLOB
);
"""


@dataclass(frozen=True)
class FixtureUser:
    id: int
    name: str
    email: str
    role: int
    plaintext_password: str


@dataclass(frozen=True)
class FixtureSession:
    id: int
    user_id: int
    session_key: str
    random: str
    expiry: int  # 0 = no expiry; >0 = Unix timestamp


@dataclass(frozen=True)
class FixtureReadLink:
    user_id: int
    book_id: int
    read_status: int  # 1 = read, 0 = unread


USERS: tuple[FixtureUser, ...] = (
    FixtureUser(
        id=1, name="alice", email="alice@example.com", role=15, plaintext_password="alice-pw"
    ),
    FixtureUser(id=2, name="bob", email="bob@example.com", role=2, plaintext_password="bob-pw"),
)


SESSIONS: tuple[FixtureSession, ...] = (
    # Alice has one active, no-expiry session
    FixtureSession(id=1, user_id=1, session_key="alice-sk-active", random="alice-rand", expiry=0),
    # Bob has one expired session (epoch 1) — used to test the expiry guard
    FixtureSession(id=2, user_id=2, session_key="bob-sk-expired", random="bob-rand", expiry=1),
)


# Alice has read books 1, 2, 3 in the fixture metadata.db. Book 6 is "unread".
READ_LINKS: tuple[FixtureReadLink, ...] = (
    FixtureReadLink(user_id=1, book_id=1, read_status=1),
    FixtureReadLink(user_id=1, book_id=2, read_status=1),
    FixtureReadLink(user_id=1, book_id=3, read_status=1),
    FixtureReadLink(user_id=1, book_id=6, read_status=0),
)


def _hash(pw: str) -> str:
    return bcrypt.hashpw(pw.encode("utf-8"), bcrypt.gensalt(rounds=4)).decode("utf-8")


def build(db_path: Path = DB_PATH) -> None:
    """Build (or rebuild) the fixture CWA ``app.db``."""
    if db_path.exists():
        db_path.unlink()
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for user in USERS:
            conn.execute(
                """INSERT INTO user (id, name, email, role, password)
                   VALUES (?, ?, ?, ?, ?)""",
                (user.id, user.name, user.email, user.role, _hash(user.plaintext_password)),
            )
        for session in SESSIONS:
            conn.execute(
                """INSERT INTO user_session (id, user_id, session_key, random, expiry)
                   VALUES (?, ?, ?, ?, ?)""",
                (session.id, session.user_id, session.session_key, session.random, session.expiry),
            )
        now = int(time.time())
        for link in READ_LINKS:
            conn.execute(
                """INSERT INTO book_read_link
                   (book_id, user_id, read_status, last_modified, times_started_reading)
                   VALUES (?, ?, ?, ?, ?)""",
                (link.book_id, link.user_id, link.read_status, now, 0),
            )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    build()
    print(f"Wrote {DB_PATH}")
