"""Propose a topic and a word list from a learner's idea (#406).

`seed_topics.py` already turns *(topic name, word list)* into real cards through
the app's own `lookup_word()` and `save_flashcard()`. What it cannot do is
decide what the words should be, and what the app cannot do is let a learner
ask. This is the missing half: an idea in, a title and a list of headwords out.

**This follows `textgen.py`, which follows `parsers._split_glued_translations()`
-- one pattern for calling Claude from this repo, not three.** A module model
constant, a client built at call time, bounded `max_tokens`, and a `try` in the
caller that logs its own failure through `applog`. Not `MykolaAgent`: that is a
conversation with a system prompt and card context, and this is one stateless
call returning a list.

**It proposes and nothing else.** Nothing here writes a card, creates a topic,
spends a lookup or touches the database -- the learner sees the list and
approves it first, and the approval is where the money starts. That is #200's
rule (guard before you spend) applied to a feature whose expensive half comes
*after* the model call rather than during it.

**Headwords, and the prompt says so.** #221 is the failure this is avoiding: a
seeded word with no Oxford entry reached production with translations and no
explanation, invisible locally because Reverso covered the gap. A model asked
for twenty words will otherwise hand back inflections (`tactics` for `tactic`),
multi-word phrases and the occasional word that is simply wrong -- so the
prompt asks for singular, uninflected, single-word entries, and the route vets
what comes back against a lexicon before spending anything on it (#389's
`parsers.wiktionary_pages()`, which is free and already deployed).
"""

import re

import applog

# The same model `textgen.py` uses, and its own constant for the same reason
# given there: two features that agree on a model today are still two
# decisions, and either should be able to move without the other. Proposing
# twenty B2 headwords is not a task that needs a larger one.
TOPIC_MODEL = "claude-haiku-4-5-20251001"

# What a learner may ask for. The floor is where a "topic" stops being one; the
# ceiling is the lookup budget rather than the model -- twenty words is twenty
# paid lookups against a daily fifty (#388), which the approve screen states out
# loud and claims up front.
MIN_WORDS = 3
MAX_WORDS = 20
DEFAULT_WORDS = 12

# The learner's idea, capped and collapsed onto one line before it reaches the
# prompt. `textgen.INSTRUCTION_MAX_CHARS` and `clean_preferred_name()` in
# ai_agent are the two precedents, and the reason is the one given there: this
# value ends up inside a model prompt, and there is no need to hand anybody an
# injection vector. Two hundred characters is a generous "renting a flat in
# London, especially the paperwork".
IDEA_MAX_CHARS = 200

# A title is a few words. Longer than this is the model having written a
# sentence where a title was asked for -- `textgen.TITLE_MAX_WORDS` draws the
# same line at twelve for the same reason.
TITLE_MAX_WORDS = 8
TITLE_MAX_CHARS = 80

# Room for a title and `MAX_WORDS` short lines, with slack for the model
# restating the format. Bounded like every other call in this repo.
BASE_TOKENS = 120
TOKENS_PER_WORD = 6

# What a headword may look like. Single word, letters only -- the same shape
# `games.pseudowords()` and #132's filters assume, and the shape `lookup_word()`
# is built for. A hyphen is allowed because `well-being` is one headword.
HEADWORD = re.compile(r"^[a-z][a-z-]{2,}$")


def max_tokens(count):
    return BASE_TOKENS + TOKENS_PER_WORD * max(count, 0)


def clean_idea(text):
    """The learner's line, fit to go in a prompt.

    Collapsed onto one line and capped. Newlines go because a prompt built from
    a multi-line value is a prompt somebody else can write the end of.
    """
    one_line = " ".join(str(text or "").split())
    return one_line[:IDEA_MAX_CHARS]


def word_count(raw, default=DEFAULT_WORDS):
    """How many words to ask for, from whatever arrived in the form."""
    try:
        wanted = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return max(MIN_WORDS, min(MAX_WORDS, wanted))


def _prompt(idea, count):
    """The whole prompt, in one place so it can be read as a unit.

    It asks for a fixed, parseable shape rather than prose, because the answer
    is a list and a model asked for prose returns a paragraph with the list
    inside it. `TITLE:` then one word per line is the least a parser can be
    wrong about.
    """
    return (
        "You are helping build a vocabulary topic for an intermediate to "
        "upper-intermediate (B2-C1) learner of English.\n\n"
        f"Their idea for the topic: {idea}\n\n"
        f"Reply with exactly this shape and nothing else:\n"
        f"TITLE: <a short topic name, at most {TITLE_MAX_WORDS} words>\n"
        f"then {count} lines, one English word per line.\n\n"
        "Rules for the words:\n"
        "- dictionary headwords only: singular, uninflected, lower case "
        "(write 'tactic', not 'tactics'; 'apply', not 'applies')\n"
        "- one word per line, no phrases, no numbering, no punctuation\n"
        "- words a B2-C1 learner would want and might not know; avoid the "
        "hundred commonest words in English\n"
        "- every word must be a real English word with a dictionary entry\n"
        f"- exactly {count} of them, all different"
    )


def _parse(reply, count):
    """`(title, words)` out of the model's answer.

    Forgiving about the shape and strict about the content: a stray blank line,
    a bullet or a numbered list costs nothing, but anything that is not a single
    alphabetic headword is dropped rather than sent to a dictionary. Duplicates
    go too -- #101 would skip the second one anyway, and a list showing the same
    word twice looks like a bug on the approve screen.
    """
    title, words, seen = "", [], set()
    for line in str(reply or "").splitlines():
        line = line.strip()
        if not line:
            continue
        if not title and line.lower().startswith("title:"):
            title = " ".join(line.split(":", 1)[1].split())[:TITLE_MAX_CHARS]
            continue
        candidate = line.lstrip("-*0123456789. \t").strip().lower()
        if not HEADWORD.match(candidate) or candidate in seen:
            continue
        seen.add(candidate)
        words.append(candidate)
    return title, words[:count]


def _ask_claude(prompt, count):
    """One call, returning the model's plain text."""
    import anthropic

    client = anthropic.Anthropic()
    message = client.messages.create(
        model=TOPIC_MODEL,
        max_tokens=max_tokens(count),
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in message.content if block.type == "text").strip()


def propose(idea, count):
    """`(title, words)` for a learner's idea, or `(None, [])` on failure.

    Failure is a return rather than a raise, the way `textgen.generate()` does
    it: the caller has a page to render either way, and the cause belongs in
    the log rather than in front of a learner.

    A short list is **not** a failure. The model occasionally returns fewer
    usable headwords than asked for, and the approve screen is exactly the place
    to notice that -- the learner can add their own or ask again, which is
    cheaper than this function quietly asking twice.
    """
    idea = clean_idea(idea)
    if not idea:
        return None, []
    try:
        reply = _ask_claude(_prompt(idea, count), count)
    except Exception as error:
        applog.topic_proposal_failed(idea, error)
        return None, []

    title, words = _parse(reply, count)
    applog.topic_proposed(idea, title, len(words), wanted=count,
                          model=TOPIC_MODEL)
    return (title or None), words
