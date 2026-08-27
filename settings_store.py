"""Application settings persisted as JSON files (issue #86).

One config file per identity, all inside the ``settings/`` directory:

    settings/config-default.json    every anonymous (not signed-in) visitor
    settings/config-<id>.json       one per signed-in user, <id> being their
                                    users-table id (issue #174)

The file records the owner's address under EMAIL_KEY so a directory listing
can still be read by a human; pre-#174 files named after the email prefix are
moved onto their id-keyed name the first time that user is seen.

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
    # issues #20/#21 — provider choices, dispatched in parsers.lookup_word().
    "translator": "claude",
    "explanatory_dictionary": "oxford",
    # issue #46 — hide a language everywhere (flashcards and Mykola's answers).
    "show_ukrainian": True,
    "show_russian": True,
    # issue #113 — the language the quiz opens in (the in-page switch still
    # lets the user take it in any visible language).
    "quiz_lang": "ukrainian",
    # issue #235 — how many cards a round of Fill the gap deals. A setting
    # rather than a literal because ten is a guess about attention, not a fact;
    # a selection with fewer playable cards than this deals what it has.
    "gapped_deck_size": 10,
    # issue #268 — how fast a word is spoken, as a **percentage** of the
    # browser's normal speed. A learner at B2 often wants a word slower, and
    # `SpeechSynthesisUtterance.rate` is free.
    #
    # A percentage rather than the natural 0.5–1.5 float, because `RANGES`
    # holds whole numbers and `sanitize()` validates everything in it through
    # `_whole_number()`. Storing 0.8 would need a second kind of validation for
    # one setting; storing 80 and dividing at the point of use needs none.
    "speech_rate": 100,
    # ai_agent#54 — hours of silence after which Mykola's chat is restarted
    # (his recap of the last exchanges opens the fresh one). 0 = never.
    "restart_chat_interval": 2,
    # ai_agent#50 — ask Mykola to deliberate less and answer shorter. Measured
    # over six short questions: 3.17s to the first word and 7.04s to the last
    # without it, 0.98s and 1.98s with.
    #
    # On by default, which is a judgement about what a chat widget is for: a
    # learner who types a question into a corner of the page is asking for an
    # answer, not an essay, and seven seconds of silence reads as broken long
    # before it reads as thorough. It remains a real trade — the replies are
    # much shorter — so anybody who wants the longer lesson turns it off, and
    # the popup says plainly what they would be turning off.
    "mykola_fast_thinking": True,
    # ai_agent#50 — type Mykola's answer out instead of showing each streamed
    # chunk the moment it lands. Presentation only: the words arrive at the
    # same time either way, and the animation never outlives the text it is
    # drawing, so nothing is ever delayed waiting for it. On by default
    # because the chunky version is what it replaces, and a reader who wants
    # the raw arrival can say so.
    "mykola_typewriter": True,
    # issue #363 — order the topics inside every section by name. On by
    # default, which is a judgement about what the browse page is for: it is
    # somewhere you look a topic up, and the stored order is only knowable by
    # having learnt it. What it overrides is real, though — `topics.position`
    # carries the curriculum #203 seeded, running from what a B2 learner meets
    # first towards what they meet last — so it is a switch rather than a new
    # ordering, and turning it off gives that back.
    "alphabetical_topics": True,
    # issue #127 — show only the cards this user added (#89 records who), and
    # hide everyone else's. Off by default: the deck is shared by design, and
    # a learner who has added nothing would otherwise open an empty site.
    # A view filter only — nothing about what is stored or who may change it.
    "individual_cards": False,
}

# Allowed values for the non-boolean settings (issues #20, #113).
CHOICES = {
    # #353: the sanctioned providers only. `google` and `bing` were scraped
    # endpoints and are gone from here on purpose -- dropping them means
    # `sanitize()` coerces any account still holding one to the default
    # rather than leaving it on a provider that cannot work (#352).
    "translator": ("claude", "microsoft", "deepl", "google_cloud"),
    "explanatory_dictionary": ("oxford", "merriam-webster"),
    "quiz_lang": ("ukrainian", "russian"),
}

# Whole-number settings and their inclusive bounds (ai_agent#54). The slider
# offers 1–24 hours; 0 is the separate "never restart" state its checkbox sets.
RANGES = {
    "restart_chat_interval": (0, 24),
    # Five is the shortest thing worth calling a round; fifty is where one
    # stops being finishable in a sitting (#235). sanitize() already validates
    # anything in here through _whole_number(), so this entry is the whole
    # validation.
    "gapped_deck_size": (5, 50),
    # #268. Half speed is slow enough to hear every syllable; half again is
    # about as fast as a browser voice stays intelligible. Outside that the
    # exercise stops being listening and starts being a trick.
    "speech_rate": (50, 150),
}

BOOLEAN_KEYS = tuple(k for k, v in DEFAULTS.items()
                     if isinstance(v, bool))


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


# The email is stored inside the file rather than in its name (issue #174,
# settled in #148 decision 1). A composite name like config-7-anton.json goes
# stale the moment the address changes, and turns every lookup into a glob —
# load() runs on every rendered page through inject_settings, so a per-render
# directory scan is a poor trade for a nicer `ls`. This key is written by
# save() and stripped by sanitize(), so it never reaches the app's settings.
EMAIL_KEY = "_email"


def safe_key(user_id=None) -> str:
    """File-name stem for this identity: the users row id, or 'default'.

    Keyed on the id (issue #174) because the email prefix was neither stable
    nor unique: an address change orphaned the file, and everything before the
    '@' collides — anton@gmail.com and anton@outlook.com shared one config.

    Anything that isn't already a whole number falls back to the shared
    default, so a junk value can't build a path outside SETTINGS_DIR.
    Deliberately strict rather than coercing with int(): int(7.5) is 7, which
    would silently hand one identity another's settings file.
    """
    if isinstance(user_id, bool) or user_id is None:
        return DEFAULT_USERNAME  # bool is an int subclass — reject it first
    if isinstance(user_id, int):
        return str(user_id)
    if isinstance(user_id, str) and re.fullmatch(r"\d+", user_id.strip()):
        return str(int(user_id))
    return DEFAULT_USERNAME


def config_path(user_id=None) -> Path:
    """Path of the config file backing this identity (may not exist yet)."""
    return SETTINGS_DIR / f"config-{safe_key(user_id)}.json"


def legacy_config_path(email: str | None = None) -> Path | None:
    """Where this identity's settings lived before #174, or None if nowhere.

    None for an address that yields no usable prefix — that visitor was on the
    shared default config, which is not anyone's to migrate.
    """
    username = safe_username(email)
    if username == DEFAULT_USERNAME:
        return None
    return SETTINGS_DIR / f"config-{username}.json"


def _stored_email(path: Path) -> str | None:
    """The EMAIL_KEY recorded in a config file, or None if absent/unreadable."""
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, json.JSONDecodeError, UnicodeDecodeError):
        return None
    value = raw.get(EMAIL_KEY) if isinstance(raw, dict) else None
    return value if isinstance(value, str) else None


def _migrate(user_id, email: str | None) -> bool:
    """Move a pre-#174 email-keyed config onto its id-keyed name, once.

    Done on read rather than by a maintenance script: a user who has not
    signed in since #148 has a settings file but no users row, so a script
    sweeping the table would skip exactly the files that need moving. Doing it
    here also means no migration window in which two files exist.
    """
    path = config_path(user_id)
    legacy = legacy_config_path(email)
    if legacy is None or not legacy.is_file() or legacy == path:
        return False
    if path.exists():
        # Something already holds this id's name. If it carries EMAIL_KEY it is
        # this user's own migrated file and the legacy one is a leftover; leave
        # both alone. If it doesn't, it is a pre-#174 file belonging to whoever
        # had the numeric email prefix (7@example.com → config-7.json), so move
        # it aside rather than letting this user inherit a stranger's settings.
        if _stored_email(path) is not None:
            return False
        try:
            os.replace(path, path.with_suffix(".json.orphaned"))
        except OSError:
            return False
    try:
        os.replace(legacy, path)
    except OSError:
        return False  # a failed move just means the defaults are used this once
    return True


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
        elif key in RANGES:
            number = _whole_number(value)
            low, high = RANGES[key]
            if number is not None and low <= number <= high:
                clean[key] = number
    return clean


def _whole_number(value):
    """`value` as an int, or None if it isn't a whole number (ai_agent#54).

    Booleans are rejected (True would sneak in as 1) and so are fractions:
    the slider only ever sends whole hours, and a config file that says
    something else falls back to the default like every other invalid value.
    """
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and re.fullmatch(r"-?\d+", value.strip()):
        return int(value)
    return None


def load(user_id=None, email: str | None = None) -> dict:
    """Settings for this identity, always complete and valid.

    Keyed on ``user_id`` (issue #174); ``email`` is only recorded inside the
    file and used to find a pre-#174 file to migrate.

    A missing file is created with the defaults on first read, so every
    identity that has visited the site has a real config file on disk
    (issue #86). Never raises: an unreadable file, invalid JSON, or a failed
    first write all yield the defaults, so settings can't break the page.
    """
    path = config_path(user_id)
    migrated = False
    if user_id is not None:
        # Attempted whenever a pre-#174 file exists for this address — not only
        # when the id-keyed name is free, because that name may itself be a
        # legacy file belonging to whoever had a numeric email prefix.
        migrated = _migrate(user_id, email)
    try:
        with open(path, encoding="utf-8") as fh:
            raw = json.load(fh)
    except FileNotFoundError:
        # First visit for this identity — materialise the file. A corrupt
        # file deliberately does NOT take this path: it may hold hand-edited
        # values worth fixing, so it is never silently overwritten.
        try:
            return save(DEFAULTS, user_id, email)
        except OSError:
            return dict(DEFAULTS)  # read-only disk etc. — defaults still work
    except (json.JSONDecodeError, OSError, UnicodeDecodeError):
        return dict(DEFAULTS)
    values = sanitize(raw)
    if migrated:
        # Stamp EMAIL_KEY straight away rather than waiting for the user's
        # next settings save — otherwise a just-migrated file is unreadable in
        # a directory listing, which is most of the point of migrating it.
        try:
            return save(values, user_id, email)
        except OSError:
            pass
    return values


def save(values: dict, user_id=None, email: str | None = None) -> dict:
    """Validate ``values``, write them atomically, and return what was stored.

    The write goes to a temp file in the same directory and is then moved into
    place, so readers only ever see a complete file.

    ``email`` is written into the file under EMAIL_KEY so a directory listing
    can be tied back to a person (#174). It refreshes on every save, so an
    address change corrects itself without renaming anything. It is not part
    of the returned settings — sanitize() drops it — so it never reaches a
    template or the /settings JSON response.
    """
    clean = sanitize(values)
    payload = dict(clean)
    if email:
        payload[EMAIL_KEY] = email
    path = config_path(user_id)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, sort_keys=True)
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


def update(changes: dict, user_id=None, email: str | None = None) -> dict:
    """Merge ``changes`` into the stored settings and persist the result."""
    current = load(user_id, email)
    current.update(changes or {})
    return save(current, user_id, email)
