r"""Give the creatorless topics in a section an owner (issue #394).

    venv/Scripts/python claim_topics.py --owner you@example.com --dry-run
    venv/Scripts/python claim_topics.py --owner you@example.com
    venv/Scripts/python claim_topics.py --owner you@example.com --section "B2-C1 Conversational Topics"

`topics.created_by_user_id` is NULL for every topic that predates #207, for
everything `seed_topics.py` filed without `--owner`, and for anything an
anonymous visitor created before #125. That reads as missing attribution and is
also a dead end: since #382 the creator is what decides who may see a private
topic, so `set_topic_visibility()` refuses a creatorless one -- meaning those
topics can never be hidden, by anybody, from anywhere in the UI. This is the
way out, and the only thing it does.

Shaped like `apply_schema.py` and `seed_topics.py`, because that shape is what
a PythonAnywhere console run needs:

* **`--dry-run` first.** It names every row it would change and writes nothing.
* **Idempotent.** Only a NULL creator is claimed, so a second run says there is
  nothing to do.
* **It never takes a topic from somebody else.** Another learner's topic is
  reported and left alone -- `created_by_user_id` decides who can see their
  private topic, so rewriting it would take the topic out of their deck and put
  it in yours. That is not a flag away; it is not offered.
* **ASCII output.** A Windows console is cp1252 and a topic name is not
  guaranteed to be, so names are printed with non-ASCII characters escaped
  rather than risking a `UnicodeEncodeError` that would end a run mid-write.
* **Not a request.** `app._save_and_log()` reads the session and `g`, neither
  of which exists here, so the write logs itself from `utils` -- the rule
  `place_topic()` and `set_user_blocked()` already follow.
"""

import argparse
import sys

from utils import claim_unowned_topics, get_db_connection

# Where the topics that nobody placed deliberately end up (#215), and so where
# the creatorless ones are.
DEFAULT_SECTION = "Other"


def owner_id_for(email):
    """The user id for `--owner`, or None when the email matches no account.

    Case-insensitive, the way `seed_topics.py` resolves the same argument: it
    arrives from a console, typed by hand.
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


def ascii_name(name):
    """A topic name a cp1252 console can print."""
    return (name or "").encode("ascii", "backslashreplace").decode("ascii")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Give the topics with no creator in one section an owner.",
        epilog="Safe to re-run: only a topic with no creator is ever claimed.",
    )
    parser.add_argument("--owner", metavar="EMAIL", required=True,
                        help="the account to record as the topics' creator")
    parser.add_argument("--section", default=DEFAULT_SECTION,
                        help=f"which section to work on (default: {DEFAULT_SECTION!r})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching the database")
    args = parser.parse_args(argv)

    owner_id = owner_id_for(args.owner)
    if owner_id is None:
        # Before any write, and named: a typo here would otherwise be a run
        # that reports success and claims nothing.
        print(f"error: no account with email {args.owner!r}", file=sys.stderr)
        return 1

    claimed, left, missing = claim_unowned_topics(
        args.section, owner_id, dry_run=args.dry_run)

    if missing:
        print(f"error: no section named {args.section!r}", file=sys.stderr)
        return 1

    verb = "would claim" if args.dry_run else "claimed"
    for name in claimed:
        print(f"  + {ascii_name(name)}   {verb} for user {owner_id}")
    for name, creator in left:
        # Not a failure and not a warning: it is the rule working. Printed
        # because a run that silently skipped rows would look like a run that
        # had nothing to do.
        print(f"  = {ascii_name(name)}   left alone, created by user {creator}")

    if not claimed:
        print(f"nothing to do - every topic in {args.section!r} has a creator")
    else:
        print(f"{len(claimed)} topic(s) "
              + ("would be claimed" if args.dry_run else "claimed")
              + f" for {args.owner}"
              + (" - dry run, nothing written" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
