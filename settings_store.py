"""Application settings persisted as JSON files (issue #86).

One config file per identity, all inside the ``settings/`` directory:

    settings/config-default.json    every anonymous (not signed-in) visitor
    settings/config-<username>.json one per Google-authorised user,
                                    <username> = the email part before the '@'

The store is deliberately independent of Flask: it takes an email (or None)
and does the rest, which keeps it importable and testable on its own. app.py
resolves the email from the session and calls in.

Design notes:

* Reads never raise. A missing, unreadable or corrupt file falls back to the
  defaults, because a broken settings file must not take the site down.
* Values are validated on the way in *and* on the way out, so a hand-edited
  file can't feed unexpected types or values into the app.
* Unknown keys are dropped; missing keys fall back to their default. That is
  what lets new settings be added here without migrating existing files.
* Writes are atomic (temp file + os.replace), so a crash mid-write can't
  leave a half-written file behind.
"""

import json
import os
import re
import tempfile
from pathlib import Path

# Overridable so tests (and PythonAnywhere, if it ever needs to) can point the
# store somewhere else without touching the code.
SETTINGS_DIR = Path(os.environ.get("SETTINGS_DIR", Path(__file__).parent / "settings"))

DEFAULT_USERNAME = "default"

# The settings this file is the source of truth for. Adding an entry here is
# all that's needed to introduce a new setting — existing config files pick up
# the default automatically on their next read.
DEFAULTS = {
    # issue #13 — add looked-up cards straight to the database, skipping the
    # review-before-save popup. Off by default: review stays the safe default.
    "cards_automatically": False,
    # issue #20 — provider choices. Stubs for now; the parsers still use
    # Google Translate until those tickets land.
    "translator": "google",
    "explanatory_dictionary": "oxford",
    # issue #46 — hide a language everywhere (flashcards and Mykola's answers).
    "show_ukrainian": True,
    "show_russian": True,
}

# Allowed values for the non-boolean settings (issue #20).
CHOICES = {
    "translator": ("google", "bing"),
    "explanatory_dictionary": ("oxford", "merriam-webster"),
}

BOOLEAN_KEYS = tuple(k for k, v in DEFAULTS.items() if isinstance(v, bool))


def safe_username(email: str | None) -> str:
    """Filesystem-safe file-name stem from the part of an email before the '@'.

    Mirrors the rule app.py already uses for per-user log directories. Anything
    outside [a-z0-9_.-] is replaced, so a crafted address can't escape
    SETTINGS_DIR ('../../etc/passwd' collapses to a harmless name), and an
    empty or unusable address falls back to the shared default config.
    """
    prefix = (email or "").split("@", 1)[0].strip().lower()
    safe = re.sub(r"[^a-z0-9_.-]", "_", prefix).strip("._")
    return safe[:64] or DEFAULT_USERNAME


def config_path(email: str | None = None) -> Path:
    """Path of the config file backing this identity (may not exist yet)."""
    return SETTINGS_DIR / f"config-{safe_username(email)}.json"


def sanitize(values: dict | None) -> dict:
    """Return a complete, valid settings dict built from ``values``.

    Unknown keys are dropped, missing keys take their default, and any value
    that isn't of the right type (or isn't an allowed choice) falls back to its
    default rather than propagating into the app.
    """
    clean = dict(DEFAULTS)
    if not isinstance(values, dict):
        return clean
    for key, default in DEFAULTS.items():
        if key not in values:
            continue
        value = values[key]
        if key in BOOLEAN_KEYS:
            if isinstance(value, bool):
                clean[key] = value
        elif key in CHOICES:
            if isinstance(value, str) and value.lower() in CHOICES[key]:
                clean[key] = value.lower()
    return clean


def load(email: str | None = None) -> dict:
    """Settings for this identity, always complete and valid.

    Never raises: a missing file, unreadable file or invalid JSON all yield the
    defaults, so settings can't break the page.
    """
    path = config_path(email)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (FileNotFoundError, json.JSONDecodeError, OSError, UnicodeDecodeError):
        return dict(DEFAULTS)
    return sanitize(raw)


def save(values: dict, email: str | None = None) -> dict:
    """Validate ``values``, write them atomically, and return what was stored.

    The write goes to a temp file in the same directory and is then moved into
    place, so readers only ever see a complete file.
    """
    clean = sanitize(values)
    path = config_path(email)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(clean, fh, indent=2, ensure_ascii=False, sort_keys=True)
            fh.write("\n")
        os.replace(tmp, path)  # atomic on the same filesystem
    except BaseException:
        # Never leave a stray temp file behind if the write failed.
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return clean


def update(changes: dict, email: str | None = None) -> dict:
    """Merge ``changes`` into the stored settings and persist the result."""
    current = load(email)
    current.update(changes or {})
    return save(current, email)
