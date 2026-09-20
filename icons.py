"""Topic and activity icons: a convention plus a directory listing (#223).

Its own module (#418) rather than a passenger in `web.py`, because it is a
*feature* -- and `web.py` takes only what a feature module cannot supply for
itself. It cannot live in `cards.py` either, which is what makes it a module
at all: the topic tiles are cards' and the activity tiles are `rounds.py`'s,
and both reach it as a Jinja filter rather than by import.

The filters are registered here as a **side effect**, which is why importing
this module is the whole of using it. `cards.py` also calls `topic_icon()`
directly, for `/topics.json`.

`TOPIC_ICON_DIR` reads `app.static_folder` at import, so this module cannot be
imported before the Flask object exists -- it takes it from `web.py`, like
every other module here.

Both icon builders go through `web.static_url()` rather than `url_for` (#300),
because a tile's picture is a static asset like any other and a topic icon
replaced in place would otherwise keep serving the old one. Reached **through**
the module (#436), so one patch on `web.static_url` covers both.
"""

import re
from pathlib import Path

import web
from web import app


# --- topic icons (#223) -----------------------------------------------------
# A topic's picture is found by **name**, not stored against the row: the file
# is `static/img/topics/<slug of the name>.webp`. That keeps the whole feature
# to a convention plus a directory listing, with no column, no migration and
# nothing to keep in step with the topics table.
#
# Deliberately *not* under a per-section folder. A topic can be moved between
# sections (#215) and renamed as a thing rather than a string (#178), so a path
# that encoded the section it happens to sit in today would go stale the first
# time either happened.
#
# When a topic owns an uploaded image of its own (#185) this becomes the
# fallback rather than the only answer, and the template does not change.

TOPIC_ICON_DIR = Path(app.static_folder) / "img" / "topics"
TOPIC_ICON_SUFFIX = ".webp"


def topic_slug(name):
    """The filename stem a topic's icon would have. '' for a nameless topic."""
    return re.sub(r"[^a-z0-9]+", "_", (name or "").lower()).strip("_")


GAME_ICON_DIR = Path(app.static_folder) / "img" / "games"


def _icon_slugs(directory):
    """Which slugs have a file in `directory`, listed once per process.

    The cache is the reusable half of #223's `topic_icon()`; what was
    topic-specific is slugging a name somebody typed. A game slug is fixed and
    known at write time, so it needs no slugging — but it wants the same cheap
    lookup, so the listing is keyed by directory rather than copied (#253).
    """
    key = str(directory)
    if key not in _icon_slugs.cache:
        try:
            _icon_slugs.cache[key] = {
                path.stem for path in directory.glob("*" + TOPIC_ICON_SUFFIX)}
        except OSError:
            # Not an error: it means nobody has added icons to this checkout.
            _icon_slugs.cache[key] = set()
    return _icon_slugs.cache[key]


_icon_slugs.cache = {}


def game_icon(slug):
    """The static URL of an activity's icon, or None.

    Unlike topics, the set of activities is closed and known, so every one
    ships with an icon and the None case is a safety net rather than, as it is
    for topics, the normal case.
    """
    if slug and slug in _icon_slugs(GAME_ICON_DIR):
        return web.static_url(f"img/games/{slug}{TOPIC_ICON_SUFFIX}")
    return None


def _topic_icon_slugs():
    """Which slugs actually have a file, listed once per process.

    Cached because icons ship with the code: they change on deploy, and a
    deploy reloads. The cost of getting that wrong is small and one-directional
    — a file added while the app is running is not seen until a reload, which
    is exactly when static assets appear anyway.

    A missing directory is not an error. It means nobody has added icons to
    this checkout, and every tile falls back to the plain colour.
    """
    return _icon_slugs(TOPIC_ICON_DIR)


def topic_icon(name):
    """The static URL of this topic's icon, or **None** when it has none.

    None rather than a placeholder path, so the caller decides: the tile keeps
    its plain background instead of rendering a broken image. Most topics have
    no icon — everything in `Other`, and anything a learner invents by looking a
    word up — so the no-icon case is the common one, not the exception.
    """
    slug = topic_slug(name)
    if slug and slug in _topic_icon_slugs():
        return web.static_url(f"img/topics/{slug}{TOPIC_ICON_SUFFIX}")
    return None


# A filter, so a template asks for it per topic rather than every route having
# to thread a parallel structure through render_template().
app.jinja_env.filters["topic_icon"] = topic_icon
app.jinja_env.filters["game_icon"] = game_icon
