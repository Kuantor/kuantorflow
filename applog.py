"""
Action logs (issue #30) — a plain-text trail of what the app did, in `logs/`:

    logs/cards.log         what someone did: cards created, skipped, edited,
                           moved and deleted, and the account-level actions
                           beside them — topics appearing, blocks, settings
                           changes, an account removing itself
    logs/dict.log          which translation / dictionary sites were used
    logs/parsed_files.log  .txt / .docx / .mht notes uploads

`cards.log` is the **action** log rather than strictly a card log: it has carried
`USER-BLOCK`, `ACCOUNT-DELETE`, `PREFERRED-NAME` and `TOPIC` lines for a while,
and #161 adds `MOVE` and `SETTINGS`. One file is deliberate — the question these
answer is "what happened to this person's deck, and in what order", and that
reads badly split across three files by category.

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
MYKOLA = "mykola"

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


def topic_visibility_set(name, public, topic_id=None, user=None,
                         outcome="changed"):
    """A topic was made public or private (#382).

    Its own action rather than a TOPIC line, because this one changes **who can
    read the deck** rather than what is in it -- the question somebody will ask
    the log later is "when did that topic stop being visible, and who did it".

    Refusals are logged too, unlike most reads: `shared` and `taken` are the
    two answers a learner will report as "it did not work", and a line saying
    which is the difference between a bug report and a five-second answer.
    """
    _write(CARDS, "TOPIC-VISIBILITY", topic=name, id=topic_id,
           visibility="public" if public else "private",
           outcome=outcome, user=_user(user))


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


def card_created(entry, source, user=None, card_id=None, alongside=None):
    """A new card reached the database.

    `alongside` is the id of the card this one deliberately duplicates (#379):
    a learner was shown what they already had, answered "add it anyway", and
    #101 was lifted for that one press. Recorded because a second row for the
    same word and part of speech is otherwise indistinguishable, later, from
    the accident #101 exists to prevent.
    """
    _write(CARDS, "CREATE", **_card_fields(entry), id=card_id,
           langs=_languages(entry), alongside=alongside,
           source=source, user=_user(user))


def card_filled(entry, fields, source, user=None):
    """A duplicate was not written, but it *gained* something (#349).

    Its own action rather than an EDIT or a SKIP. A SKIP says nothing happened
    and this is not that; an EDIT says somebody changed a card and this is not
    that either -- nothing was overwritten, only gaps closed by a fresh lookup.
    `fields` is what actually changed, which is the whole interest of the line
    when asking later which cards still carry a translator outage.
    """
    _write(CARDS, "FILL", **_card_fields(entry), fields=",".join(fields),
           source=source, user=_user(user))


def card_skipped(entry, source, user=None, reason="duplicate"):
    """A card was not written — same word + part of speech already exists
    (issue #101). Worth logging: it explains a card the user expected."""
    _write(CARDS, "SKIP", **_card_fields(entry), reason=reason,
           source=source, user=_user(user))


def card_edited(entry, source, user=None, card_id=None, changed=None):
    """An existing card's content was changed (#176's editor).

    `changed` names the fields, not their values. Which field was touched is what
    answers "when did this card's explanation change?"; storing the old text as
    well would put a copy of the deck in the log, and the card itself is where
    the current value lives.
    """
    _write(CARDS, "EDIT", **_card_fields(entry), id=card_id,
           changed=",".join(changed) if changed else None,
           source=source, user=_user(user))


def card_moved(card_id, word, from_topic, to_topic, user=None):
    """A card changed topic (#177), with **both** ends of the move (#161).

    Its own action rather than an `EDIT changed=topic`, which is what this was
    until #161. Two reasons. A move is the one card change whose *previous* value
    matters — "which topic did this come out of" is the whole question, and an
    EDIT line could only ever name the destination, so `from_topic` was computed
    by the route and thrown away. And `grep MOVE` is how you find them; they were
    otherwise mixed in with every explanation someone retyped.

    `topic=` stays the destination, matching every other card line, so a grep for
    a topic still finds the cards that are in it now. `from=` is the addition,
    and it is absent rather than empty when the card had no topic at all — the
    same way `_write()` drops any None, and "no topic" is a real state (#207).

    The fields are ordered like the other card lines — word, id, then the topics —
    which is why `from` arrives as a `**` dict mid-call: it is a Python keyword
    and cannot be written as `from=`.
    """
    _write(CARDS, "MOVE", word=word, id=card_id, **{"from": from_topic},
           topic=to_topic, source="topic move", user=_user(user))


def settings_changed(requested, stored, user=None):
    """Someone changed their settings (#161).

    Unlogged until now, and the omission cost real diagnosis time: with
    `individual_cards` on (#127) a learner sees none of the shared deck and is
    told a word is "already in the database" that they cannot find (#186). That
    reads as a broken app, and nothing recorded the setting being switched on.

    Both sides are logged because they can differ. `settings_store.update()`
    drops unknown keys and silently replaces invalid values with the default, so
    "I set the translator to Bong" is a real support question whose answer is
    only visible by comparing what arrived with what stuck.

    Values, unlike a card edit's, *are* logged: they are booleans, provider names
    and a number, so there is no deck content and no personal data in them.
    """
    applied = {key: stored[key] for key in sorted(requested) if key in stored}
    # Asked for and not stored at all: a key the store does not know.
    unknown = sorted(key for key in requested if key not in stored)
    # Asked for and stored as something else: the store replaced an invalid value
    # with the default. Recorded as what was *sent*, since the stored value is
    # already in `set=` — without this the log could not answer "I chose Bong and
    # it went back to Google", which is the whole reason both sides are compared.
    rejected = {key: requested[key] for key in sorted(requested)
                if key in stored and requested[key] != stored[key]}
    _write(CARDS, "SETTINGS",
           set=",".join(f"{k}={v}" for k, v in applied.items()) or None,
           rejected=",".join(f"{k}={v}" for k, v in rejected.items()) or None,
           unknown=",".join(unknown) or None,
           user=_user(user))


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


def lookup_degraded(word, cards, dictionary):
    """A lookup that produced cards from the dictionary alone (#349).

    Its own line rather than a flag on RESULT: this is the shape of a
    translator outage, and it is the line you want to count when asking *how
    long was this broken, and how many cards carry the scar*. The individual
    `TRANSLATE` failures above it say which provider refused; this says what
    the learner ended up with.
    """
    _write(DICT, "DEGRADED", word=word, cards=cards, dictionary=dictionary)


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


def text_generated(model=None, supplied=0, used=0, length=None, elapsed_ms=None,
                   error=None):
    """A text was written from the learner's own words (#237).

    It calls a paid API, so it leaves a line — `terms_split()` above is the
    shape to copy, and for the same reason: a path that costs money and can fail
    quietly is exactly the one that has to say what model it used and what went
    wrong.

    `supplied` and `used` are the honest pair the page shows: how many words
    went into the prompt, and how many of them the text turned out to contain.
    A gap between them is information, not an error — see textgen.generate().

    In `dict.log` rather than `cards.log`: nothing is written to the deck here,
    and this is the log that already answers "what did the app go and ask
    somebody else for", which is where the lookups and the quota refusals live.
    """
    _write(DICT, "GENERATE", model=model, supplied=supplied, used=used,
           words=length, ms=elapsed_ms, error=error)


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


# --- the agent's own logging (ai_agent#71, #75) ---------------------------

# The loggers ai_agent writes to. It is a library, so it emits and does not
# decide where the lines land — that is the application's job, and this is the
# application. Named here rather than imported, so a missing or older ai_agent
# costs nothing.
AGENT_LOGGERS = ("mykola.usage", "mykola.tools")


def attach_agent_logs():
    """Give ai_agent's loggers somewhere to write: logs/mykola.log.

    Without this they inherit the root logger's WARNING and have no handler, so
    every line is discarded — which is how ai_agent#71's token figures and #75's
    tool-choice lines both shipped writing to nothing at all. An observability
    feature that is itself invisible is worse than none, because it is believed.

    Safe to call more than once: `_logger()` reconfigures rather than stacking
    handlers, and the level and propagation are set idempotently.
    """
    handler_owner = _logger(MYKOLA)
    for name in AGENT_LOGGERS:
        logger = logging.getLogger(name)
        logger.handlers = list(handler_owner.handlers)
        logger.setLevel(logging.INFO)
        logger.propagate = False    # the file, not the web server's stderr
