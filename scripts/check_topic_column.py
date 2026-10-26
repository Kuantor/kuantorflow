r"""Is `flashcards.topic` safe to drop? Compare it with the topics table (#210).

    venv/Scripts/python scripts/check_topic_column.py            # Windows
    venv/bin/python scripts/check_topic_column.py                # PythonAnywhere
    venv/bin/python scripts/check_topic_column.py --show 50      # more examples

#207 gave topics a table of their own and pointed `flashcards.topic_id` at it,
but kept the old `topic` string beside it for the transition. #210 drops the
string. Before it does, this compares every card's copy with the name its
`topic_id` points at, and sorts the cards into one case each:

    match            the string is the topic's name
    case_or_spacing  the same name, differing only in case or spaces
    differs          a different name: a stale copy
    id_only          a topic_id and no string (fine: nothing to lose)
    no_topic         neither (fine)
    unlinked         a string and NO topic_id     -- blocks the drop
    missing_topic    a topic_id with no topics row -- blocks the drop

**Only the last two block it**, and only they set the exit code (1). An
`unlinked` card's string is its only topic, so dropping the column would lose
it; a `missing_topic` card points at nothing. A stale `differs` string is
reported, not counted against the drop: the app already shows the topics row's
name, so dropping the copy loses nothing anybody sees.

**Read-only.** It runs one SELECT and writes nothing, so it is safe on
PythonAnywhere at any time. **ASCII output**, like the other console scripts:
a Windows console is cp1252, so Cyrillic topic names are printed escaped.

Not part of a deploy. Run it before #210, locally and on PythonAnywhere; once
#210 has dropped the column it says so and exits 0.
"""

import argparse

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

from utils import get_db_connection

CASES = ("match", "case_or_spacing", "differs", "id_only", "no_topic",
         "unlinked", "missing_topic")
BLOCKING = ("unlinked", "missing_topic")

COLUMN_QUERY = ("SELECT 1 FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = DATABASE() AND TABLE_NAME = 'flashcards' "
                "AND COLUMN_NAME = 'topic'")

QUERY = ("SELECT f.id, f.word, f.topic, f.topic_id, t.name "
         "FROM flashcards f LEFT JOIN topics t ON t.id = f.topic_id "
         "ORDER BY f.id")


def _same(a, b):
    return " ".join(str(a).split()).lower() == " ".join(str(b).split()).lower()


def classify(rows):
    """{case: [row, ...]} for rows of (id, word, topic, topic_id, topics.name)."""
    found = {case: [] for case in CASES}
    for row in rows:
        _id, _word, string, topic_id, name = row
        string = (string or "").strip() if string is not None else ""
        if topic_id is None:
            case = "unlinked" if string else "no_topic"
        elif name is None:
            case = "missing_topic"
        elif not string:
            case = "id_only"
        elif string == name:
            case = "match"
        elif _same(string, name):
            case = "case_or_spacing"
        else:
            case = "differs"
        found[case].append(row)
    return {case: rows for case, rows in found.items() if rows}


def blocking(found):
    """The cases present that would lose data if the column were dropped."""
    return [case for case in BLOCKING if found.get(case)]


def _ascii(text):
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Compare flashcards.topic with the topics table before "
                    "dropping the column (#210). Read-only.")
    parser.add_argument("--show", type=int, default=10,
                        help="examples to list per case (default 10)")
    args = parser.parse_args(argv)

    conn = get_db_connection()
    try:
        cursor = conn.cursor()
        # After #210 the column is gone, and that is the check's answer: there
        # is nothing left that could disagree with the topics table.
        cursor.execute(COLUMN_QUERY)
        present = bool(cursor.fetchall())
        rows = []
        if present:
            cursor.execute(QUERY)
            rows = cursor.fetchall()
        cursor.close()
    finally:
        conn.close()

    if not present:
        print("flashcards.topic is already gone (#210): every card's topic is "
              "its topic_id, and there is nothing left to compare.")
        return 0

    found = classify(rows)
    print(f"{len(rows)} cards")
    for case in CASES:
        cards = found.get(case, [])
        flag = "  <-- blocks the drop" if case in BLOCKING and cards else ""
        print(f"  {case:16} {len(cards):5}{flag}")

    for case in ("differs", "case_or_spacing", "unlinked", "missing_topic"):
        cards = found.get(case, [])
        if not cards:
            continue
        print(f"\n{case} (first {min(args.show, len(cards))} of {len(cards)}):")
        for card_id, word, string, topic_id, name in cards[:args.show]:
            print(_ascii(f"  #{card_id} {word}: topic={string!r} "
                         f"topic_id={topic_id} topics.name={name!r}"))

    blocked = blocking(found)
    if blocked:
        print("\nNOT safe to drop flashcards.topic: " + ", ".join(blocked)
              + " would lose a card's topic. Fix those cards first.")
        return 1
    print("\nsafe to drop flashcards.topic: every card's topic is in the "
          "topics table.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
