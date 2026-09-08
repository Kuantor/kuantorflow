r"""Consolidate the topics in `Other` (issue #407).

    venv/Scripts/python retopic.py --plan local --dry-run
    venv/Scripts/python retopic.py --plan local
    venv/bin/python retopic.py --plan pa --dry-run      # on PythonAnywhere

`Other` is where `_get_or_create_topic()` files anything a learner invents
(#215), so it fills up by design and nothing ever tidies it: names typed in
lower case, one-card experiments, and two or three that are the same subject
under different words while the curriculum already has a topic for them. This
merges the strays into the eighteen seeded topics where one fits, and renames
the survivors.

**Two plans, because the two databases drifted apart** and neither is a subset
of the other -- the deployed deck is more complete than the local one, which is
deliberate. `--plan` picks; the steps are declared below and nowhere else.

Shaped like `claim_topics.py`, `apply_schema.py` and `seed_topics.py`, because
that shape is what a PythonAnywhere console run needs:

* **`--dry-run` first, and it is a rehearsal rather than a prediction.** It
  applies every step for real and then **rolls back**, so what it prints is what
  the database actually did rather than what this script guessed it would do.
  That matters here because the steps are not independent: the local plan's
  first step *creates* the topic the next two merge into, so a dry run that
  predicted each step against the untouched database reported two of them as
  impossible. Both tables are InnoDB and the connection does not autocommit, so
  the rollback is the whole of it -- and `applog` is skipped while rehearsing,
  because a line in `cards.log` is the one thing a rollback cannot take back.
* **Idempotent.** A rename whose source is gone and whose destination is already
  there is done; a merge whose source is gone is done. A second run says there
  is nothing to do.
* **Resumable**, which falls out of that: the fix for an interrupted run is to
  run it again.
* **ASCII output.** A Windows console is cp1252 and a topic name is not
  guaranteed to be, so names are printed with non-ASCII characters escaped
  rather than risking a `UnicodeEncodeError` that would stop a script which was
  writing perfectly well.
* **Not a request.** `app._save_and_log()` reads the session and `g`, neither of
  which exists here, so the write logs beside itself -- the rule `place_topic()`,
  `set_user_blocked()` and `claim_topics.py` already follow. One line per step,
  not per card, for the reason `CARDS-CLAIMED` settled.

**Two columns move together.** `flashcards.topic_id` points at the row and
`flashcards.topic` still holds the canonical name beside it (#207). That string
is what ai_agent's `cards_db` reads today, so updating only the id leaves Mykola
naming topics that no longer exist, and updating only the string leaves every
page unchanged. Every write here does both, in one statement.

**A merge is not `save_flashcard()`, so #101 is not protecting it.** A bulk
UPDATE can put the same word and part of speech in a topic twice. Collisions are
found before anything is written and the step is **skipped**, not forced --
`--allow-duplicates` exists for when that is genuinely wanted, and prints what
it is doing.

**An emptied source row is deleted here**, which departs from the usual rule
that an empty topic row is kept because it holds the name, creator and age.
That is right for a topic whose last card was deleted and wrong for one being
consolidated away: leaving `math` behind keeps the name in
`uq_topics_namespace`, so the next card saved under it resurrects the row this
script exists to remove. `fk_flashcards_topic` is ON DELETE RESTRICT (#165), so
the delete can only follow an emptied source -- a source that still holds cards
is reported and left standing.

**Visibility is never changed** (#382). A card moving into a topic takes that
topic's visibility, which is the point of the move; but no `is_public` and no
`namespace` is written here, so `set_topic_visibility()`'s two refusals cannot
fire and no topic changes hands.

Not part of a deploy. A one-off, run once per database, safe to re-run and safe
never to run again.
"""

import argparse
import sys

import applog
from utils import get_db_connection


class Rename:
    """`old` becomes `new`. The row survives; its name and its cards' strings
    change."""

    def __init__(self, old, new):
        self.old, self.new = old, new


class Merge:
    """Every card in `source` moves to `dest`, and the emptied `source` row
    goes."""

    def __init__(self, source, dest):
        self.source, self.dest = source, dest


# The plans. Order matters in one place and it is worth naming: a Rename that
# *creates* a destination has to run before the Merges that fill it, because a
# merge into a topic that does not exist is an error rather than a creation --
# this script never invents a topic, so that a typo in a plan fails loudly
# instead of quietly filing cards somewhere new.
PLANS = {
    # The local deck. "Psychology, feelings and emotions" does not exist, so it
    # is made by renaming the largest of its three contributors rather than
    # creating a fourteenth row and emptying all three.
    "local": [
        Rename("emotions", "Psychology, feelings and emotions"),
        Merge("psychology", "Psychology, feelings and emotions"),
        Merge("dream", "Psychology, feelings and emotions"),

        Rename("character and personality", "Character and personality"),
        Merge("luck and chance", "Character and personality"),
        Merge("vocabulary", "Character and personality"),

        Merge("Education", "Education and study"),
        Merge("IT", "Technology and the internet"),
        Merge("math", "Science and research"),
        Merge("science", "Science and research"),

        # Into `general` while it still has that name, then renamed once -- the
        # end state is the same either way, and this order keeps the plan
        # reading like the request that produced it.
        Merge("Runaway Bride", "general"),
        Merge("test", "general"),
        Rename("general", "General knowledge"),
    ],
    # The deployed deck. `Media and the news` is the seeded spelling; the
    # request wrote "Media and the News" and the column collates
    # case-insensitively, so both resolve to the same row -- but the row keeps
    # the name it has, and matching the other seventeen is worth more than
    # matching the request's capitals.
    "pa": [
        Rename("emotions", "Psychology, feelings and emotions"),
        Merge("psychology", "Psychology, feelings and emotions"),
        Merge("Runaway Bride", "Psychology, feelings and emotions"),

        Rename("fantasy", "Fantasy world"),
        Rename("general", "General knowledge"),

        Merge("medicine", "Health and medicine"),
        Merge("news", "Media and the news"),
    ],
}


def ascii_safe(text):
    """A topic name a cp1252 console can print (`claim_topics.py`'s rule)."""
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def find_topic(cursor, name):
    """The one topic called `name`, or None.

    Case-insensitive because `uq_topics_namespace` is: the column collates
    `utf8mb4_unicode_ci`, so `general` and `General` were never two topics and a
    plan written in either case must find the same row.

    Raises when a name is ambiguous. Since #382 a name is unique only within a
    namespace, so one public `Work` and one learner's private `Work` can both
    exist -- and a bulk move that picked one of them by luck is exactly the kind
    of thing this script must not do.
    """
    cursor.execute(
        "SELECT id, name, is_public, namespace, "
        "       (SELECT COUNT(*) FROM flashcards f WHERE f.topic_id = t.id) "
        "  FROM topics t WHERE LOWER(name) = LOWER(%s)", (name,))
    rows = cursor.fetchall()
    if len(rows) > 1:
        raise SystemExit(
            "ambiguous topic name %r -- %d rows in different namespaces (#382). "
            "Resolve by hand; this script will not guess."
            % (ascii_safe(name), len(rows)))
    if not rows:
        return None
    row = rows[0]
    return {"id": row[0], "name": row[1], "is_public": row[2],
            "namespace": row[3], "cards": row[4]}


def collisions(cursor, source_id, dest_id):
    """Words that exist in both topics, by word + part of speech.

    #101's rule, asked of the bulk path that does not go through
    `save_flashcard()` and therefore does not get it for free.
    """
    cursor.execute(
        "SELECT s.word, s.pos FROM flashcards s JOIN flashcards d"
        "  ON LOWER(s.word) = LOWER(d.word)"
        " AND LOWER(COALESCE(s.pos, '')) = LOWER(COALESCE(d.pos, ''))"
        " WHERE s.topic_id = %s AND d.topic_id = %s", (source_id, dest_id))
    return cursor.fetchall()


def do_rename(cursor, step, dry_run, out):
    source = find_topic(cursor, step.old)
    dest = find_topic(cursor, step.new)

    # Exactly, not case-insensitively. `find_topic()` matches the way the
    # unique key collates, so after `character and personality` becomes
    # `Character and personality` the old name still resolves to that same row
    # -- and every later run would rename it onto itself, reporting a change
    # and writing a log line for a step that finished the first time. A
    # case-only rename is the only shape that shows this, and this plan has one.
    if source is not None and source["name"] == step.new:
        out("= %-34s already named %s" % (ascii_safe(step.old),
                                          ascii_safe(step.new)))
        return 0

    if source is None:
        if dest is not None:
            out("= %-34s already named %s" % (ascii_safe(step.old),
                                              ascii_safe(step.new)))
            return 0
        out("! %-34s no such topic, and no %s either -- skipped"
            % (ascii_safe(step.old), ascii_safe(step.new)))
        return 0

    # A rename onto a name another row already holds would merge two topics by
    # accident, or fail on the unique key. Say so rather than either.
    if dest is not None and dest["id"] != source["id"]:
        out("! %-34s cannot rename: %s already exists (id %s) -- use a Merge"
            % (ascii_safe(step.old), ascii_safe(step.new), dest["id"]))
        return 0

    mark = "~" if dry_run else "+"
    out("%s %-34s -> %-34s (%s cards)" % (
        mark, ascii_safe(step.old), ascii_safe(step.new), source["cards"]))

    cursor.execute("UPDATE topics SET name = %s WHERE id = %s",
                   (step.new, source["id"]))
    # The string column beside the id (#207) -- the half that is easy to forget.
    cursor.execute("UPDATE flashcards SET topic = %s WHERE topic_id = %s",
                   (step.new, source["id"]))
    if not dry_run:
        applog.topic_renamed(step.old, step.new, topic_id=source["id"],
                             cards=source["cards"])
    return 1


def do_merge(cursor, step, dry_run, allow_duplicates, out):
    source = find_topic(cursor, step.source)
    if source is None:
        out("= %-34s already merged" % ascii_safe(step.source))
        return 0

    dest = find_topic(cursor, step.dest)
    if dest is None:
        out("! %-34s destination %s does not exist -- skipped"
            % (ascii_safe(step.source), ascii_safe(step.dest)))
        return 0
    if dest["id"] == source["id"]:
        out("! %-34s is its own destination -- skipped"
            % ascii_safe(step.source))
        return 0

    clash = collisions(cursor, source["id"], dest["id"])
    if clash and not allow_duplicates:
        out("! %-34s -> %-34s %d collision(s): %s -- skipped" % (
            ascii_safe(step.source), ascii_safe(step.dest), len(clash),
            ", ".join("%s (%s)" % (ascii_safe(w), ascii_safe(p or "-"))
                      for w, p in clash[:5])))
        out("  (re-run with --allow-duplicates to move them anyway)")
        return 0
    if clash:
        out("  --allow-duplicates: moving %d word(s) the destination already "
            "holds" % len(clash))

    mark = "~" if dry_run else "+"
    out("%s %-34s -> %-34s (%s cards)" % (
        mark, ascii_safe(step.source), ascii_safe(step.dest), source["cards"]))

    cursor.execute(
        "UPDATE flashcards SET topic_id = %s, topic = %s WHERE topic_id = %s",
        (dest["id"], dest["name"], source["id"]))
    moved = cursor.rowcount

    # Re-ask rather than assume: fk_flashcards_topic is RESTRICT (#165), so a
    # source that somehow still holds a card must survive and be reported --
    # the delete would fail anyway, and a loud line beats a caught exception.
    cursor.execute("SELECT COUNT(*) FROM flashcards WHERE topic_id = %s",
                   (source["id"],))
    left = cursor.fetchone()[0]
    removed = False
    if left:
        out("  source still holds %d card(s) -- row kept" % left)
    else:
        cursor.execute("DELETE FROM topics WHERE id = %s", (source["id"],))
        removed = True
        out("  emptied row deleted (id %s)" % source["id"])

    if not dry_run:
        applog.topics_merged(step.source, dest["name"], moved,
                             source_id=source["id"], dest_id=dest["id"],
                             removed=removed)
    return 1


def run(plan, dry_run=False, allow_duplicates=False, out=None):
    """Apply `plan`. Returns the number of steps that did (or would do) work."""
    say = out or (lambda line: print(line, flush=True))
    conn = get_db_connection()
    changed = 0
    try:
        cursor = conn.cursor()
        for step in plan:
            if isinstance(step, Rename):
                changed += do_rename(cursor, step, dry_run, say)
            else:
                changed += do_merge(cursor, step, dry_run,
                                    allow_duplicates, say)
        if dry_run:
            conn.rollback()
        else:
            conn.commit()
        cursor.close()
    finally:
        conn.close()
    return changed


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Consolidate the topics in Other (#407).")
    parser.add_argument("--plan", choices=sorted(PLANS), required=True,
                        help="which database's plan to apply")
    parser.add_argument("--dry-run", action="store_true",
                        help="say what would change and write nothing")
    parser.add_argument("--allow-duplicates", action="store_true",
                        help="merge even where the destination already holds "
                             "the word (off by default: #101 keeps one card "
                             "per word and part of speech)")
    args = parser.parse_args(argv)

    plan = PLANS[args.plan]
    print("plan: %s (%d steps)%s" % (
        args.plan, len(plan), "  [dry run]" if args.dry_run else ""))
    changed = run(plan, dry_run=args.dry_run,
                  allow_duplicates=args.allow_duplicates)
    print()
    if not changed:
        print("nothing to do")
    elif args.dry_run:
        print("%d step(s) would change something; nothing was written" % changed)
    else:
        print("%d step(s) applied" % changed)
    return 0


if __name__ == "__main__":
    sys.exit(main())
