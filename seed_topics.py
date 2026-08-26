r"""Build the starting deck from a checked-in word list (issue #203).

A new site opens on an empty deck: nothing to drill, nothing for the quiz,
nothing for Mykola to recap, and the first thing a learner is asked to do is the
slowest thing the app does — look words up one at a time. This turns
`seed_words.py` into real cards through the app's own lookup and save path.

    venv/Scripts/python seed_topics.py --check        # just check the word list
    venv/Scripts/python seed_topics.py --dry-run      # say what it would add
    venv/Scripts/python seed_topics.py                # add it
    venv/Scripts/python seed_topics.py --topic "Crime and justice"

Shaped like `apply_schema.py`, and for the same reasons — that shape is what a
PythonAnywhere console run needs:

* **Idempotent, cheaply.** `save_flashcard()` already refuses a duplicate
  word + part of speech (#101), so a re-run adds only what is missing and says
  it had nothing to do.
* **Resumable.** 360 lookups over a network *will* be interrupted. Because of
  the point above, the fix for a dead run is to run it again — and because
  `seed_words.py` is ordered by usefulness, an interrupted run leaves the useful
  half.
* **One line per word, flushed.** Read in a console while it happens and in a
  log afterwards; a block-buffered stdout puts the lines in the wrong order
  relative to the error that stopped them.
* **A failed lookup is a skipped word, not a dead run.** `lookup_word()` raises
  `ValueError` when nothing comes back, and Reverso and Merriam-Webster are
  blocked from PythonAnywhere's IPs. Losing word 12 of 360 must not cost the
  other 348, so failures are collected and named at the end.
* **Polite.** A pause between lookups and no parallel fan-out at the
  dictionaries. This is not a ten-second script.
* **ASCII output.** Same rule as `apply_schema.py`: a Windows console is
  cp1252, and printing a Ukrainian translation there raises
  `UnicodeEncodeError` — which would turn a working save into a crashed run.
  Words and topic names are ASCII; translations are never printed.

**Two passes, and the order matters.** The topics are created *first*, as a
curriculum — eighteen rows in `B2-C1 Conversational Topics`, numbered from 1,
which #215 built that section empty to receive. Only then are they filled. Doing
it the other way round would file every one of them under 'Other' at position 0,
because that is what `save_flashcard()` does with a topic name it has not seen
(and rightly, for a topic a learner invents by looking a word up).

**Not a request.** `app._save_and_log()` is unavailable here: it reads the
session and `g` through `add_refusal()` and `_current_email()`. So this is a
second non-request writer and it follows `set_user_blocked()`'s example — log
beside the write. CLAUDE.md's rule is that a new save path logs; being outside a
request is not an exemption, and `cards.log` should be able to answer "where did
these 400 cards come from?".
"""

import argparse
import sys
import time

import applog
import settings_store
import seed_words
from parsers import _fetch_oxford_definitions, lookup_word
from utils import get_db_connection, place_topic, save_flashcard

# The section #215 created empty for this. Not a flag: putting the curriculum
# somewhere else is not a thing a run of this script should be able to do
# quietly, and #178 is where moving topics between sections belongs.
SECTION = "B2–C1 Conversational Topics"

# The providers. Passed explicitly rather than read from anyone's
# settings/config-*.json: the script is not a user, and a deck whose contents
# depend on whose settings file was lying around is not reproducible. These are
# settings_store.DEFAULTS' values, overridable by flag.
# Follows the settings default (#353). Was "google" until the scraped
# backends were retired; a pinned literal here would have the seed
# quietly using a provider the app no longer offers.
DEFAULT_TRANSLATOR = settings_store.DEFAULTS["translator"]
DEFAULT_DICTIONARY = "oxford"

# Seconds between lookups. Each word already makes several requests (two
# languages plus a dictionary), so this is on top of a naturally slow loop.
PAUSE = 1.0

SOURCE = "seed script"


class Counts:
    """What the run did, for the summary line."""

    def __init__(self):
        self.added = 0
        self.present = 0        # already in the database (#101)
        self.failed = []       # (word, reason) - named at the end

    @property
    def looked_up(self):
        return self.added + self.present + len(self.failed)


def oxford_misses(words, pause=0.3, out=None):
    """The words Oxford has no entry for, as `[(word, reason)]` (#221).

    Asked of the dictionary directly rather than through `lookup_word()`: one
    request per word instead of three, and no translation noise in the answer.

    Worth having as a command rather than a one-off script, because the rule it
    checks is easy to break by accident. Oxford is the **only** explanatory
    dictionary reachable from PythonAnywhere, so a word it does not have reaches
    production with translations and no explanation — and locally you would never
    notice, since Reverso covers the gap from a developer's machine. #221 was
    exactly that failure, hidden for months.
    """
    out = out if out is not None else sys.stdout
    misses = []
    for index, word in enumerate(words, 1):
        try:
            definitions = _fetch_oxford_definitions(word)
        except Exception as exc:      # noqa: BLE001 - reported, never fatal
            definitions, reason = {}, f"{type(exc).__name__}: {exc}"
        else:
            reason = "no entry"
        if not definitions:
            misses.append((word, reason))
        print(f"  {index:>3}/{len(words)} {'MISS' if not definitions else 'ok  '} "
              f"{word}", file=out, flush=True)
        time.sleep(pause)
    return misses


def owner_id_for(email):
    """The user id for `--owner`, or None when the email matches no account.

    Matched exactly and case-insensitively, like `set_user_blocked()` — never a
    prefix, which could attribute 400 cards to a bystander.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute("SELECT id FROM users WHERE LOWER(email) = LOWER(%s)",
                       ((email or "").strip(),))
        row = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    return row[0] if row else None


def chosen_topics(only):
    """`{topic: words}` for this run — everything, or the one `--topic` asked
    for. Matched case-insensitively, since it arrives from a console."""
    if only is None:
        return dict(seed_words.SEED_WORDS)
    for topic, words in seed_words.SEED_WORDS.items():
        if topic.lower() == only.strip().lower():
            return {topic: words}
    raise LookupError(
        f"no seeded topic called {only!r}. Known: "
        + ", ".join(repr(t) for t in seed_words.SEED_WORDS))


def current_placement(topics):
    """Where each topic sits today: `{topic: (section, position)}`, absent when
    the topic does not exist yet.

    Read-only, and only for `--dry-run`. Worth the query: the one thing this
    script does to data somebody else made is **move** a topic they had already
    started, and "18 would be created" versus "3 of yours would be moved out of
    Other" is the difference between a dry run you can act on and a list of
    names you already knew.
    """
    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT t.name, s.name, t.position FROM topics t "
            "LEFT JOIN topic_sections s ON t.section_id = s.id")
        rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()
    # Matched case-insensitively, because that is what the UNIQUE key does: a
    # 'work and careers' in the database *is* 'Work and careers' here.
    found = {name.lower(): (section, position) for name, section, position in rows}
    return {topic: found[topic.lower()]
            for topic in topics if topic.lower() in found}


def place_topics(topics, owner_id, dry_run, out):
    """Pass one: the curriculum, before any of its cards exist.

    Positions come from `seed_words.SEED_WORDS`, not from `topics` — a
    `--topic` run must place its one topic at the number it has in the full
    curriculum, or a partial run would renumber the section.
    """
    numbers = {topic: index
               for index, topic in enumerate(seed_words.SEED_WORDS, start=1)}
    existing = current_placement(topics) if dry_run else {}
    for topic in topics:
        position = numbers[topic]
        if dry_run:
            where = existing.get(topic)
            if where is None:
                note = "would be created"
            elif where == (SECTION, position):
                note = "already in place"
            else:
                # Named, so a topic about to be taken out of Other is visible
                # before it happens rather than in the log afterwards.
                note = f"would MOVE from {where[0]!r} position {where[1]}"
            mark = "=" if note == "already in place" else "~"
            print(f"  {mark} {position:>2}. {topic:<34} {note}",
                  file=out, flush=True)
            continue
        outcome, _ = place_topic(topic, SECTION, position,
                                 created_by_user_id=owner_id)
        mark = {"created": "+", "moved": ">", "unchanged": "="}[outcome]
        print(f"  {mark} {position:>2}. {topic:<34} {outcome}",
              file=out, flush=True)


def seed_word(word, topic, owner_id, owner_email, translator, dictionary,
              counts, out):
    """Look one word up and save its cards. Never raises."""
    try:
        cards = lookup_word(word, topic=topic, translator=translator,
                            explanatory_dictionary=dictionary)
    except Exception as exc:      # noqa: BLE001 - one word must not end the run
        counts.failed.append((word, f"{type(exc).__name__}: {exc}"))
        print(f"    ! {word:<16} lookup failed: {type(exc).__name__}",
              file=out, flush=True)
        return

    added = present = 0
    for entry in cards:
        try:
            card_id = save_flashcard(entry, added_by_user_id=owner_id)
        except Exception as exc:  # noqa: BLE001 - a dead row, not a dead run
            counts.failed.append((word, f"save failed: {type(exc).__name__}: {exc}"))
            print(f"    ! {word:<16} save failed: {type(exc).__name__}",
                  file=out, flush=True)
            return
        # Logged beside the write, both ways: a skip explains a card somebody
        # expected to appear, which is the whole reason #101 logs one.
        if card_id is None:
            applog.card_skipped(entry, SOURCE, user=owner_email)
            present += 1
        else:
            applog.card_created(entry, SOURCE, user=owner_email, card_id=card_id)
            added += 1

    counts.added += added
    # A word counts as "already present" only when *none* of its cards were
    # written. Counting per card would make the totals disagree with the
    # per-word lines above, which is what someone reads to see progress.
    if added:
        marks = f"+{added}" + (f" ={present}" if present else "")
    else:
        marks = f"={present}"
        counts.present += 1
    poses = ",".join(c.get("pos") or "-" for c in cards)
    print(f"    {marks:<8} {word:<16} {poses}", file=out, flush=True)


def run(topics, owner_id, owner_email, translator, dictionary,
        dry_run=False, pause=PAUSE, out=None):
    """Place the topics, then fill them. Returns Counts."""
    out = out if out is not None else sys.stdout
    counts = Counts()

    print("topics", file=out, flush=True)
    place_topics(topics, owner_id, dry_run, out)

    print("words", file=out, flush=True)
    for topic, words in topics.items():
        print(f"  {topic}", file=out, flush=True)
        for word in words:
            if dry_run:
                # No lookup: a dry run must not spend 360 network requests to
                # tell you what it would spend them on.
                print(f"    ~        {word}", file=out, flush=True)
                continue
            seed_word(word, topic, owner_id, owner_email, translator,
                      dictionary, counts, out)
            time.sleep(pause)
    return counts


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Seed the deck with B2-C1 conversational vocabulary.",
        epilog="Safe to re-run and safe to interrupt: a re-run adds only what "
               "is missing.",
    )
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would be added, look nothing up")
    parser.add_argument("--check", action="store_true",
                        help="validate seed_words.py and exit; touches nothing")
    parser.add_argument("--check-oxford", action="store_true",
                        help="ask Oxford about every word and name the ones it "
                             "cannot define; writes nothing to the database")
    parser.add_argument("--topic", metavar="NAME",
                        help="seed one topic instead of all of them")
    parser.add_argument("--owner", metavar="EMAIL",
                        help="attribute the cards and topics to this account "
                             "(default: nobody, so they are community cards "
                             "that an 'only my cards' learner will not see)")
    parser.add_argument("--translator", default=DEFAULT_TRANSLATOR,
                        help=f"default: {DEFAULT_TRANSLATOR}")
    parser.add_argument("--dictionary", default=DEFAULT_DICTIONARY,
                        help=f"default: {DEFAULT_DICTIONARY}")
    parser.add_argument("--pause", type=float, default=PAUSE, metavar="SECONDS",
                        help=f"between lookups (default: {PAUSE})")
    args = parser.parse_args(argv)

    # Belt as well as braces for the ASCII rule in the module docstring. The
    # output is ASCII by design, but a *value* that is not — a topic name read
    # back from the database, a provider's error text quoted into a message —
    # would otherwise raise UnicodeEncodeError on a cp1252 console and end a run
    # that was saving cards perfectly well. Degrading one character to '?' is
    # always the better trade here.
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(errors="replace")
        except (AttributeError, OSError):
            pass    # already wrapped, or not a real stream (the test suite)

    problems = seed_words.problems()
    if problems:
        # Checked before anything else, always: a list with a topic of nineteen
        # words would otherwise be found out 300 lookups in.
        print("seed_words.py has problems:", file=sys.stderr)
        for line in problems:
            print(f"  - {line}", file=sys.stderr)
        return 1
    if args.check:
        total = sum(len(w) for w in seed_words.SEED_WORDS.values())
        print(f"seed_words.py is fine - {len(seed_words.SEED_WORDS)} topics, "
              f"{total} words, no duplicates")
        return 0

    try:
        topics = chosen_topics(args.topic)
    except LookupError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    if args.check_oxford:
        # After --topic is resolved, so one topic can be re-checked on its own
        # rather than paying 360 requests to look at twenty words.
        words = [w for ws in topics.values() for w in ws]
        print(f"asking Oxford about {len(words)} word(s)")
        misses = oxford_misses(words, pause=args.pause)
        if not misses:
            print(f"\nall {len(words)} word(s) have an Oxford definition")
            return 0
        print(f"\n{len(misses)} word(s) Oxford cannot define:")
        for word, reason in misses:
            print(f"  - {word}: {reason}")
        # Non-zero: on PythonAnywhere these words would be saved with
        # translations and no English explanation, which is a content bug worth
        # failing a check over.
        print("Oxford is the only dictionary reachable from PythonAnywhere, so "
              "these would be saved with no explanation.", file=sys.stderr)
        return 1

    owner_id = None
    if args.owner:
        owner_id = owner_id_for(args.owner)
        if owner_id is None:
            # Refused rather than falling back to nobody: someone who passed
            # --owner wants the cards attributed, and silently not doing it
            # would be found out only by a learner seeing an empty deck.
            print(f"error: no account with email {args.owner!r}", file=sys.stderr)
            return 1

    words = sum(len(w) for w in topics.values())
    print(f"{len(topics)} topic(s), {words} word(s), "
          f"{args.translator} + {args.dictionary}"
          + (f", owner {args.owner}" if args.owner else ", unowned"))

    try:
        counts = run(topics, owner_id, args.owner, args.translator,
                     args.dictionary, dry_run=args.dry_run, pause=args.pause)
    except LookupError as exc:
        # The section is missing, so apply_schema.py has not run. Reported as
        # ASCII rather than letting the exception's en dash reach the console.
        print(f"error: {exc}".encode("ascii", "replace").decode(),
              file=sys.stderr)
        print("Run 'python apply_schema.py' first - the curriculum section is "
              "a migration step (#215).", file=sys.stderr)
        return 1

    if args.dry_run:
        print(f"\n{words} word(s) would be looked up - nothing was changed")
        return 0

    print(f"\n{counts.added} card(s) added, {counts.present} word(s) already "
          f"present, {len(counts.failed)} failed")
    if counts.failed:
        # Named, not counted: these are the words to look at by hand, and a
        # number does not tell you which.
        print("could not be built:")
        for word, reason in counts.failed:
            print(f"  - {word}: {reason}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
