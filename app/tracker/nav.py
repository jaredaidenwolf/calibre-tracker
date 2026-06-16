"""Sidebar items for the tracker's caliBlur shell.

The sidebar's content lives here (not in a template) so that Phases 7-9
(Quotes, Shelves, Stats) can append items without touching layout
templates. Each item is a :class:`SidebarItem`, grouped under a
:class:`SidebarSection`. The template walks the sections and renders the
caliBlur ``#scnd-nav`` markup.

An item is rendered as "active" when ``request.endpoint`` matches
:attr:`SidebarItem.endpoint` *and* :attr:`SidebarItem.match_kwargs` (if
any) match against the current request's ``view_args`` + query string.
Items without an endpoint (placeholders for unbuilt phases) are inert.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import request, url_for


@dataclass(frozen=True)
class SidebarItem:
    """A single ``<li>`` under a sidebar section."""

    label: str
    glyph: str
    endpoint: str | None = None
    """Flask endpoint to ``url_for``. ``None`` renders as an inert
    placeholder (used for unbuilt phases)."""

    endpoint_kwargs: dict[str, str] = field(default_factory=dict)
    """Keyword args passed to ``url_for`` — e.g. ``{"status": "reading"}``
    for the per-status list view."""

    title: str | None = None
    """``title=`` attribute / tooltip. Used to label placeholders
    ("Coming in Phase 7")."""


@dataclass(frozen=True)
class SidebarSection:
    """A header + a list of items."""

    title: str
    items: tuple[SidebarItem, ...]


def sidebar_sections() -> tuple[SidebarSection, ...]:
    """Build the sidebar's section list.

    Returns a fresh tuple per call (cheap; no I/O) so future sections
    can be conditional on user state.
    """
    return (
        SidebarSection(
            title="My Reading",
            items=(
                SidebarItem(
                    label="Overview",
                    glyph="glyphicon-home",
                    endpoint="tracker.dashboard",
                ),
                SidebarItem(
                    label="All",
                    glyph="glyphicon-th-list",
                    endpoint="tracker.status_list",
                    endpoint_kwargs={"status": "all"},
                ),
                SidebarItem(
                    label="Currently reading",
                    glyph="glyphicon-book",
                    endpoint="tracker.status_list",
                    endpoint_kwargs={"status": "reading"},
                ),
                SidebarItem(
                    label="Want to read",
                    glyph="glyphicon-bookmark",
                    endpoint="tracker.status_list",
                    endpoint_kwargs={"status": "want_to_read"},
                ),
                SidebarItem(
                    label="Finished",
                    glyph="glyphicon-ok",
                    endpoint="tracker.status_list",
                    endpoint_kwargs={"status": "read"},
                ),
                SidebarItem(
                    label="Did not finish",
                    glyph="glyphicon-remove",
                    endpoint="tracker.status_list",
                    endpoint_kwargs={"status": "dnf"},
                ),
            ),
        ),
        SidebarSection(
            title="Quotes & Notes",
            items=(
                SidebarItem(
                    label="Quotes",
                    glyph="glyphicon-comment",
                    title="Coming in Phase 7",
                ),
            ),
        ),
        SidebarSection(
            title="Shelves",
            items=(
                SidebarItem(
                    label="Shelves",
                    glyph="glyphicon-list",
                    title="Coming in Phase 8",
                ),
            ),
        ),
        SidebarSection(
            title="Stats",
            items=(
                SidebarItem(
                    label="Reading stats",
                    glyph="glyphicon-stats",
                    title="Coming in Phase 9",
                ),
                SidebarItem(
                    label="Goals",
                    glyph="glyphicon-flag",
                    title="Coming in Phase 9",
                ),
            ),
        ),
    )


def resolve_item_url(item: SidebarItem) -> str | None:
    """Return the href for a sidebar item, or ``None`` if it's a placeholder."""
    if item.endpoint is None:
        return None
    return url_for(item.endpoint, **item.endpoint_kwargs)


def item_is_active(item: SidebarItem) -> bool:
    """True iff this item points at the URL the user is currently on.

    Matches on endpoint plus any URL kwargs — so ``/list/reading`` and
    ``/list/read`` register as different items.
    """
    if item.endpoint is None or request.endpoint != item.endpoint:
        return False
    if not item.endpoint_kwargs:
        return True
    return all(
        str(request.view_args.get(k, "")) == str(v)
        for k, v in item.endpoint_kwargs.items()
    )
