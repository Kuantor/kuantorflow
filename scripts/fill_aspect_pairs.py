"""Fill `aspect_pairs` from Wiktionary for every verb card in the deck (#100).

    venv/bin/python scripts/fill_aspect_pairs.py              # both languages
    venv/bin/python scripts/fill_aspect_pairs.py --dry-run    # count only
    venv/bin/python scripts/fill_aspect_pairs.py --lang ukr

Reads every verb card's Ukrainian and Russian translations, takes their
single-word variants, and asks Wiktionary about each form **not yet checked**,
storing its aspect partner or "checked, no partner". Idempotent: a second run
says `nothing to do`. Polite to Wikimedia (batches of 25 with a 6-second pause,
`Retry-After` honoured), so a first run over a few hundred forms takes a few
minutes. A card saved during a Wiktionary outage is picked up here.

Run after the deploy that creates the table (`apply_schema.py`), on the local
database and on PythonAnywhere. Not part of a deploy otherwise.
"""

import argparse
import sys

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

import aspects
import utils

LANGS = ("ukr", "rus")


def forms_by_lang(rows, langs):
    """Single-word variants per language, from (translation_ukr, translation_rus) rows."""
    out = {lang: [] for lang in langs}
    for ukr, rus in rows:
        for lang, text in (("ukr", ukr), ("rus", rus)):
            if lang in out:
                out[lang] += aspects.single_word_variants(text)
    return {lang: list(dict.fromkeys(forms)) for lang, forms in out.items()}


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Fill aspect_pairs from Wiktionary for the deck's verb cards (#100).")
    parser.add_argument("--dry-run", action="store_true",
                        help="count the forms that would be asked, ask nothing")
    parser.add_argument("--lang", choices=LANGS, help="one language only")
    args = parser.parse_args(argv)

    langs = (args.lang,) if args.lang else LANGS
    wanted = forms_by_lang(utils.verb_card_translations(), langs)

    if args.dry_run:
        for lang, forms in wanted.items():
            norms = {aspects.normalise(f) for f in forms}
            todo = norms - utils.aspect_forms_checked(lang, norms)
            print(f"{lang}: {len(norms)} forms on verb cards; would ask Wiktionary about {len(todo)}")
        return 0

    summary = aspects.fill(wanted, source="script")
    if not any(s["asked"] for s in summary.values()):
        print("nothing to do: every form on a verb card has been checked")
        return 0
    for lang, s in summary.items():
        print(f"{lang}: asked about {s['asked']} form(s), {s['paired']} with a partner")
    return 0


if __name__ == "__main__":
    sys.exit(main())
