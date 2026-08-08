"""
Action logs (issue #30) — a plain-text trail of what the app did, in `logs/`:

    logs/cards.log         cards created, skipped as duplicates, edited, deleted
    logs/dict.log          which translation / dictionary sites were used
    logs/parsed_files.log  .txt / .docx / .mht notes uploads

Each line is `<timestamp> ACTION key=value …`, so the logs stay greppable
(`grep "word='fount'" logs/*.log`). Values containing spaces are quoted.

Files rotate every 30 days (Python's timed handler has no calendar-month
unit, so a month is 30 days here) and 12 rotations are kept — a year of
history. The live file keeps its plain name; rotated ones gain a date suffix,
e.g. `cards.log.2026-08-26`.

Logging must never break a user action, so every helper swallows its own
errors: a read-only or full disk costs a log line, not a saved card.

The directory can be redirected with KF_LOGS_DIR (the test suite points it at
a temp directory so it never writes into a real checkout).
"""

import logging
import os
import time
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

LOGS_DIR = Path(os.environ.get("KF_LOGS_DIR")
                or Path(__file__).parent / "logs")
ROTATE_DAYS = 30
KEEP_ROTATIONS = 12

CARDS = "cards"
DICT = "dict"
PARSED_FILES = "parsed_files"

_configured = {}  # logger name -> the directory it is currently writing to


def _logger(name):
    """The rotating logger for one log file, configured on first use."""
    directory = Path(LOGS_DIR)
    logger = logging.getLogger(f"kuantorflow.{name}")
    if _configured.get(name) != str(directory):
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            handler.close()
        directory.mkdir(parents=True, exist_ok=True)
        handler = TimedRotatingFileHandler(
            directory / f"{name}.log", when="midnight", interval=ROTATE_DAYS,
            backupCount=KEEP_ROTATIONS, encoding="utf-8", delay=True,
        )
        handler.setFormatter(logging.Formatter(
            "%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        logger.propagate = False   # these lines belong in the file, not stderr
        _configured[name] = str(directory)
    return logger


def _value(value):
    text = str(value)
    if text == "" or any(ch.isspace() for ch in text):
        return "'" + text.replace("'", "\\'") + "'"
    return text


def _write(name, action, **fields):
    """Append one line; never raise — logging is not worth failing a request."""
    try:
        parts = [action] + [f"{key}={_value(value)}"
                            for key, value in fields.items()
                            if value is not None]
        _logger(name).info(" ".join(parts))
    except Exception:       # noqa: BLE001 - a broken log must stay harmless
        pass


def _user(user):
    """Identify the actor: the signed-in email, or 'anonymous' (issue #30)."""
    return user or "anonymous"


# --- cards.log --------------------------------------------------------------

def _card_fields(entry):
    return {
        "word": entry.get("word"),
        "pos": entry.get("pos"),
        "topic": entry.get("topic"),
    }


def topic_created(name, topic_id=None, created_by=None):
    """A new topic row appeared (issue #207).

    Written from utils, where the row is actually created, rather than from the
    route — the same reasoning as set_user_blocked(): there is one place a topic
    can come into existence, and putting the line there is what makes an
    unlogged one impossible.

    `created_by` is the user *id*, not the email the other card lines carry:
    this is called from below the request, where the session is not in reach.
    The CREATE line for the card that caused it follows immediately and names
    the user and the source, so the pair reads as one event.
    """
    # The field is `topic=`, matching the card lines, so one grep finds the
    # topic being created and every card later filed under it. It cannot be
    # `name=` either way: that is _write()'s own first argument.
    _write(CARDS, "TOPIC", topic=name, id=topic_id,
           created_by=(created_by if created_by is not None else "anonymous"))


def topic_placed(name, section, position, topic_id=None):
    """An existing topic was moved into a section (issue #203).

    Only the seed script does this, and only to a topic it did not create — a
    name somebody had already started filing cards under, which the curriculum
    then claims. That is the one thing the seed does to other people's data, so
    it does not get to happen without a line saying so.

    `topic=` and `id=`, matching topic_created() and the card lines, so one grep
    still finds a topic's whole history.
    """
    _write(CARDS, "TOPIC-PLACED", topic=name, id=topic_id,
           section=section, position=position)


def card_created(entry, source, user=None, card_id=None):
    """A new card reached the database."""
    _write(CARDS, "CREATE", **_card_fields(entry), id=card_id,
           langs=_languages(entry), source=source, user=_user(user))


def card_skipped(entry, source, user=None, reason="duplicate"):
    """A card was not written — same word + part of speech already exists
    (issue #101). Worth logging: it explains a card the user expected."""
    _write(CARDS, "SKIP", **_card_fields(entry), reason=reason,
           source=source, user=_user(user))


def card_edited(entry, source, user=None, card_id=None, changed=None):
    """An existing card's content was changed.

    Nothing calls this yet: the app can only edit a card *before* it is saved
    (in the review popup, which is logged as CREATE). It is here so that an
    edit feature logs from day one.
    """
    _write(CARDS, "EDIT", **_card_fields(entry), id=card_id,
           changed=",".join(changed) if changed else None,
           source=source, user=_user(user))


def card_edit_denied(card_id, topic=None, user=None, reason=None):
    """An edit refused by the ownership rule (#176) — the counterpart to
    DELETE-DENIED, and worth the same line for the same reason."""
    _write(CARDS, "EDIT-DENIED", id=card_id, topic=topic,
           reason=reason, user=_user(user))


def card_deleted(card_id, word, topic=None, user=None, source="topic page"):
    """A card was removed from the database."""
    _write(CARDS, "DELETE", id=card_id, word=word, topic=topic,
           source=source, user=_user(user))


def card_delete_missed(card_id, topic=None, user=None):
    """A delete that hit nothing — the card was already gone."""
    _write(CARDS, "DELETE-MISS", id=card_id, topic=topic, user=_user(user))


def account_deleted(user_id, cards=0, kept=True):
    """An account removed itself, or an admin removed it (#165).

    Recorded without the address, deliberately: the point of the operation is
    to remove the identifier, so writing it into a fresh log line would undo
    part of what the user just asked for. Existing lines are left alone —
    rewriting an append-only audit trail costs more than the identifier is
    worth, and log retention ages them out on its own.
    """
    _write(CARDS, "ACCOUNT-DELETE", id=user_id, cards=cards,
           choice="kept" if kept else "deleted")


def card_add_denied(entry, source, user=None, reason="anonymous"):
    """A save refused because the visitor has no account (#125).

    The counterpart to DELETE-DENIED: it separates "the card never arrived"
    from "the card was refused", which is otherwise guesswork when someone
    reports that a word they added is missing.
    """
    _write(CARDS, "ADD-DENIED", **_card_fields(entry), reason=reason,
           source=source, user=_user(user))


def preferred_name_set(user_id, name, user=None):
    """The learner told Mykola what to call them (ai_agent#62), or asked for
    their real name back (`name` is None). Recorded because it is written by
    the model on the user's behalf: if the wrong thing is stored, the log is
    where you find out what was asked for."""
    # `preferred=`, not `name=`: _write's own first parameter is the log file
    # name, so a field called `name` collides with it.
    _write(CARDS, "PREFERRED-NAME", id=user_id,
           preferred=name if name is not None else "(cleared)",
           user=_user(user))


def user_blocked(user_id, email, reason=None):
    """An account was blocked (#126). Recorded with the address, unlike an
    account deletion: the block is a decision someone may have to justify or
    reverse later, and 'which account?' is the first question asked."""
    _write(CARDS, "USER-BLOCK", id=user_id, email=email, reason=reason)


def user_unblocked(user_id, email):
    """A block was lifted (#126)."""
    _write(CARDS, "USER-UNBLOCK", id=user_id, email=email)


def mykola_denied(user=None):
    """A blocked account's chat request was refused (#126). Worth a line: the
    widget is not rendered for them, so this only happens for a request made
    by hand, or a page loaded before the block took effect."""
    _write(CARDS, "MYKOLA-DENIED", reason="blocked", user=_user(user))


def card_delete_denied(card_id, topic=None, user=None, reason=None):
    """A delete refused by the ownership rule (#162).

    Cheap to record and worth having: it shows whether the rule is getting in
    people's way, and a burst of them is the signature of someone posting
    delete requests by hand.
    """
    _write(CARDS, "DELETE-DENIED", id=card_id, topic=topic,
           reason=reason, user=_user(user))


def _languages(entry):
    """Which translation languages the card carries, for a sense of quality."""
    langs = [lang for lang in ("ukr", "rus") if entry.get(f"translation_{lang}")]
    return "+".join(langs) if langs else "none"


# --- dict.log ---------------------------------------------------------------

def lookup_started(word, translator, explanatory_dictionary, user=None):
    """Who looked up what, and which providers their settings selected. The
    lines below come from the parser, which has no request context — they are
    tied to this one by the word and the timestamp."""
    _write(DICT, "LOOKUP", word=word, translator=translator,
           dictionary=explanatory_dictionary, user=_user(user))


def translations_fetched(word, provider, lang, count, elapsed_ms,
                         fallback_from=None, error=None):
    """One translation site's answer: how many parts of speech came back, how
    long it took, and whether we are here because another provider failed."""
    _write(DICT, "TRANSLATE", word=word, provider=provider, lang=lang,
           pos_count=count, ms=elapsed_ms, fallback_from=fallback_from,
           error=error)


def definitions_fetched(word, provider, count, elapsed_ms,
                        fallback_from=None, error=None):
    """One explanatory dictionary's answer (Oxford / Merriam-Webster /
    Reverso). Merriam-Webster and Reverso are blocked from PythonAnywhere's
    IPs, so these lines are how a silent fallback becomes visible."""
    _write(DICT, "DEFINE", word=word, provider=provider, pos_count=count,
           ms=elapsed_ms, fallback_from=fallback_from, error=error)


def anonymous_limit_hit(kind, used, limit):
    """An anonymous visitor was refused a message (#164). `kind` is "session"
    (their own allowance) or "daily" (everyone's ceiling). Worth logging: it
    is the only way to see whether the numbers are set sensibly."""
    _write(DICT, "LIMIT", kind=kind, used=used, limit=limit)


def lookup_finished(word, cards, elapsed_ms):
    _write(DICT, "RESULT", word=word, cards=cards, ms=elapsed_ms)


def lookup_failed(word, error):
    _write(DICT, "FAILED", word=word, error=error)


# --- parsed_files.log -------------------------------------------------------

def file_parsed(filename, size_bytes, cards, topic=None, user=None,
                elapsed_ms=None):
    _write(PARSED_FILES, "PARSE", file=filename, bytes=size_bytes,
           cards=cards, topic=topic, ms=elapsed_ms, user=_user(user))


def file_rejected(filename, error, user=None):
    """An unsupported extension, or a file the parser could not read."""
    _write(PARSED_FILES, "REJECT", file=filename, error=error, user=_user(user))


def terms_split(lines, terms, model=None, error=None):
    """The AI split of Reverso's glued translation terms (#134) — the flakiest
    step of a file parse, and it fails silently by design."""
    _write(PARSED_FILES, "SPLIT", lines=lines, terms=terms, model=model,
           error=error)


class Timer:
    """Elapsed milliseconds for the log lines above: `with Timer() as t: …`.

    The clock stops when the block exits, so `t.ms` still reports the work
    itself when it is read a few statements later.
    """

    def __init__(self):
        self._start = time.monotonic()
        self._end = None

    def __enter__(self):
        self._start = time.monotonic()
        return self

    def __exit__(self, *exc):
        self._end = time.monotonic()
        return False

    @property
    def ms(self):
        end = self._end if self._end is not None else time.monotonic()
        return int((end - self._start) * 1000)
