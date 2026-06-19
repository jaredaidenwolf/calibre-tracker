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

The Shelves section is populated dynamically per request from the
logged-in user's own ``Shelf`` rows, matching the way CWN renders its
sidebar — a "+ Create a Shelf" affordance plus one item per shelf.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from flask import request, url_for
from flask_login import current_user

from .models import Shelf


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

    css_class: str | None = None
    """Optional CSS class on the wrapping ``<li>``. Used for caliBlur's
    ``.create-shelf`` affordance, which it paints differently from a
    normal nav item."""


@dataclass(frozen=True)
class SidebarSection:
    """A header + a list of items."""

    title: str
    items: tuple[SidebarItem, ...]


def _shelf_items() -> tuple[SidebarItem, ...]:
    """Build the Shelves section: one item per shelf + the "create" link.

    Mirrors CWN's sidebar layout (Calibre-Web vendors a ``shelf.create``
    affordance plus a flat list of shelves under the section header).
    Unauthenticated requests get an empty tuple — the section header is
    only rendered when there's something below it.
    """
    if not getattr(current_user, "is_authenticated", False):
        return ()
    shelves = (
        Shelf.query.filter_by(user_id=current_user.id)
        .order_by(Shelf.name.asc())
        .all()
    )
    items: list[SidebarItem] = [
        SidebarItem(
            label=shelf.name,
            glyph="glyphicon-list",
            endpoint="tracker.shelf_detail",
            endpoint_kwargs={"shelf_id": str(shelf.id)},
        )
        for shelf in shelves
    ]
    # "+ Create a Shelf" and "Reorder Shelves" sit last, styled small/
    # inline via caliBlur's ``.create-shelf`` class (defined in
    # caliBlur.css around line 1435). The "+" glyph for Create is
    # painted by the same caliBlur rule; the sidebar template skips the
    # inline glyphicon when ``css_class == 'create-shelf'`` so we don't
    # double up.
    items.append(
        SidebarItem(
            label="Create a Shelf",
            glyph="glyphicon-plus",
            endpoint="tracker.shelf_new",
            css_class="create-shelf",
        )
    )
    items.append(
        SidebarItem(
            label="Reorder Shelves",
            glyph="glyphicon-resize-vertical",
            endpoint="tracker.shelves_reorder",
            css_class="reorder-shelves",
        )
    )
    return tuple(items)


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
                    glyph="glyphicon-check",
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
                    endpoint="tracker.quotes_index",
                ),
            ),
        ),
        SidebarSection(
            title="Shelves",
            items=_shelf_items(),
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
