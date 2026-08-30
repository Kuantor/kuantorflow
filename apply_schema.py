r"""Apply the database schema and its pending migrations (issue #180).

Run this instead of piping schema.sql into mysql:

    venv/Scripts/python apply_schema.py             # apply
    venv/Scripts/python apply_schema.py --dry-run   # say what would change

`schema.sql` creates its tables with CREATE TABLE IF NOT EXISTS, which does
nothing to a table that already exists — so re-applying the file adds a new
*table* but never a new *column*. On 2026-08-02 that gap took card saving down
in production: #89's ALTER statements lived in schema.sql as **comments**, the
deploy re-applied the file and reported success, and the new code then inserted
into a column that was never added.

So the two halves are kept apart here:

* **schema.sql** defines a *fresh* database and holds CREATE TABLE only.
* **MIGRATIONS** below changes an *existing* one, as real statements.

Every step names the object it creates and is skipped when the database already
has it. That makes a re-run a no-op which says so, and it lets a half-applied
database — some ALTERs already run by hand, as production's were — finish
cleanly rather than failing on the first duplicate.

Adding a column later is therefore two edits in this repo: the column in
schema.sql, for databases that don't exist yet, and a Migration here, for the
one that does.

Most steps create an *object* and are skipped when the database already has it.
A step that moves **data** has no object to look up, so it carries a `Pending`
probe instead: SQL that still returns rows while there is work left to do
(issue #207's topic backfill is the first). Same contract either way — the
database is asked, nothing is recorded.
"""

import argparse
import re
import sys
from collections import namedtuple
from pathlib import Path

from utils import get_db_connection

SCHEMA_PATH = Path(__file__).with_name("schema.sql")

# What a step creates. Existence of this object is the whole idempotency
# check — there is no record of "which migrations ran", because the database
# itself already answers the only question that matters.
Table = namedtuple("Table", "table")
Column = namedtuple("Column", "table column")
Index = namedtuple("Index", "table index")
Constraint = namedtuple("Constraint", "table constraint")

# A step that moves *data* rather than creating an object (issue #207's
# backfill). It has no name to look up, so it carries its own question: SQL
# that returns at least one row while there is still work left to do. "No rows"
# is what "already applied" means for these.
#
# The query has to describe the work, not count it — "cards with a topic but no
# topic_id", never "how many topics exist" — so that a run interrupted halfway
# is correctly seen as unfinished rather than as done.
Pending = namedtuple("Pending", "probe")

Step = namedtuple("Step", "name target statements")

CREATE_TABLE_RE = re.compile(
    r"CREATE\s+TABLE\s+(?:IF\s+NOT\s+EXISTS\s+)?`?(\w+)`?", re.IGNORECASE
)

# Changes to tables that already exist, oldest first. Order matters: a column
# has to exist before it can be indexed, and be indexed before a foreign key
# can reference it. These four were comments in schema.sql until #180.
MIGRATIONS = (
    Step(
        "flashcards.pos",
        Column("flashcards", "pos"),
        ("ALTER TABLE flashcards ADD COLUMN pos VARCHAR(20) AFTER word",),
    ),
    Step(
        "flashcards.added_by_user_id",
        Column("flashcards", "added_by_user_id"),
        ("ALTER TABLE flashcards ADD COLUMN added_by_user_id INT NULL AFTER topic",),
    ),
    Step(
        "flashcards.idx_added_by",
        Index("flashcards", "idx_added_by"),
        ("ALTER TABLE flashcards ADD INDEX idx_added_by (added_by_user_id)",),
    ),
    Step(
        "flashcards.fk_flashcards_user",
        Constraint("flashcards", "fk_flashcards_user"),
        (
            "ALTER TABLE flashcards ADD CONSTRAINT fk_flashcards_user "
            "FOREIGN KEY (added_by_user_id) REFERENCES users (id) "
            "ON DELETE RESTRICT",
        ),
    ),
    # Blocking an account (#126). Two columns in one ALTER, so they arrive
    # together or not at all — checking only blocked_at is then enough, and
    # there is no half-applied state for the check to miss.
    Step(
        "users.blocked_at",
        Column("users", "blocked_at"),
        (
            "ALTER TABLE users "
            "ADD COLUMN blocked_at TIMESTAMP NULL DEFAULT NULL, "
            "ADD COLUMN blocked_reason VARCHAR(255) NULL",
        ),
    ),
    # Topics get their own table (#207). The table itself needs nothing here —
    # it is a CREATE TABLE in schema.sql, which the pass above already applies
    # to old databases and new ones alike. What is left is teaching the existing
    # flashcards table to point at it, in the order the objects depend on each
    # other: column, then the data, then the index, then the key.
    Step(
        "flashcards.topic_id",
        Column("flashcards", "topic_id"),
        ("ALTER TABLE flashcards ADD COLUMN topic_id INT NULL AFTER topic",),
    ),
    # The backfill: one topics row per distinct topic string, then every card
    # linked to it. The first data step in this script — see Pending above for
    # why it is checked by a question rather than by a name.
    Step(
        "flashcards.topic_id backfill",
        Pending(
            "SELECT 1 FROM flashcards "
            "WHERE topic IS NOT NULL AND topic <> '' AND topic_id IS NULL "
            "LIMIT 1"
        ),
        (
            # Creator and age come from the *earliest card* in each topic, not
            # from NULL and NOW(). Under the rules this replaces, a topic came
            # into existence when the first card was filed under it — so
            # whoever saved that card did create it, and that is when. Stamping
            # NOW() would tell every later reader that the whole deck was
            # created on migration day.
            #
            # Cards saved anonymously or before #89 have no owner, so topics
            # that began that way inherit NULL: the honest answer, arrived at
            # rather than assumed.
            #
            # GROUP BY collapses case-variant spellings ('Work'/'work') because
            # the column collation is case-insensitive, so the UNIQUE key
            # cannot be violated here — but the surviving spelling is then
            # whichever the engine picked. ORDER BY created_at, id makes the
            # creator deterministic where several cards share a timestamp.
            #
            # ON DUPLICATE KEY UPDATE id = id is a no-op that makes a re-run
            # after a half-finished attempt harmless: existing rows keep the
            # creator and age they were given, and correctness never rests on
            # catching a duplicate-key error.
            "INSERT INTO topics (name, created_by_user_id, created_at) "
            "SELECT f.topic, "
            "       (SELECT f2.added_by_user_id FROM flashcards f2 "
            "         WHERE f2.topic = f.topic "
            "         ORDER BY f2.created_at, f2.id LIMIT 1), "
            "       MIN(f.created_at) "
            "  FROM flashcards f "
            " WHERE f.topic IS NOT NULL AND f.topic <> '' "
            " GROUP BY f.topic "
            "ON DUPLICATE KEY UPDATE id = id",
            # The join is case-insensitive for the same reason, so every
            # spelling lands on the single surviving row. Cards with no topic
            # keep topic_id NULL — "no topic" is a real state (get_topics() has
            # always filtered it out), not a topic named ''.
            #
            # `topic` is rewritten to the row's name as well, so the string and
            # the id never disagree about spelling. Only case can change, and
            # only for variants the old GROUP BY already displayed as one
            # topic — the column still holds a usable topic name afterwards,
            # which is what makes it a rollback.
            "UPDATE flashcards f JOIN topics t ON f.topic = t.name "
            "   SET f.topic_id = t.id, f.topic = t.name "
            " WHERE f.topic_id IS NULL AND f.topic IS NOT NULL AND f.topic <> ''",
        ),
    ),
    Step(
        "flashcards.idx_topic_id",
        Index("flashcards", "idx_topic_id"),
        ("ALTER TABLE flashcards ADD INDEX idx_topic_id (topic_id)",),
    ),
    # Last, so it is applied to data that already satisfies it. Were the
    # backfill to be skipped or to fail, this fails loudly rather than leaving
    # a table whose key does not describe its contents.
    Step(
        "flashcards.fk_flashcards_topic",
        Constraint("flashcards", "fk_flashcards_topic"),
        (
            "ALTER TABLE flashcards ADD CONSTRAINT fk_flashcards_topic "
            "FOREIGN KEY (topic_id) REFERENCES topics (id) "
            "ON DELETE RESTRICT",
        ),
    ),
    # Topic sections (#215). Same shape as #207 one level up: the
    # `topic_sections` table is a CREATE TABLE in schema.sql, and what is left
    # here is teaching `topics` to point at it — columns, then the rows to point
    # at, then the data, then the index, then the key.
    #
    # Two columns in one ALTER, as users.blocked_at does: they are one feature,
    # they arrive together or not at all, and checking section_id is then enough
    # to know both are there.
    Step(
        "topics.section_id",
        Column("topics", "section_id"),
        (
            "ALTER TABLE topics "
            "ADD COLUMN section_id INT NULL AFTER name, "
            "ADD COLUMN position INT NOT NULL DEFAULT 0 AFTER section_id",
        ),
    ),
    # The two starting sections. Data, not an object, so a Pending probe asks
    # the question — and it asks for *both* rows, not for the table, because a
    # run interrupted between the two inserts has work left to do.
    #
    # 'Other' is positioned far past its neighbour so later sections can be
    # slotted in front of it without renumbering; see schema.sql. The B2-C1
    # section is created **empty** on purpose: filling it is #203, and a section
    # with no topics is a perfectly good row — the same way an empty topic is
    # (#207), because the row is where the name and the order live.
    Step(
        "topic_sections rows",
        Pending(
            "SELECT 1 FROM topic_sections "
            "WHERE name IN ('Other', 'B2–C1 Conversational Topics') "
            "HAVING COUNT(*) < 2"
        ),
        (
            # ON DUPLICATE KEY UPDATE id = id for the reason #207's backfill
            # gives: a re-run after a half-finished attempt inserts the missing
            # row and leaves the existing one exactly as it is, including a
            # position an admin may have changed by hand.
            "INSERT INTO topic_sections (name, position) VALUES "
            "('B2–C1 Conversational Topics', 1), ('Other', 100) "
            "ON DUPLICATE KEY UPDATE id = id",
        ),
    ),
    # Every topic that predates sections goes to 'Other' — the honest place for
    # a topic whose curriculum position nobody has decided. `position` keeps its
    # default of 0 across the whole bucket, so they stay in the alphabetical
    # order the index page already shows (see schema.sql on the name tiebreak):
    # this migration is deliberately invisible on the page.
    #
    # The probe is "a topic with no section", not "does 'Other' have rows" —
    # it has to describe the work, so that a topic created between this step and
    # the next deploy is still seen as unfinished business.
    Step(
        "topics.section_id backfill",
        Pending("SELECT 1 FROM topics WHERE section_id IS NULL LIMIT 1"),
        (
            "UPDATE topics t JOIN topic_sections s ON s.name = 'Other' "
            "   SET t.section_id = s.id "
            " WHERE t.section_id IS NULL",
        ),
    ),
    Step(
        "topics.idx_topics_section",
        Index("topics", "idx_topics_section"),
        ("ALTER TABLE topics ADD INDEX idx_topics_section (section_id)",),
    ),
    # Last, so it meets data that already satisfies it — #207's reason, and here
    # it also means a skipped or failed backfill fails loudly right here rather
    # than leaving a key that does not describe the table.
    Step(
        "topics.fk_topics_section",
        Constraint("topics", "fk_topics_section"),
        (
            "ALTER TABLE topics ADD CONSTRAINT fk_topics_section "
            "FOREIGN KEY (section_id) REFERENCES topic_sections (id) "
            "ON DELETE RESTRICT",
        ),
    ),
    # Private topics (#382). Four steps, and the order is the whole of it: the
    # flag, the generated column that reads it, the old key out, the new key
    # in. Swapping the last two would leave `uq_topics_name` alive alongside a
    # key that permits what it forbids -- and the old one wins, silently, which
    # is the feature not working with everything reporting success.
    Step(
        "topics.is_public",
        Column("topics", "is_public"),
        ("ALTER TABLE topics ADD COLUMN is_public TINYINT(1) NOT NULL "
         "DEFAULT 1 AFTER position",),
    ),
    Step(
        "topics.namespace",
        Column("topics", "namespace"),
        # A plain column: MySQL will not derive this one. `IF(is_public, 0,
        # created_by_user_id)` is refused both as a generated column (1215) and
        # as a CHECK (3823) because fk_topics_user's ON DELETE SET NULL needs
        # that column for its referential action. schema.sql says the rest.
        ("ALTER TABLE topics ADD COLUMN namespace INT NOT NULL DEFAULT 0 "
         "AFTER is_public",),
    ),
    # Out with the old key and in with the new, in **one** step targeted on the
    # new one. A removal usually needs a question rather than a name (an
    # existence check on the thing being dropped is backwards, which is why
    # #207 grew `Pending`) -- but here the drop and the add are one change, and
    # the arriving key is a perfectly good name to check:
    #
    #   * an old database has neither -> both statements run, in order;
    #   * a fresh one has the new key from schema.sql -> skipped, and the DROP
    #     that would fail on it never runs;
    #   * a second run -> skipped.
    #
    # The two must not be separate steps. Between them the table would hold a
    # key that permits what the other forbids, and the stricter one wins -- so
    # a half-applied migration would look finished and quietly refuse every
    # private topic that shares a name.
    Step(
        "topics.uq_topics_namespace",
        Index("topics", "uq_topics_namespace"),
        (
            "ALTER TABLE topics DROP INDEX uq_topics_name",
            "ALTER TABLE topics ADD UNIQUE KEY uq_topics_namespace "
            "(name, namespace)",
        ),
    ),
)


class SchemaError(RuntimeError):
    """schema.sql holds something this script will not run blindly."""


class StepFailed(RuntimeError):
    """A statement was rejected by the database."""

    def __init__(self, step, statement, cause):
        super().__init__(f"{step} failed: {cause}\n    statement: {statement}")
        self.step = step
        self.statement = statement
        self.cause = cause


def parse_statements(sql_text):
    """Split schema.sql into executable statements.

    Comments sit on their own lines in schema.sql — deliberately, since an
    instruction hidden at the end of a code line is exactly what #180 is
    about — so dropping whole comment lines is enough and no string-literal
    parsing is needed.
    """
    body = "\n".join(
        line for line in sql_text.splitlines() if not line.strip().startswith("--")
    )
    return [chunk.strip() for chunk in body.split(";") if chunk.strip()]


def schema_steps(sql_text):
    """One step per CREATE TABLE in schema.sql, in file order."""
    steps = []
    for statement in parse_statements(sql_text):
        match = CREATE_TABLE_RE.match(statement)
        if not match:
            first_line = statement.splitlines()[0]
            raise SchemaError(
                "schema.sql may hold CREATE TABLE statements only; found:\n"
                f"    {first_line}\n"
                "A change to an existing table belongs in MIGRATIONS in "
                "apply_schema.py — re-applying schema.sql cannot make it."
            )
        name = match.group(1)
        steps.append(Step(name, Table(name), (statement,)))
    return steps


class Schema:
    """What the connected database already contains."""

    def __init__(self, conn):
        self._conn = conn

    def _any(self, sql, params):
        cursor = self._conn.cursor()
        try:
            cursor.execute(sql, params)
            return bool(cursor.fetchall())
        finally:
            cursor.close()

    def exists(self, target):
        """True when the object a step would create is already there."""
        if isinstance(target, Table):
            return self._any(
                "SELECT 1 FROM information_schema.TABLES "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s",
                (target.table,),
            )
        if isinstance(target, Column):
            return self._any(
                "SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                "AND COLUMN_NAME = %s",
                (target.table, target.column),
            )
        if isinstance(target, Index):
            return self._any(
                "SELECT 1 FROM information_schema.STATISTICS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                "AND INDEX_NAME = %s",
                (target.table, target.index),
            )
        if isinstance(target, Constraint):
            return self._any(
                "SELECT 1 FROM information_schema.TABLE_CONSTRAINTS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = %s "
                "AND CONSTRAINT_NAME = %s",
                (target.table, target.constraint),
            )
        if isinstance(target, Pending):
            # A data step is "already applied" when its probe finds nothing
            # left to do.
            #
            # A probe naturally mentions the column an earlier step adds, so
            # under --dry-run — where that step was only *reported* — it can
            # fail on a column that does not exist yet. That failure is itself
            # the answer: nothing has been backfilled, so the step is pending.
            # On a real run the earlier steps have actually been applied by the
            # time this is asked, and a genuinely broken probe still surfaces
            # when the step's own statements run.
            try:
                return not self._any(target.probe, ())
            except Exception:  # noqa: BLE001 - reported as pending, see above
                return False
        raise TypeError(f"unknown step target: {target!r}")


def run(steps, schema, conn, dry_run=False, out=None):
    """Apply the steps the database is missing. Returns (changed, skipped).

    Existence is re-checked per step rather than once up front, so a step can
    depend on the one before it (the index needs its column).
    """
    out = out if out is not None else sys.stdout  # resolved late, so it is redirectable
    # flush per line: piped into a deploy log, a block-buffered stdout would
    # otherwise land *after* the stderr message saying which step failed.
    changed = skipped = 0
    for step in steps:
        if schema.exists(step.target):
            print(f"  = {step.name:<28} already present", file=out, flush=True)
            skipped += 1
            continue
        if dry_run:
            print(f"  ~ {step.name:<28} would be applied", file=out, flush=True)
            changed += 1
            continue
        cursor = conn.cursor()
        try:
            for statement in step.statements:
                try:
                    cursor.execute(statement)
                except Exception as exc:  # mysql.connector.Error and friends
                    raise StepFailed(step.name, statement, exc) from exc
        finally:
            cursor.close()
        conn.commit()
        print(f"  + {step.name:<28} applied", file=out, flush=True)
        changed += 1
    return changed, skipped


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Create missing tables and apply pending schema changes.",
        epilog="Safe to re-run: a second run reports that there is nothing to do.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="report what would change without touching the database",
    )
    args = parser.parse_args(argv)

    try:
        tables = schema_steps(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, SchemaError) as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    conn = get_db_connection()
    try:
        schema = Schema(conn)
        print("schema.sql")
        created, present = run(tables, schema, conn, args.dry_run)
        print("migrations")
        applied, already = run(MIGRATIONS, schema, conn, args.dry_run)
    except StepFailed as exc:
        print(f"error: {exc}", file=sys.stderr)
        print("Nothing after this step was applied - fix it and re-run.",
              file=sys.stderr)
        return 1
    finally:
        conn.close()

    changed, skipped = created + applied, present + already
    # Deploy output is read in a Windows console as often as a Linux one, so
    # it stays ASCII: a mojibaked dash in a deploy log is a distraction.
    if not changed:
        print(f"nothing to do - {skipped} object(s) already in place")
    else:
        verb = "pending" if args.dry_run else "applied"
        print(f"{changed} change(s) {verb}, {skipped} already in place")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
