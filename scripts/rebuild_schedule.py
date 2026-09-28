r"""Recompute every learner's SM-2 schedule from the answer log (issue #479).

    venv/Scripts/python scripts/rebuild_schedule.py --dry-run
    venv/Scripts/python scripts/rebuild_schedule.py
    venv/Scripts/python scripts/rebuild_schedule.py --user you@example.com

`recall_schedule` is a **cache** of `recall_answers` (#338): every row in it is
what `recall.replay()` says that word's answers add up to. After each graded
round the app refreshes the words that round answered, so on an ordinary day
nothing needs this. It is for the three moments when the cache and the log
can disagree:

* **The first deploy of #479.** Answers recorded since #338 shipped have no
  schedule rows yet -- the live refresh only reaches words answered *after*
  it exists.
* **A change to the rules in `recall.py`** -- the constants, the day
  boundary, how a tick is weighed. The rebuild makes every schedule what the
  new rules would always have said, which is the reason the log exists: Anki
  moved every learner to FSRS by replaying `revlog` the same way.
* **A refresh that failed.** The round logs it and carries on, and this
  repairs it.

Shaped like the other one-offs in `scripts/`:

* **`--dry-run` first.** It computes everything and writes nothing.
* **Idempotent.** A row already right is counted as `=` and not rewritten, so
  a second run reports no changes.
* **ASCII output**, per learner rather than per word: a console line for every
  word of every learner would drown the one number that matters.
"""

import argparse
import sys

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

from utils import get_db_connection, rebuild_schedules


def user_id_for(email):
    """The user id for `--user`, or None when the email matches no account.
    Case-insensitive, like `--owner` in the other scripts."""
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


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Recompute recall_schedule from recall_answers.",
        epilog="Safe to re-run: a row that is already right is left alone.",
    )
    parser.add_argument("--user", metavar="EMAIL",
                        help="rebuild one learner only (default: everybody)")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching the database")
    args = parser.parse_args(argv)

    user_id = None
    if args.user:
        user_id = user_id_for(args.user)
        if user_id is None:
            # Before anything runs: a typo would otherwise be a rebuild of
            # nobody that reports success.
            print(f"error: no account with email {args.user!r}", file=sys.stderr)
            return 1

    report = rebuild_schedules(user_id, dry_run=args.dry_run)
    if not report:
        print("nothing to do - no answers recorded"
              + (" for that learner" if user_id else ""))
        return 0

    totals = [0, 0, 0, 0]
    for uid in sorted(report):
        added, changed, same, removed = report[uid]
        totals = [t + n for t, n in zip(totals, report[uid])]
        mark = "~" if args.dry_run else "+"
        mark = mark if (added or changed or removed) else "="
        print(f"  {mark} user {uid}: {added} new, {changed} changed, "
              f"{same} unchanged, {removed} removed")

    added, changed, same, removed = totals
    pending = added + changed + removed
    if not pending:
        print(f"nothing to do - {same} word(s) already scheduled correctly")
    else:
        print(f"{pending} row(s) "
              + ("would change" if args.dry_run else "changed")
              + f", {same} already right"
              + (" - dry run, nothing written" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
