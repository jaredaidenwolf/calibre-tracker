"""Phase 5 — theming and base-template tests.

The plan's acceptance criteria are visual (fonts/colors/navbar match
caliBlur). These tests can't actually inspect rendered pixels, but
they can verify:

* layout.html and base.html render without error.
* The vendored CWN CSS is referenced from the rendered HTML.
* tracker.css is served by the app, contains the documented variables,
  and never overrides base caliBlur custom properties.
* The "My Reading" nav item is present.
* Component partials render against representative inputs.
* The cwa-override layout.html contains the tracker nav link.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from flask import render_template, render_template_string

from app.calibre.repository import BookDTO

REPO_ROOT = Path(__file__).resolve().parent.parent


# ── base.html / layout.html ─────────────────────────────────────────────────


def test_layout_renders_without_error(app_context, client):
    """Even an anonymous request can render base.html — the navbar
    primary block is unconditionally present, the user-only items are
    gated on current_user.is_authenticated."""
    html = render_template_string("{% extends 'base.html' %}{% block body %}OK{% endblock %}")
    assert "OK" in html
    assert "<!DOCTYPE html>" in html


def test_layout_references_vendored_caliblur_css(app_context):
    html = render_template_string("{% extends 'base.html' %}{% block body %}{% endblock %}")
    for href_fragment in (
        "css/cwa/style.css",
        "css/cwa/caliBlur.css",
        "css/cwa/caliBlur_override.css",
        "css/cwa/cwa.css",
    ):
        assert href_fragment in html, f"missing {href_fragment} in base.html output"


def test_layout_body_has_blur_class(app_context):
    html = render_template_string("{% extends 'base.html' %}{% block body %}{% endblock %}")
    assert 'class="page-tracker blur"' in html or "blur" in html


def test_base_includes_tracker_stylesheet(app_context):
    html = render_template_string("{% extends 'base.html' %}{% block body %}{% endblock %}")
    assert "css/tracker.css" in html


def test_base_renders_my_reading_nav(app_context):
    html = render_template_string("{% extends 'base.html' %}{% block body %}{% endblock %}")
    assert "My Reading" in html


# ── tracker.css ─────────────────────────────────────────────────────────────


@pytest.fixture()
def tracker_css() -> str:
    return (REPO_ROOT / "app" / "static" / "css" / "tracker.css").read_text()


def test_tracker_css_served_by_app(client):
    resp = client.get("/static/css/tracker.css")
    assert resp.status_code == 200
    assert b"--tracker-accent" in resp.data


def test_tracker_css_defines_all_documented_variables(tracker_css):
    expected = (
        "--tracker-accent",
        "--tracker-dnf",
        "--tracker-reading",
        "--tracker-read",
        "--tracker-want",
        "--tracker-star-filled",
        "--tracker-star-empty",
        "--tracker-progress-bg",
        "--tracker-progress-fill",
    )
    missing = [v for v in expected if v not in tracker_css]
    assert not missing, f"tracker.css missing variables: {missing}"


def test_tracker_css_only_extends_never_overrides(tracker_css):
    """No --bs-*, --cwa-* or other base caliBlur variables should be
    redefined inside :root in tracker.css — we extend, never override."""
    forbidden_prefixes = ("--bs-", "--cwa-", "--calibre-")
    for line in tracker_css.splitlines():
        stripped = line.strip()
        for prefix in forbidden_prefixes:
            if stripped.startswith(prefix):
                pytest.fail(f"tracker.css overrides base variable: {stripped}")


def test_tracker_css_declares_all_status_badges(tracker_css):
    for badge in (
        ".badge-want-to-read",
        ".badge-reading",
        ".badge-read",
        ".badge-dnf",
        ".badge-re-reading",
    ):
        assert badge in tracker_css, f"missing status badge class: {badge}"


# ── Components ──────────────────────────────────────────────────────────────


@pytest.fixture()
def sample_book() -> BookDTO:
    return BookDTO(
        id=42,
        title="Piranesi",
        sort="Piranesi",
        authors=("Susanna Clarke",),
        series=None,
        series_index=None,
        tags=("Fantasy",),
        isbn="9781635575637",
        pubdate="2020-09-15",
        path="Susanna Clarke/Piranesi (42)",
        has_cover=True,
        cover_path="/tmp/whatever.jpg",
    )


def test_book_card_renders(app_context, sample_book):
    html = render_template(
        "components/book_card.html",
        book=sample_book,
        status="read",
        rating=9,
    )
    assert "Piranesi" in html
    assert "Susanna Clarke" in html
    assert "badge-read" in html
    assert "/cover/42" in html
    # 5 stars rendered
    assert html.count("glyphicon-star") == 5


def test_book_card_cover_fallback_when_missing(app_context, sample_book):
    no_cover = BookDTO(**{**sample_book.__dict__, "has_cover": False, "cover_path": None})
    html = render_template("components/book_card.html", book=no_cover)
    assert "cover-fallback" in html
    assert "/cover/" not in html


def test_rating_stars_fills_correct_number(app_context):
    # rating=7 → 4 stars filled (1-2, 3-4, 5-6, 7-8), 5th empty.
    html = render_template("components/rating_stars.html", rating=7)
    assert html.count("star filled") == 4
    assert html.count("glyphicon-star") == 5


def test_rating_stars_no_fill_when_none(app_context):
    html = render_template("components/rating_stars.html", rating=None)
    assert "star filled" not in html


def test_rating_stars_aria_label_for_unrated(app_context):
    html = render_template("components/rating_stars.html", rating=None)
    assert "Not rated" in html


def test_rating_stars_aria_label_for_rated(app_context):
    html = render_template("components/rating_stars.html", rating=8)
    assert "4.0 out of 5 stars" in html


def test_progress_bar_clamps_to_range(app_context):
    over = render_template("components/progress_bar.html", percent=150)
    assert "width: 100%" in over
    under = render_template("components/progress_bar.html", percent=-20)
    assert "width: 0%" in under


def test_progress_bar_label(app_context):
    html = render_template("components/progress_bar.html", percent=50, label="2026 GOAL")
    assert "2026 GOAL" in html
    assert "width: 50%" in html


# ── CWN override file ───────────────────────────────────────────────────────


def test_cwa_override_layout_includes_tracker_link():
    override = (REPO_ROOT / "cwa-override" / "templates" / "layout.html").read_text()
    assert 'href="/tracker/"' in override
    assert "My Reading" in override


def test_cwa_override_readme_exists():
    readme = REPO_ROOT / "cwa-override" / "README.md"
    assert readme.exists(), "cwa-override/README.md is required for install instructions"
    body = readme.read_text()
    assert "/config/templates" in body
    assert "docker restart" in body
