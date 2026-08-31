r"""Give the authorless cards in a section's topics an owner (issue #396).

    venv/Scripts/python claim_flashcards.py --owner you@example.com --dry-run
    venv/Scripts/python claim_flashcards.py --owner you@example.com
    venv/Scripts/python claim_flashcards.py --owner you@example.com --section "B2-C1 Conversational Topics"

The other half of `claim_topics.py`. That one gives a topic a creator; this
gives its cards an author, and without it the first does not finish its job:
`set_topic_visibility()` refuses a topic holding *other people's* cards, and a
card with no author counts as somebody else's -- so a claimed topic full of
unowned cards still cannot be hidden.

**`flashcards.added_by_user_id` is three things at once**, which is why this
writes more than attribution:

* **#127** hides other people's cards behind *Show only my cards*, so an
  unowned card is invisible there to everybody;
* **#162** lets only the owner delete a card, so an unowned one cannot be
  deleted at all except by the admin;
* **#382** reads it to decide whether a topic may be private.

So claiming a card makes it visible under that setting, deletable by its new
owner, and part of what **#165** asks about when that account is deleted. That
is the intended effect and it is worth knowing before the run rather than
after.

Shaped like `claim_topics.py` and `apply_schema.py`, because that shape is what
a PythonAnywhere console run needs:

* **`--dry-run` first**, naming every topic it would touch and every other
  author it found.
* **Idempotent.** Only a NULL author is claimed, so a second run has nothing
  to do.
* **It never takes a card from somebody else.** Their cards are counted, named
  and left. Not a flag: an author here is a permission, so taking one would
  hand somebody's work to another account in three ways at once.
* **ASCII output**, and **not a request** -- the write logs itself from
  `utils`, one line per topic. Both rules are `seed_topics.py`'s.
"""

import argparse
import sys

from utils import claim_unowned_cards, get_db_connection

# Where the topics nobody placed deliberately live (#215), and so where the
# unowned cards are.
DEFAULT_SECTION = "Other"


def owner_id_for(email):
    """The user id for `--owner`, or None when the email matches no account.

    Case-insensitive, as in `claim_topics.py` and `seed_topics.py`: it arrives
    from a console, typed by hand.
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
        description="Give the cards with no author in one section an owner.",
        epilog="Safe to re-run: only a card with no author is ever claimed.",
    )
    parser.add_argument("--owner", metavar="EMAIL", required=True,
                        help="the account to record as the cards' author")
    parser.add_argument("--section", default=DEFAULT_SECTION,
                        help=f"which section to work on (default: {DEFAULT_SECTION!r})")
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change without touching the database")
    args = parser.parse_args(argv)

    owner_id = owner_id_for(args.owner)
    if owner_id is None:
        # Before any write, and named: a typo would otherwise be a run that
        # reports success and claims nothing.
        print(f"error: no account with email {args.owner!r}", file=sys.stderr)
        return 1

    claimed, others, missing = claim_unowned_cards(
        args.section, owner_id, dry_run=args.dry_run)

    if missing:
        print(f"error: no section named {args.section!r}", file=sys.stderr)
        return 1

    verb = "would claim" if args.dry_run else "claimed"
    for name, count in claimed:
        print(f"  + {ascii_name(name)}   {count} card(s) {verb} for user {owner_id}")
    for author, email, count in others:
        # The report the run is half for. Another author is not a problem to
        # fix, but "who else has cards here" is exactly what somebody running
        # this wants to know, and silence would read as "nobody".
        print(f"  = user {author} ({email or 'account deleted'})   "
              f"{count} card(s), left alone")

    total = sum(count for _, count in claimed)
    if not total:
        print(f"nothing to do - every card in {args.section!r} has an author")
    else:
        print(f"{total} card(s) in {len(claimed)} topic(s) "
              + ("would be claimed" if args.dry_run else "claimed")
              + f" for {args.owner}"
              + (" - dry run, nothing written" if args.dry_run else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
