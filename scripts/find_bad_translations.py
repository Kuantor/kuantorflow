r"""List the saved cards whose translations are in the wrong alphabet (#544).

    venv/Scripts/python scripts/find_bad_translations.py

Since #544 every translator's answer is checked before it reaches a card
(`parsers.checked_translations()`), so no new card gets a Latin letter hidden in
a Ukrainian word, a Chinese word in a Ukrainian field, or a Russian-only letter
where Ukrainian was asked for. The cards saved **before** it are what this
finds: every comma-separated variant `parsers.script_problem()` would refuse,
with the card id, the word, the field and the reason.

**Report-only.** It writes nothing. The right fix for `екзубeрантний` is
the same word with a Cyrillic "е", and only a person knows that is what was
meant -- a script would have to guess the letter, which is how a wrong word gets
saved as a confident one. Correct each card in the card editor on its topic's
page, which the report names, or delete the bad variant there.

**ASCII output**, like the other console scripts: a Windows console is cp1252,
so non-ASCII text is printed escaped (`е`) rather than risking an error
halfway through the list. The reason names the offending character's code
point, which is what makes a hidden Latin "e" visible at all.

Not part of a deploy. Run it once on PythonAnywhere after #544 ships; re-running
is harmless and, once the cards are fixed, says `nothing to fix`.
"""

import argparse

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

import parsers
from utils import get_db_connection

FIELDS = (("translation_ukr", "ukr"), ("translation_rus", "rus"))


def bad_translations(cards):
    """Every wrong-alphabet variant in `cards` (rows with `id`, `word`,
    `topic` and the two translation fields), as
    `(id, word, topic, field, variant, reason)`."""
    found = []
    for card in cards:
        for field, lang in FIELDS:
            for variant in (card.get(field) or "").split(","):
                variant = variant.strip()
                reason = parsers.script_problem(variant, lang)
                if reason:
                    found.append((card["id"], card["word"], card.get("topic"),
                                  field, variant, reason))
    return found


def _ascii(text):
    return str(text).encode("ascii", "backslashreplace").decode("ascii")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="List saved cards whose translations are in the wrong "
                    "alphabet (#544). Report-only: writes nothing.")
    parser.parse_args(argv)

    conn = get_db_connection()
    try:
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT id, word, topic, translation_ukr, "
                       "translation_rus FROM flashcards ORDER BY id")
        cards = cursor.fetchall()
    finally:
        conn.close()

    found = bad_translations(cards)
    for card_id, word, topic, field, variant, reason in found:
        print(f"! card {card_id} '{_ascii(word)}' in '{_ascii(topic)}', "
              f"{field}: '{_ascii(variant)}' -- {_ascii(reason)}")
    if found:
        ids = sorted({row[0] for row in found})
        print(f"{len(found)} variant(s) on {len(ids)} of {len(cards)} cards. "
              "Correct each in the card editor on its topic's page.")
    else:
        print(f"nothing to fix ({len(cards)} cards checked)")


if __name__ == "__main__":
    main()
