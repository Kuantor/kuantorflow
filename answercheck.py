"""
Quiz answers close in meaning, judged by Claude (#612).

In the Quiz (English -> Ukrainian/Russian) an answer is right when it is one
of the card's stored translations (`rounds._matched_a_variant`), or the other
aspect of a stored verb (#100). Anything else used to be wrong, though a
learner who writes a correct synonym the card does not list knows the word.
So what is **still unmatched** after those free checks is sent here, once per
round, and Claude gives each answer one of three verdicts:

    green   a correct translation in this sense (a synonym the card lacks):
            counts as right
    yellow  close, not 100% (a related word, a broader or narrower sense):
            shown apart, counted wrong for recall
    red     a different word or meaning, or not a translation: wrong

**One call per round, never per answer,** on Haiku like every call this repo
makes, under its own three ceilings (`utils.claim_answer_check`), after every
free check; verdicts are stored (`answer_verdicts`), so the same answer to the
same word is never paid for twice. **A failed or refused call costs the learner
nothing**: those answers stay wrong by the exact rule, as before #612.

The prompt carries learner-typed text and deck text written by other learners,
so all of it goes in through `promptdata.wrap()` (#602), and the reply must fit
a JSON schema; anything else is a failure, not a verdict.
"""

import json

import applog
import parsers
import promptdata

CHECK_MODEL = parsers.TRANSLATE_MODEL
CHECK_MAX_TOKENS = 1200
VERDICTS = ("green", "yellow", "red")
NOTE_MAX_CHARS = 120

SCHEMA = {
    "type": "object",
    "properties": {
        "verdicts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "n": {"type": "integer"},
                    "verdict": {"type": "string", "enum": list(VERDICTS)},
                    "note": {"type": "string"},
                },
                "required": ["n", "verdict", "note"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["verdicts"],
    "additionalProperties": False,
}


def build_prompt(language, items):
    """The whole prompt, in one place so it can be read as a unit."""
    lines = []
    for n, item in enumerate(items):
        lines.append(json.dumps({
            "n": n, "english": item["word"], "part_of_speech": item.get("pos") or "",
            "meaning": (item.get("explanation") or "")[:200],
            "stored_translations": item["translations"],
            "learner_answer": item["answer"],
        }, ensure_ascii=False))
    return (
        f"You check a language learner's quiz answers. For each item below the "
        f"learner saw an English word, with its part of speech and meaning, and "
        f"typed a translation into {language}. The answer is not one of the "
        "stored translations. Give each item one verdict:\n\n"
        f"- green: a teacher marking a vocabulary quiz would accept it as a "
        "translation of this word in this meaning: a synonym or near-synonym "
        "that a bilingual dictionary could list for it, even if it is not the "
        "most common one;\n"
        "- yellow: close, but a teacher would correct it -- a related word, a "
        "clearly broader or narrower meaning, a different shade, or the right "
        "word misspelt;\n"
        "- red: wrong -- a different word or meaning, the opposite, not a "
        f"translation, not {language}, or nonsense.\n\n"
        "Do not reward guesses: an answer that only shares a topic with the "
        "word is red. Give a note of at most ten words, written in English.\n\n"
        f"{promptdata.rule('items')}\n\n"
        + promptdata.wrap("items", "\n".join(lines), block=True)
    )


def judge(language, items, client=None):
    """{index: (verdict, note)} for `items`, from one call; raises on failure.

    Each item: `word`, `pos`, `explanation`, `translations` (the stored text),
    `answer`. An item the reply leaves out simply has no verdict.
    """
    if not items:
        return {}
    if client is None:
        import anthropic
        client = anthropic.Anthropic()
    with applog.Timer() as timer:
        message = client.messages.create(
            model=CHECK_MODEL, max_tokens=CHECK_MAX_TOKENS,
            messages=[{"role": "user", "content": build_prompt(language, items)}],
            output_config={"format": {"type": "json_schema", "schema": SCHEMA}},
        )
    applog.model_usage(applog.USAGE_CHECK, CHECK_MODEL, message, timer.ms)
    text = next((b.text for b in message.content if b.type == "text"), "")
    out = {}
    for row in json.loads(text).get("verdicts", []):
        n, verdict = row.get("n"), row.get("verdict")
        if isinstance(n, int) and 0 <= n < len(items) and verdict in VERDICTS:
            note = " ".join(str(row.get("note") or "").split())[:NOTE_MAX_CHARS]
            out[n] = (verdict, note)
    return out
