# CWA Template Override — "My Reading" Nav Link

This directory contains a modified copy of Calibre-Web NextGen's
`layout.html` that adds a "My Reading" link in CWN's navbar pointing
to the tracker. Installing it is optional; the tracker works fine
without it, but the override makes CWN aware of the tracker so users
can jump between the two apps from the existing CWN UI.

## How CWN template overrides work

CWN reads templates from `/config/templates/` *before* falling back to
its packaged `cps/templates/` directory. Dropping a file in there with
the same name replaces the shipped one, and the override survives
container upgrades because `/config/` is on a bind mount.

## Install (Unraid)

```bash
mkdir -p /mnt/user/appdata/calibre-web-nextgen/config/templates
cp templates/layout.html \
   /mnt/user/appdata/calibre-web-nextgen/config/templates/layout.html
docker restart calibre-web
```

## Install (local Docker)

Substitute your bind-mount target for `/config`:

```bash
mkdir -p /config/templates
cp templates/layout.html /config/templates/layout.html
docker restart calibre-web
```

## What changed

A single new `<li>` in the navbar, added immediately after the existing
"Advanced Search" item. The link points to `/tracker/` on the same host
(this assumes the same-domain subpath deployment from
`docs/03-auth-and-theming.md`). If your tracker lives elsewhere, edit
the `href` before copying.

```diff
       <li><a href="{{url_for('search.advanced_search')}}" id="advanced_search">...</a></li>
+      {# calibre-tracker override: link to the tracker app. ... #}
+      <li><a href="/tracker/" id="tracker_link">...</a></li>
```

## Keeping it in sync

The override is a full copy of CWN's layout.html captured from the
running container. When CWN ships a new version that changes
`layout.html`, the easiest path is:

```bash
# from a freshly-updated CWN container:
docker cp calibre-web:/app/calibre-web-automated/cps/templates/layout.html \
          templates/layout.html
# then re-apply the My Reading <li> in the navbar by hand
```

If your CWN install diverges enough that re-applying by hand is painful,
delete the override and rely on the tracker's own "Library" link in
the tracker navbar instead — the override is convenience, not
correctness.
