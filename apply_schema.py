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
