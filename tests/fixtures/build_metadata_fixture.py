"""Build a tiny ``metadata.db`` that mirrors Calibre's schema.

Run this script whenever the fixture needs to be regenerated:

    python tests/fixtures/build_metadata_fixture.py

It produces:

* ``tests/fixtures/calibre-library/metadata.db``
* ``tests/fixtures/calibre-library/<author>/<title> (<id>)/cover.jpg``

The schema and column names are a faithful subset of Calibre's own
``metadata.db`` — only the columns the tracker actually reads are included.
That keeps the fixture small while exercising the same code paths the
production database will.
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

FIXTURE_ROOT = Path(__file__).resolve().parent
LIBRARY_ROOT = FIXTURE_ROOT / "calibre-library"
DB_PATH = LIBRARY_ROOT / "metadata.db"

PNG_PIXEL = bytes.fromhex(
    "89504E470D0A1A0A0000000D49484452000000010000000108060000001F15C489"
    "0000000D49444154789C636060606000000005000148AFA4710000000049454E44"
    "AE426082"
)


@dataclass(frozen=True)
class FixtureBook:
    id: int
    title: str
    sort: str
    isbn: str
    pubdate: str
    series_index: float
    path: str
    has_cover: int
    authors: tuple[str, ...]
    tags: tuple[str, ...]
    series: str | None
    series_index_in_series: float | None


BOOKS: tuple[FixtureBook, ...] = (
    FixtureBook(
        id=1,
        title="The Long Way to a Small, Angry Planet",
        sort="Long Way to a Small Angry Planet, The",
        isbn="9780062444134",
        pubdate="2014-07-29 00:00:00+00:00",
        series_index=1.0,
        path="Becky Chambers/The Long Way to a Small, Angry Planet (1)",
        has_cover=1,
        authors=("Becky Chambers",),
        tags=("Science Fiction", "Space Opera"),
        series="Wayfarers",
        series_index_in_series=1.0,
    ),
    FixtureBook(
        id=2,
        title="A Closed and Common Orbit",
        sort="Closed and Common Orbit, A",
        isbn="9780062569400",
        pubdate="2016-10-20 00:00:00+00:00",
        series_index=2.0,
        path="Becky Chambers/A Closed and Common Orbit (2)",
        has_cover=1,
        authors=("Becky Chambers",),
        tags=("Science Fiction", "Space Opera"),
        series="Wayfarers",
        series_index_in_series=2.0,
    ),
    FixtureBook(
        id=3,
        title="Piranesi",
        sort="Piranesi",
        isbn="9781635575637",
        pubdate="2020-09-15 00:00:00+00:00",
        series_index=1.0,
        path="Susanna Clarke/Piranesi (3)",
        has_cover=1,
        authors=("Susanna Clarke",),
        tags=("Fantasy", "Literary"),
        series=None,
        series_index_in_series=None,
    ),
    FixtureBook(
        id=4,
        title="The Fifth Season",
        sort="Fifth Season, The",
        isbn="9780316229296",
        pubdate="2015-08-04 00:00:00+00:00",
        series_index=1.0,
        path="N. K. Jemisin/The Fifth Season (4)",
        has_cover=1,
        authors=("N. K. Jemisin",),
        tags=("Science Fiction", "Fantasy"),
        series="The Broken Earth",
        series_index_in_series=1.0,
    ),
    FixtureBook(
        id=5,
        title="Good Omens",
        sort="Good Omens",
        isbn="9780060853983",
        pubdate="1990-05-01 00:00:00+00:00",
        series_index=1.0,
        path="Terry Pratchett & Neil Gaiman/Good Omens (5)",
        has_cover=0,  # intentionally cover-less to test the negative path
        authors=("Terry Pratchett", "Neil Gaiman"),
        tags=("Fantasy", "Humor"),
        series=None,
        series_index_in_series=None,
    ),
    FixtureBook(
        id=6,
        title="Project Hail Mary",
        sort="Project Hail Mary",
        isbn="9780593135204",
        pubdate="2021-05-04 00:00:00+00:00",
        series_index=1.0,
        path="Andy Weir/Project Hail Mary (6)",
        has_cover=1,
        authors=("Andy Weir",),
        tags=("Science Fiction",),
        series=None,
        series_index_in_series=None,
    ),
)


SCHEMA = """
CREATE TABLE books (
    id        INTEGER PRIMARY KEY AUTOINCREMENT,
    title     TEXT NOT NULL DEFAULT 'Unknown' COLLATE NOCASE,
    sort      TEXT COLLATE NOCASE,
    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    pubdate   TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    series_index REAL NOT NULL DEFAULT 1.0,
    author_sort TEXT COLLATE NOCASE,
    path      TEXT NOT NULL DEFAULT '',
    uuid      TEXT,
    has_cover BOOL DEFAULT 0,
    last_modified TIMESTAMP NOT NULL DEFAULT '2000-01-01 00:00:00+00:00'
);

CREATE TABLE authors (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    sort TEXT COLLATE NOCASE,
    link TEXT NOT NULL DEFAULT ''
);

CREATE TABLE books_authors_link (
    id     INTEGER PRIMARY KEY,
    book   INTEGER NOT NULL,
    author INTEGER NOT NULL,
    UNIQUE(book, author)
);

CREATE TABLE series (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    sort TEXT COLLATE NOCASE,
    UNIQUE(name)
);

CREATE TABLE books_series_link (
    id     INTEGER PRIMARY KEY,
    book   INTEGER NOT NULL,
    series INTEGER NOT NULL,
    UNIQUE(book)
);

CREATE TABLE tags (
    id   INTEGER PRIMARY KEY,
    name TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(name)
);

CREATE TABLE books_tags_link (
    id   INTEGER PRIMARY KEY,
    book INTEGER NOT NULL,
    tag  INTEGER NOT NULL,
    UNIQUE(book, tag)
);

CREATE TABLE identifiers (
    id   INTEGER PRIMARY KEY,
    book INTEGER NOT NULL,
    type TEXT NOT NULL DEFAULT 'isbn' COLLATE NOCASE,
    val  TEXT NOT NULL COLLATE NOCASE,
    UNIQUE(book, type)
);

CREATE TABLE comments (
    id   INTEGER PRIMARY KEY,
    book INTEGER NOT NULL,
    text TEXT NOT NULL,
    UNIQUE(book)
);
"""


def _upsert_lookup(conn: sqlite3.Connection, table: str, name: str) -> int:
    """Insert ``name`` into a (name UNIQUE) table and return the row id."""
    cur = conn.execute(f"SELECT id FROM {table} WHERE name = ?", (name,))
    row = cur.fetchone()
    if row:
        return int(row[0])
    cur = conn.execute(f"INSERT INTO {table} (name) VALUES (?)", (name,))
    return int(cur.lastrowid)


def build(library_root: Path = LIBRARY_ROOT, db_path: Path = DB_PATH) -> None:
    """Build (or rebuild) the fixture database and cover files on disk."""
    if db_path.exists():
        db_path.unlink()
    library_root.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(db_path)
    try:
        conn.executescript(SCHEMA)
        for book in BOOKS:
            conn.execute(
                """INSERT INTO books
                   (id, title, sort, pubdate, series_index, path, has_cover, uuid)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    book.id,
                    book.title,
                    book.sort,
                    book.pubdate,
                    book.series_index,
                    book.path,
                    book.has_cover,
                    f"uuid-{book.id}",
                ),
            )
            if book.isbn:
                conn.execute(
                    "INSERT INTO identifiers (book, type, val) VALUES (?, 'isbn', ?)",
                    (book.id, book.isbn),
                )

            for author_name in book.authors:
                author_id = _upsert_lookup(conn, "authors", author_name)
                conn.execute(
                    """UPDATE authors SET sort = ? WHERE id = ? AND sort IS NULL""",
                    (
                        author_name.split()[-1] + ", " + " ".join(author_name.split()[:-1]),
                        author_id,
                    ),
                )
                conn.execute(
                    "INSERT OR IGNORE INTO books_authors_link (book, author) VALUES (?, ?)",
                    (book.id, author_id),
                )

            for tag_name in book.tags:
                tag_id = _upsert_lookup(conn, "tags", tag_name)
                conn.execute(
                    "INSERT OR IGNORE INTO books_tags_link (book, tag) VALUES (?, ?)",
                    (book.id, tag_id),
                )

            if book.series:
                series_id = _upsert_lookup(conn, "series", book.series)
                conn.execute(
                    "INSERT OR IGNORE INTO books_series_link (book, series) VALUES (?, ?)",
                    (book.id, series_id),
                )

            book_dir = library_root / book.path
            book_dir.mkdir(parents=True, exist_ok=True)
            if book.has_cover:
                (book_dir / "cover.jpg").write_bytes(PNG_PIXEL)

        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    build()
    print(f"Wrote {DB_PATH} and covers under {LIBRARY_ROOT}")
