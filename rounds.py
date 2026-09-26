"""The games chassis, the ten rounds and the quiz (#418).

The largest block in `app.py` and the last one that had to wait: 1,479 lines
and 52 names, and until the spending guards moved to `web.py` it could not
leave, because `_read_a_text_round()` asks `_generation_refusal()` and so does
#406's topic builder, which is not a game.

It is **one module rather than two** although the ticket lists the quiz
separately. The quiz is not a tenth game -- `/quiz` and `/quiz/<topic>` are
deliberately two endpoints (#250) and predate the chassis -- but it grades
through `_graded_answers()`, picks topics through `_render_picker()` and reads
`QUIZ_LANGS` beside `_fill_the_gap_round()`. Splitting them would put an import
between halves of one mechanism, which is the shape #418 is trying to remove.

Nothing here is imported by `app.py`: the routes, the context processor and
`GAME_ROUNDS` all register themselves, so `app.py` imports this module for its
side effects and asks it for nothing. That is what keeps the dependency one
way -- `web.py` <- `rounds.py` <- `app.py` -- and it was measured rather than
hoped for, with a pass over every top-level name in both files.

`games.py` remains the module with no request and no database in it. This one
is the opposite: it is all request, and the division is the same one #389 drew
when the Wiktionary vet went to `app._vetted_pseudowords()` rather than into
`games.pseudowords()` -- a filter that is a property of the *deck* belongs
there, one that needs the network or the session belongs here.
"""

import random

from flask import (
    abort,
    redirect,
    render_template,
    request,
    session,
    url_for,
)

import applog
import games
import parsers
import textgen
import utils
import web
from web import app


def _answer_variants(translation):
    """
    Split a stored translation like 'дом, здание, жильё' into normalized
    variants accepted as correct answers.
    """
    return {
        variant.strip().lower().replace("ё", "е")
        for variant in translation.split(",")
        if variant.strip()
    }


def _matched_a_variant(card, given, field):
    """The quiz's answer rule: one of a stored translation's comma-separated
    variants, with the Cyrillic ё folded onto е.

    It stays on the quiz's own path rather than joining `games.same_answer()`.
    Both halves are facts about a stored *translation* -- that it is a list of
    synonyms, and that one Cyrillic letter is optional in writing -- and
    neither is a fact about an English headword (#267).
    """
    return given.lower().replace("ё", "е") in _answer_variants(card[field])


QUIZ_LANGS = {"rus": "Russian", "ukr": "Ukrainian"}

# Quiz language code -> the settings key that controls its visibility
# (#46/#79/#111). A hidden language can't be quizzed on.
QUIZ_LANG_SETTINGS = {"rus": "show_russian", "ukr": "show_ukrainian"}

# quiz_lang setting value (#113) -> quiz language code.
QUIZ_LANG_CODES = {"ukrainian": "ukr", "russian": "rus"}


def _visible_quiz_langs(prefs):
    """The QUIZ_LANGS subset this identity hasn't hidden in Settings."""
    return {
        code: name for code, name in QUIZ_LANGS.items()
        if prefs[QUIZ_LANG_SETTINGS[code]]
    }


def _visible_sections():
    """`utils.get_topics_by_section()` for this visitor, or [] if the DB is down.

    Same tolerance the index page has: a dead database leaves the picker with
    nothing to offer rather than a 500.
    """
    try:
        return web._sections_for_visitor()
    except Exception:
        app.logger.exception("Could not list topics for the picker")
        return []


def _hint_settings(activity):
    """`(session key, allowed modes, default)` for this activity's hint.

    One place that knows the two games differ, so a round and the picker cannot
    disagree about which modes are legal or which one a fresh visitor gets.
    """
    if activity.slug == "fill_the_gap":
        return games.GAP_HINT_KEY, games.GAP_HINTS, games.HINT_NONE
    # #340's worksheet offers exactly what *Fill the gap* offers -- the same
    # three modes and the same default -- under its own key, because a choice
    # made for a sheet about to be printed is not a choice about the next
    # round played on screen.
    if activity.slug == "read_a_text":
        return games.WORKSHEET_HINT_KEY, games.GAP_HINTS, games.HINT_NONE
    return games.HINT_KEY, games.HINTS, games.HINT_FIRST


def _remembered_hint(activity):
    key, allowed, default = _hint_settings(activity)
    return games.remembered_hint(session, key, allowed, default)


def _round_hint(activity):
    """The mode this round runs in, read from the query and remembered."""
    key, allowed, default = _hint_settings(activity)
    mode = games.hint_mode(request.args.get("hint"),
                           games.remembered_hint(session, key, allowed,
                                                 default),
                           allowed, default)
    games.remember_hint(session, mode, key, allowed, default)
    return mode


def _render_picker(activity, start_url):
    """The topic picker (#250), shared by every activity in #233.

    `start_url` is where the form submits. It carries no topics of its own —
    the ticked boxes are the query string, which is why this is a plain GET
    form: the resulting URL is the shareable, bookmarkable one #233 asked for,
    and no JavaScript is needed to build it.

    The remembered selection (#248) is re-checked against what is visible now,
    so a topic deleted, renamed, or hidden by #127 since the last round simply
    is not ticked.
    """
    sections = _visible_sections()
    visible = games.visible_topic_names(sections)
    # The translation language, chosen before the words are drawn rather than
    # after (#113). Offered only when there is a choice to make: with one
    # language hidden in Settings (#46/#79) a lone radio is a control that
    # cannot do anything, and the round says which language it is using anyway.
    quiz_langs = {}
    quiz_lang = None
    if activity.picks_language:
        prefs = web.current_settings()
        visible_langs = _visible_quiz_langs(prefs)
        if len(visible_langs) > 1:
            quiz_langs = visible_langs
            quiz_lang = _quiz_lang(prefs, visible_langs)
    return render_template(
        "picker.html",
        activity=activity,
        start_url=start_url,
        quiz_langs=quiz_langs,
        quiz_lang=quiz_lang,
        # The hint mode, remembered like the selection and the round length so
        # the picker opens on what was played last. Each game keeps its own,
        # because the sets differ and asking for help in one must not silently
        # soften the other (#334).
        hint=_remembered_hint(activity),
        hint_labels=games.HINT_LABELS,
        sections=[(name, topics) for name, topics in sections if topics],
        selected=set(games.remembered_selection(session, visible)),
        total_cards=sum(count for _, topics in sections for _, count in topics),
        # The box counts what this activity counts, between its own bounds
        # (#237) — questions for a quiz, words of prose for a text. Read off
        # the activity rather than off one pair of constants, so the picker
        # cannot offer 1–200 words of prose to a reader.
        words=games.remembered_word_count(session, activity.words),
        words_min=activity.words.low,
        words_max=activity.words.high,
        words_hint=activity.words.hint,
        instruction=session.get(INSTRUCTION_KEY, "") if activity.asks_instruction else "",
        instruction_max=textgen.INSTRUCTION_MAX_CHARS,
    )


def activity_picker_url(activity):
    """Where an activity's tile points: its picker, never a round (#233).

    The quiz keeps its own URLs, so the fork lives here rather than in each
    template — the panel exists to hide exactly this seam.
    """
    if activity.kind == "quiz":
        return url_for("quiz_topics")
    return url_for("game_picker", game=activity.slug)


def activity_play_url(activity, topic):
    """Straight into an activity for one topic — the topic page's links (#253).

    No picker: the topic is already chosen, and that is the whole context the
    page is in. A topic too thin for the activity is explained *there*, which
    is why this never redirects.
    """
    if activity.kind == "quiz":
        return url_for("quiz", topic=topic)
    return url_for("game_play", game=activity.slug, topic=topic)


# What a greyed tile says on hover (#261). An activity carrying `ticket` has no
# round yet; the tile stays on the page so the set of activities is legible, and
# explains itself rather than vanishing.
UNDER_CONSTRUCTION = "Not built yet — this one is still under construction."


@app.context_processor
def inject_activities():
    """The one declaration, reachable from every template that renders it
    (#233): the front-page panels and the topic page's activity row."""
    return {
        "game_activities": games.panel("game"),
        "quiz_activity": games.ACTIVITIES["quiz"],
        "reader_activity": games.ACTIVITIES["read_a_text"],
        "generation_available": web._generation_available(),
        "activity_picker_url": activity_picker_url,
        "activity_play_url": activity_play_url,
        # Said in one place because three surfaces say it (#261), and a tooltip
        # that differed between them would read as three different states.
        "under_construction": UNDER_CONSTRUCTION,
    }


@app.route("/games/<game>")
def game_picker(game):
    """The picker for one game (#250).

    Every game slug 404s until its own ticket registers it in
    `games.ACTIVITIES` and adds a /games/<slug>/play route. That is deliberate:
    a tile that opened a picker whose start button led nowhere would be worse
    than no tile at all.
    """
    activity = _reachable_activity(game)
    if activity is None:
        abort(404)
    return _render_picker(
        activity, url_for("game_play", game=activity.slug))


def _reachable_activity(slug):
    """The activity behind a /games/ URL, or **None** if it is not there.

    Not there covers three things, and they are one answer on purpose: an
    unknown slug, the quiz (which has its own URLs, so `/games/quiz` is not a
    second way in), and — since #237 — an activity whose requirements this
    deployment does not meet.

    Text generation with no `ANTHROPIC_API_KEY` cannot work and has no
    fallback: nothing else can write the text. So the tile is hidden, and this
    is the same answer given to a hand-typed URL. #233's rule is to hide a link
    the learner cannot act on, and `MYKOLA_AVAILABLE=False` already does exactly
    this for the chat widget.
    """
    found = games.activity(slug, kind=games.GAMES_URL_KINDS)
    if found is None or (found.kind == "reader" and not web._generation_available()):
        return None
    return found

# --- #237: the text this visitor is holding ---------------------------------
# What the round remembers between requests. The *ceilings* moved to `web.py`
# with the other spending guards (#418); these two are not policy, they are the
# session keys the reader reads back, so they stay with the round that writes
# them.
#
# The text itself, held so re-reading, flipping back and a stray refresh cost
# nothing (#237's "generate once").
#
# The Flask session is a signed cookie with a ~4 KB ceiling that Werkzeug
# enforces by silently dropping it — which would sign the learner out — and
# games.py warns in as many words that a generated text may not fit. It does,
# and the bound is `textgen.max_tokens()` rather than good luck: the longest
# text the model is *allowed* to return is 600 tokens, and the worst case
# measured — 400 words of deliberately incompressible text, a signed-in
# identity and an eighteen-topic selection — serialises to 3.2 KB of the 4 KB.
# Only the text and the words are kept; the highlighting is recomputed on each
# read, which is a regex over a paragraph and cheaper than carrying it.
GENERATED_TEXT_KEY = "generated_text"
# The learner's "what it should be about", remembered like the selection.
INSTRUCTION_KEY = "generated_about"


def _held_generation():
    """Whatever text this visitor is holding, ready to render, or None.

    The highlighting is worked out here rather than stored: it is a regex over
    a paragraph, and recomputing it costs less than carrying it in a cookie
    that has a 4 KB ceiling to respect.

    A failed generation is held too, and is not None — it has an `error` and no
    text. Dropping it would leave the learner on the "write me a text" panel
    with no idea that anything had been tried.
    """
    held = session.get(GENERATED_TEXT_KEY)
    if not isinstance(held, dict) or not (held.get("text") or held.get("error")):
        return None
    # textgen.mark() rather than games.mark_words() directly: the title counts
    # towards a word being used (#315), and that rule has to be the same one
    # generate() applied or a refresh would change the answer.
    return dict(held, **textgen.mark(held.get("title") or "",
                                     held.get("text") or "",
                                     held.get("words") or []))


def _held_for(topics, length, instruction):
    """The held text, but only if it is the one this request is asking for.

    Keyed on what produced it — the topics, the length and the instruction — so
    re-reading and a stray refresh are free while *changing* any of them offers
    a fresh text instead of silently showing one about something else.
    """
    held = _held_generation()
    if held is None:
        return None
    if (held.get("topics") != list(topics) or held.get("length") != length
            or held.get("instruction") != instruction):
        return None
    return held


def _read_a_text_round(activity, topics):
    """A round of #237: Claude writes a passage using the learner's own words.

    GET renders whatever is held and never spends anything; POST is the one
    thing that calls the API, and it redirects back to the GET so a refresh
    re-reads the text rather than writing a second one. Regeneration is that
    same explicit POST, and it spends an allowance — otherwise the ceilings
    mean nothing.
    """
    length = games.word_count(
        request.values.get("words"),
        games.remembered_word_count(session, activity.words),
        activity.words)
    instruction = textgen.clean_instruction(request.values.get("about"))
    games.remember_word_count(session, length, activity.words)
    session[INSTRUCTION_KEY] = instruction

    def page(**extra):
        return render_template(
            "game_read_a_text.html", activity=activity, topics=topics,
            words=length, instruction=instruction,
            instruction_max=textgen.INSTRUCTION_MAX_CHARS,
            topic_summary=_topic_summary(topics), **extra)

    if request.method == "POST":
        refusal = web._generation_refusal()
        if refusal:
            # Whatever they are holding, not only a text matching this request:
            # the instruction box may well be what they just changed, and the
            # answer to "you cannot have another" is to leave the one they have
            # on the screen rather than to clear it as well.
            return page(held=_held_generation(), refusal=refusal)
        cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
        chosen = textgen.words_for_text(cards, length)
        if not chosen:
            return page(held=None, refusal=None)
        result = textgen.generate(chosen, instruction, length,
                                  user=web._current_email())
        session[GENERATED_TEXT_KEY] = {
            "title": result["title"],
            "text": result["text"], "words": result["words"],
            "topics": list(topics), "length": length,
            "instruction": instruction, "error": result["error"],
        }
        # Post/redirect/get: the text now lives in the session, so the address
        # bar holds a plain GET that can be refreshed, shared and gone back to.
        return redirect(url_for("game_play", game=activity.slug, topic=topics,
                                words=length, about=instruction or None))

    return page(held=_held_for(topics, length, instruction), refusal=None)


def _cannot_run(activity, topics, heading, explanation=None):
    """The page a round shows instead of dealing one it cannot deal (#266).

    #233's rule is that "can this run?" is answered in the picker; this is the
    part that rule always needed and no shipped game could reach, because the
    picker answers it from *counts* and two questions live below that -- whether
    enough topics are ticked, and whether any of their cards carry what the game
    needs. Neither is a 404 and neither is an empty round: the learner asked for
    something reasonable and is told which of the two it was.
    """
    return render_template("game_cannot_run.html", activity=activity,
                           topics=topics, heading=heading,
                           explanation=explanation)


def _round_stub(activity, topics):
    """The round an activity will have, before it has one (#253).

    Every activity registers now, so its tile, its picker and its Start button
    are all real. Only the round is missing, and this says so — naming the
    ticket that owns it and the selection it would have played, with the picker
    a click away. A tile whose Start led to a 404 would be worse than no tile.
    """
    # Truncated, because "no ?topic=" resolves to *every* visible topic
    # (#248), and naming all twenty-six of them is a wall of text where one
    # line was wanted. Same three-then-ellipsis rule the quiz's title uses.
    shown = ", ".join(topics[:NAMED_TOPICS])
    if len(topics) > NAMED_TOPICS:
        shown += " …"
    return render_template("game_stub.html", activity=activity,
                           topics=topics, topic_summary=shown)


def _typed_the_word(card, given):
    """The answer is the card's headword, typed (#267's rule on both sides).

    Three rounds share this and each says why in its own words: a trailing full
    stop, a doubled space and a hyphen typed as a space are forgiven, and
    nothing touches the middle of a word, because `resigned` is not `resign`.
    """
    return games.same_answer(given, card["word"])


def _rebuilt_the_sentence(card, given):
    """*Rebuild the sentence* is graded against the sentence that travelled
    with the answer, not against anything on the card.

    Which example was drawn and how it was shuffled are both random, so
    nothing here could be rebuilt from the card -- and the assembled string is
    compared rather than chip positions, which is what gets a sentence holding
    `the` twice right for free.
    """
    sentence = request.form.get(f"sentence_{card['id']}", "")
    return bool(sentence) and games.same_answer(given, sentence)


def _chose_the_word(card, given):
    """*Multiple choice* is graded against an option **the server generated**,
    so it compares exactly and deliberately does not use `same_answer()`.

    #267's normalisation folds a hyphen to a space, and #131's distractors are
    slips on the answer: with it, an option generated from `well-being` could
    normalise back onto the answer and be marked right. Nothing a learner types
    reaches this -- a radio submits one of the strings the round put on the
    page -- so the forgiveness that rule exists for has nothing to forgive.
    """
    return given.casefold() == card["word"].casefold()


def _graded_answers(cards, judge):
    """What a graded round asked, what was typed for it, and whether it was
    right -- `[(card, given, correct), ...]` in submission order (#416).

    The six rounds that grade server-side had six copies of this loop: read the
    questions back out of the submitted field names (`games.asked()`, since the
    draw is random and re-drawing on POST would mark answers against words
    nobody saw), take `answer_<id>`, decide. The loop was already identical in
    five of them and *nearly* identical in the sixth -- *Multiple choice* kept
    its own inlined copy, which is the shape #414 cost us: one rule living in
    two places, so a change reaches one of them.

    **Here rather than in `games.py`**, which holds pure round logic with no
    request and no database in it. `games.asked()` stays what it is; this is
    its caller. That is the line #389 already drew when the Wiktionary vet went
    into `app._vetted_pseudowords()` instead of `games.pseudowords()` -- every
    rule in that module is a property of the deck, and reading a form is not.

    **`judge` is a callable because "correct" genuinely differs by game.** The
    typed rounds compare a headword through #267's normalisation; the quiz
    matches one of a stored translation's comma-separated variants and folds a
    Cyrillic yo onto ye; *Multiple choice* compares an option string the
    server itself generated, and must **not** use `same_answer()` -- that folds
    a hyphen to a space, so a generated slip on `well-being` would be marked
    right.

    It is also the seam the next feature needs. #338 records per-learner recall
    from exactly the rounds that grade an answer against a real card, and this
    is that set: one write here rather than one per round, and the two rounds
    that must never write -- *Odd one out*, whose POST is indexed and carries
    no card id, and *Real or fake*, whose items are invented words with no row
    behind them -- are precisely the two that cannot call this.
    """
    by_id = {str(card["id"]): card for card in cards}
    graded = []
    for card in games.asked(request.form, by_id):
        given = (request.form.get(f"answer_{card['id']}") or "").strip()
        graded.append((card, given, bool(judge(card, given))))
    return graded


def _round_played(activity, topics, results, stage="graded"):
    """Leave one `games.log` line for a finished round (#448).

    **Not inside `_graded_answers()`**, though that is the obvious seam, and
    the ticket said why: *Odd one out* and *Real or fake* never reach it --
    one posts indexes rather than card ids, the other invented words with no
    row behind them -- so a line hung there would silently miss two of the
    nine. It is called beside each results render instead, which is the one
    thing every graded round has, and the test suite plays a round of every
    activity to prove none was missed.

    `correct` is counted off the results the page is about to show, so the
    logged score is the displayed score by construction. For *Fill the gap*,
    dealt rather than graded, there is nothing to count and it is left out.
    """
    correct = (sum(1 for r in results if r.get("correct"))
               if stage == "graded" else None)
    applog.round_played(activity.slug, len(topics), len(results),
                        correct=correct, stage=stage,
                        user=web._current_email())


def _scrambled_round(activity, topics):
    """A round of #133: the middle letters shuffled, the learner rebuilds it.

    Typed and graded rather than self-marked, because unlike #235's meanings
    there is exactly one right answer and the page can check it.

    A card whose word cannot be scrambled is **skipped**, not shown unchanged:
    `cat` has no middle, `noon` shuffles to itself, and either would put the
    answer on screen as the question. How many were dropped is said out loud,
    for the same reason the quiz says how many cards lack a translation — the
    picker counts cards, and a round shorter than the count looks like a bug.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    # One card per word (#101 keeps one per word *and part of speech*, and
    # this deck holds true duplicates besides). Before the eligibility rule,
    # so a duplicate never reaches `dropped` -- it is usable, just already
    # asked.
    cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    cards = games.one_per_word(cards)

    if request.method == "POST":
        # Both halves shared since #267: which questions were asked, and what
        # counts as the same answer. The second is the one that changed — a
        # trailing full stop, a doubled space and a hyphen typed as a space are
        # now forgiven, none of which taught a learner anything when marked
        # wrong.
        results = []
        for card, given, correct in _graded_answers(cards, _typed_the_word):
            results.append({
                "word": card["word"],
                "scrambled": request.form.get(f"scrambled_{card['id']}", ""),
                "user_answer": given,
                "correct": correct,
            })
        _round_played(activity, topics, results)
        return render_template(
            "game_scrambled.html", activity=activity, topics=topics,
            questions=None, results=results, words=words,
            score=sum(1 for r in results if r["correct"]), dropped=0)

    # The rule returns the puzzle rather than a yes, which is what keeps one
    # call doing both jobs (#266): asking whether a word can be scrambled and
    # then scrambling it would spend the draw twice and shuffle a different
    # word than it tested.
    usable, dropped = games.playable(
        cards, lambda card: games.scramble_entry(card["word"]))
    if not usable:
        return _cannot_run(
            activity, topics, "No words here can be scrambled yet.",
            "A word needs at least four letters, with two different letters "
            "between the first and the last.")
    questions = [{"id": card["id"], "word": card["word"], "scrambled": puzzle}
                 for card, puzzle in usable]
    return render_template(
        "game_scrambled.html", activity=activity, topics=topics, words=words,
        questions=games.sample(questions, words), results=None, score=None,
        dropped=dropped)


# How many generate-and-check passes a round may make (#389). One is the design
# and the measured normal case; the extra two are for the round where the first
# pass loses a word or two and has to be topped up. Bounded for the reason
# `pseudowords()` is bounded -- a deck has only so many words in it, and the
# alternative to a limit is a page that never renders.
VET_ATTEMPTS = 3


def _vetted_pseudowords(pool, wanted, known):
    """`wanted` invented words, minus the ones a lexicon has heard of (#389).

    The vet lives **here rather than in `games.pseudowords()`**, which has no
    database and no network in it -- that is why it is testable at all, and why
    every filter it holds is a property of the deck. This one is a property of
    English, so it is the route's job.

    Each rejected word is added to `known`, which costs nothing and buys the
    next pass: `pseudowords()` treats `known` as both exact words and stems, so
    a run that offered `defence` will not come back with `defencive`.

    **An unreachable lexicon is not a failed round.** The words generated so far
    are played unvetted -- exactly what this game did before #389 -- because a
    game that will not start is worse than a game that is occasionally wrong,
    and #258's dispute path is still underneath it.
    """
    kept, blocked, rejected = [], set(known), set()
    attempts = 0
    while len(kept) < wanted and attempts < VET_ATTEMPTS:
        attempts += 1
        batch = games.pseudowords(
            pool, wanted - len(kept),
            known=blocked | {w.lower() for w in kept})
        if not batch:
            break                      # the generator has nothing left to give
        found = parsers.wiktionary_pages(batch)
        if found is None:
            kept.extend(batch)         # unreachable: today's behaviour
            break
        kept.extend(w for w in batch if w.lower() not in found)
        rejected |= found
        blocked |= found
        if not found:
            break                      # a clean pass needs no second one
    if rejected:
        applog.invented_vetted(len(kept) + len(rejected), len(rejected),
                               words=rejected, attempts=attempts)
    return kept[:wanted]


def _real_or_fake_round(activity, topics):
    """A round of #132: real words from the deck, mixed with invented ones.

    **The model is trained on the whole visible deck, not the selection.** A
    trigram over twenty words reproduces those twenty words; over five hundred
    it invents. The *real* words still come from the topics the learner ticked
    — the selection chooses what is being revised, and the model only needs a
    bigger sample of English to imitate.

    Both halves share a length floor, which is not tidiness: the fakes cannot
    be shorter than `MIN_INVENTED_LENGTH` (that is where the model collides
    with real English), so real words shorter than it would be a giveaway in
    the opposite direction — every short word on the page would be real.

    Single words only. An expression's spaces would have the model inventing
    phrases, and half the page would be obviously real for having them.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))

    if request.method == "POST":
        results = []
        for key in sorted(request.form, key=_answer_index):
            if not key.startswith("answer_"):
                continue
            index = key[len("answer_"):]
            word = request.form.get(f"item_{index}", "")
            really = request.form.get(f"real_{index}") == "1"
            said = request.form.get(key) == "real"
            results.append({"word": word, "real": really, "said_real": said,
                            "correct": said == really})
        _round_played(activity, topics, results)
        return render_template(
            "game_real_or_fake.html", activity=activity, topics=topics,
            items=None, results=results, words=words, dropped=0,
            score=sum(1 for r in results if r["correct"]))

    def usable(card):
        word = (card.get("word") or "").strip()
        return (word.isalpha() and len(word) >= games.MIN_INVENTED_LENGTH
                and " " not in word)

    in_selection = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    kept, dropped = games.playable(in_selection, usable)
    # Deduplicated: #101 keeps one card per word *and part of speech*, so a
    # word that is both a noun and a verb is two cards, and the same word twice
    # on the page is a free mark and looks like a mistake.
    #
    # **Not counted as dropped**, though it was at first. `dropped` is printed
    # beside the activity's `needs` -- "not usable for this game, which needs a
    # single word of seven letters or more" -- and a duplicate meets that
    # perfectly well. Counting it would put a large number in front of a reason
    # that is not the real one.
    selected = [card["word"]
                for card in games.one_per_word(card for card, _ in kept)]
    everything = utils.get_flashcards_by_topics(
        games.visible_topic_names(_visible_sections()), web.cards_owner_filter(),
        **web.viewer())

    wanted_fake = words // 2
    # Everything the deck knows is English, plus everything a learner has
    # already disputed and won (#258). The second set is small and grows from
    # real disagreements -- and a word that has been settled once must never be
    # offered as invented again, which is the whole reason it is written down.
    known = games.vocabulary(everything) | utils.confirmed_words()
    # Vetted against a lexicon before the round rather than after a dispute
    # (#389). Measured over 60 rounds of this deck, one round in three offered
    # a real English word as invented -- `defence`, `provision`, `edition` and
    # `version` among them, and `bailment`, which is the word that opened #258.
    fakes = _vetted_pseudowords(
        [c["word"] for c in everything], wanted_fake, known)
    reals = games.sample(selected, words - len(fakes))

    if not reals:
        return _cannot_run(
            activity, topics,
            "There aren't enough real words here for a round yet.",
            f"This game needs words of {games.MIN_INVENTED_LENGTH} letters or "
            "more, written as a single word, in the topics you chose.")
    items = ([{"word": w, "real": True} for w in reals]
             + [{"word": w, "real": False} for w in fakes])
    random.shuffle(items)
    return render_template(
        "game_real_or_fake.html", activity=activity, topics=topics,
        items=items, results=None, score=None, words=words, dropped=dropped,
        real_count=len(reals), fake_count=len(fakes),
        games_min=games.MIN_INVENTED_LENGTH)


def _gap_translation(prefs):
    """Which translation #235 reveals, as `(field, label)` or `(None, None)`.

    The identity's **`quiz_lang`** (#113) rather than the card deck's
    Ukrainian-first rule, because this is a question being answered rather than
    a card being browsed — and #113 already exists, already defaults to
    Ukrainian, and already knows what to do when the preferred language is
    hidden in Settings (#46/#79). Two rules for one question drift apart, so
    this reuses that one instead of inventing a second.

    Both languages hidden is not an error: the reveal falls back to the
    explanation, and failing that to the word alone, which was the answer.
    """
    visible = _visible_quiz_langs(prefs)
    if not visible:
        return None, None
    preferred = QUIZ_LANG_CODES.get(prefs["quiz_lang"], "ukr")
    code = preferred if preferred in visible else next(iter(visible))
    return f"translation_{code}", visible[code]


def _fill_the_gap_round(activity, topics):
    """A round of #235: a word cut out of one of its own example sentences.

    **Eligibility is asked of the cards, not of the picker.** `min_cards` is the
    cheap check the picker can answer from its counts; whether a card has an
    example containing its own headword can only be answered here, with the
    cards in hand. Of production's 503 cards, 86 have no English examples at
    all, and a card whose examples never use its own word is just as unplayable
    — so a topic can be perfectly full and still yield a short round.

    Nothing here is written down. The score is the learner's own ticking, held
    in the page and gone when they leave it (#233's rule about a game that
    records a score does not apply to a game that records nothing).
    """
    prefs = web.current_settings()
    wanted = prefs["gapped_deck_size"]
    field, label = _gap_translation(prefs)
    # #334. `none` is the default and reproduces #235 exactly, so a learner who
    # never opens the control sees the game they have always seen.
    hint = _round_hint(activity)

    # One card per word, before the eligibility rule so a duplicate never
    # reaches `dropped` (#272's rule). Shuffled first, so which of a word's
    # cards survives is not always the lowest id.
    cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    random.shuffle(cards)
    cards = games.one_per_word(cards)

    # The rule returns the gapped sentence, not a yes: finding an example that
    # contains its own headword and cutting the word out of it are one question
    # (#266), and asking twice would draw a different example the second time.
    usable, dropped = games.playable(
        cards,
        lambda card: games.gapped_example(card.get("examples_en"),
                                          (card.get("word") or "").strip(),
                                          hint=hint))
    if not usable:
        return _cannot_run(
            activity, topics,
            "No cards here have an example that uses their own word.",
            "This game hides a word inside one of its own example sentences, "
            "so a card with no examples — or none that use the word — sits "
            "this one out.")

    # Deduping the **gapped sentence** as well was tried and dropped. It is a
    # blunt rule: two different words in the same frame gap to the same string,
    # and collapsing those loses questions that are genuinely distinct. Across
    # the deck's 917 gapped sentences exactly one pair collides -- `campaign`
    # and `manifesto` both give "an election ______" -- which is not worth the
    # cost, and one card per word already fixes the reported repetition, since
    # the two `tip` cards are one word.
    questions = []
    for card, sentence in usable[:wanted]:
        word = (card.get("word") or "").strip()
        questions.append({
            "word": word,
            "pos": card.get("pos"),
            "sentence": sentence,
            "explanation_en": card.get("explanation_en"),
            "explanation_source": card.get("explanation_source"),
            # The gapped sentence is one of the card's own examples, so this
            # page shows both kinds of dictionary text and needs both credits.
            "examples_source": card.get("examples_source"),
            "translation": card.get(field) if field else None,
            "translation_label": label,
        })

    # Logged at the deal (#448), because this is the only moment the server
    # sees: the round never submits, and the score lives in the page.
    _round_played(activity, topics, questions, stage="dealt")
    return render_template(
        "game_fill_the_gap.html", activity=activity, topics=topics,
        cards=questions, wanted=wanted, dropped=dropped, hint=hint)


# Distinct words a selection needs before it can supply its own wrong answers
# (#130). Three questions' worth of distractors plus the answers they belong
# to: below that, the same handful of words comes back every question. Twelve
# is a judgement about when repetition stops being noticeable, not a measured
# number -- the deck's own topics hold twenty each, so this only ever fires on
# a small hand-made one.
MIN_SELF_SUFFICIENT_POOL = 12


def _multiple_choice_round(activity, topics):
    """A round of #130: a translation, and four English words to choose from.

    The prompt is the card's Ukrainian or Russian translation and the answer is
    its English headword, which is the one direction this ships with. The
    reverse — an English word above four Ukrainian answers — is not built here:
    409 of the deck's 502 Ukrainian translations are comma-separated lists
    rather than words, so four of them stacked as options is unreadable, and
    that is a rendering question nobody has answered yet.

    **Which language the prompt is in is the quiz's question, answered by the
    quiz's code.** `picks_language` puts the choice in the picker and
    `_quiz_lang()` resolves it, including the case where the preferred language
    is hidden in Settings (#46/#79). A second rule for the same question would
    drift from the first inside a month.
    """
    prefs = web.current_settings()
    langs = _visible_quiz_langs(prefs)
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    page = {"activity": activity, "topics": topics, "words": words,
            "lang_name": None}

    if not langs:
        # Both languages hidden (#46/#79). There is no prompt to show, so there
        # is no round. Since #266 that is the shared page rather than a branch
        # in this game's template: "the selection cannot produce a round" is one
        # situation with several causes, and each game rendering it its own way
        # is what #266 set out to stop.
        return _cannot_run(
            activity, topics, "There is no language to be tested in.",
            "This game shows a translation and asks for the English word, so "
            "it needs Ukrainian or Russian to be visible. Enable at least one "
            "in Settings.")

    lang = _quiz_lang(prefs, langs)
    field = f"translation_{lang}"
    page["lang_name"] = QUIZ_LANGS[lang]

    in_selection = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    usable, untranslated = games.playable(
        in_selection,
        lambda card: bool(card.get(field)) and bool((card.get("word") or "").strip()))
    answerable = [card for card, _ in usable]

    if request.method == "POST":
        # Grade the questions that were asked, read back from the submitted
        # field names — the quiz's rule, and here for the same reason: the
        # draw is random and the distractors are generated per round, so a
        # fresh draw would mark answers against options nobody saw.
        #
        # Graded against `answerable` rather than the deduplicated draw, so
        # grading never depends on the dedupe landing the same way twice.
        results = []
        for card, given, correct in _graded_answers(answerable,
                                                    _chose_the_word):
            results.append({
                "prompt": card[field],
                "word": card["word"],
                "pos": card.get("pos"),
                "user_answer": given,
                "correct": correct,
            })
        _round_played(activity, topics, results)
        return render_template(
            "game_multiple_choice.html", questions=None, results=results,
            score=sum(1 for r in results if r["correct"]),
            dropped=0, unbuildable=0, **page)

    # One card per English word -- asking both `work` the noun and `work` the
    # verb would show the same four options twice, the second a free mark.
    unique = games.one_per_word(answerable)
    pool = [card["word"].strip() for card in unique]

    # The wider deck, fetched **only when the selection cannot furnish its own
    # wrong answers**. A one-topic selection of five cards would otherwise
    # offer the same four words in a different order every question, which a
    # learner spots immediately. Everything larger already has more real words
    # than a round can use, and a second full-deck query per round to prove
    # that is a query nobody needs.
    spare, wider = [], in_selection
    if len(pool) < MIN_SELF_SUFFICIENT_POOL:
        wider = utils.get_flashcards_by_topics(
            games.visible_topic_names(_visible_sections()),
            web.cards_owner_filter(), **web.viewer())
        spare = [(c.get("word") or "").strip() for c in wider
                 if (c.get("word") or "").strip()]

    # Real English, used to throw away a "typo" that landed on a real word —
    # `design` is one slip from `resign` (#132 built this for the same job).
    known = games.vocabulary(wider)

    questions = []
    for card in games.sample(unique, words):
        options = games.question_options(card["word"].strip(), pool,
                                         spare=spare, known=known)
        if options is None:
            continue
        questions.append({
            "id": card["id"],
            "prompt": card[field],
            "pos": card.get("pos"),
            "options": options,
        })

    if not questions:
        return _cannot_run(
            activity, topics,
            "There aren't enough words here for a round yet.",
            "Each question needs four different answers, so this game needs at "
            f"least four cards with a {page['lang_name']} translation in the "
            "topics you chose.")
    return render_template(
        "game_multiple_choice.html", questions=questions, results=None,
        score=None, dropped=untranslated,
        unbuildable=min(len(unique), words) - len(questions), **page)


def _listen_and_type_round(activity, topics):
    """A round of #272: the word is spoken, the learner writes it.

    The first activity on the site that makes a sound. Every other one is a
    reading exercise — a learner can hold five hundred cards and never once
    hear an English word, which #236 called a missing sense rather than a
    missing game.

    **Nothing on the server touches audio.** The word travels to the page and
    #268's `speech.js` speaks it in the browser: no round trip, no key, no
    stored audio. Which is also why *grading stays here*. The audio being in
    the browser is not a reason to move correctness there, and moving it would
    put the one automatically testable half of this game out of reach too.

    Whether the round can actually run is the one place #233's rule breaks
    down: the picker answers "can this activity run" everywhere else, but no
    card count can tell the server whether the visitor's browser owns an
    English voice. So the page probes on load and says so itself, which is
    written down as a deliberate exception in #268 rather than left to look
    like an oversight.
    """
    prefs = web.current_settings()
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    field, label = _gap_translation(prefs)
    cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())

    if request.method == "POST":
        results = []
        for card, given, correct in _graded_answers(cards, _typed_the_word):
            results.append({
                "word": card["word"],
                "pos": card.get("pos"),
                "explanation_en": card.get("explanation_en"),
                "explanation_source": card.get("explanation_source"),
                "translation": card.get(field) if field else None,
                "translation_label": label,
                "user_answer": given,
                # #267's rule, so a trailing full stop and a hyphen typed as a
                # space are forgiven. A homophone is **not**: `their` for
                # `there` is wrong, and that is the exercise rather than a
                # defect -- telling them apart by ear is the whole point, and
                # the results say which word was meant.
                "correct": correct,
            })
        _round_played(activity, topics, results)
        return render_template(
            "game_listen_and_type.html", activity=activity, topics=topics,
            questions=None, results=results, words=words, dropped=0,
            score=sum(1 for r in results if r["correct"]))

    usable, dropped = games.playable(
        cards, lambda card: games.speakable(card.get("word")))
    if not usable:
        return _cannot_run(
            activity, topics,
            "There are no words here that can be read aloud.",
            "This game dictates a headword, so it needs cards whose word is "
            "letters — an abbreviation or a bracketed note is a poor thing to "
            "hear and worse to type back.")

    # One card per word. #101 keeps a card per word *and part of speech*, so
    # `work` the noun and `work` the verb are two cards -- and dictating the
    # same word twice is a free second mark and sounds like a mistake.
    # **Not counted as dropped.** `dropped` is printed beside the activity's
    # `needs`, so every card in that number is one the game could not use for
    # the stated reason -- and a duplicate is perfectly usable, it has just
    # already been asked. Adding it would make the sentence say 153 cards have
    # no headword a voice can read, which is false and alarming.
    unique = [{"id": card["id"], "word": card["word"].strip()}
              for card in games.one_per_word(card for card, _ in usable)]

    return render_template(
        "game_listen_and_type.html", activity=activity, topics=topics,
        questions=games.sample(unique, words), results=None, score=None,
        words=words, dropped=dropped)


def _topic_sections(sections):
    """`{topic: section}` from `utils.get_topics_by_section()`'s shape (#269).

    Only used to *prefer* an intruder from another section, so a topic missing
    from it costs nothing -- the preference simply does not fire for that one.
    """
    return {topic: name for name, topics in sections for topic, _ in topics}


def _odd_one_out_round(activity, topics):
    """A round of #269: three words from one topic, and a stranger.

    The only game built on the deck's own **structure** rather than on what a
    card holds -- #207 and #215 turned a topic from a text label into a real
    grouping with sections, and this asks a question out of that.

    Every card qualifies, which is why this is the one wave-two game with no
    card-level rule (#266): it needs a word and the topic it lives in, and
    every card has both. What it needs instead is #266's other half, two
    ticked topics, and `game_play` has already enforced that by the time this
    runs.

    Nothing here is written to the database.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))

    if request.method == "POST":
        # Indexed rather than keyed by card id, like real-or-fake and for the
        # same reason: a question is four words from two topics rather than one
        # row, so there is nothing to look up. Everything the results need
        # travels in hidden fields -- the draw is random and could not be
        # rebuilt from the selection.
        results = []
        for key in sorted(request.form, key=_answer_index):
            if not key.startswith("answer_"):
                continue
            index = key[len("answer_"):]
            shown = request.form.getlist(f"word_{index}")
            answer = request.form.get(f"intruder_{index}", "")
            given = request.form.get(key, "")
            results.append({
                "words": shown,
                "answer": answer,
                "given": given,
                "home": request.form.get(f"home_{index}", ""),
                "intruder_topic": request.form.get(f"from_{index}", ""),
                "correct": bool(answer) and given == answer,
            })
        _round_played(activity, topics, results)
        return render_template(
            "game_odd_one_out.html", activity=activity, topics=topics,
            questions=None, results=results, words=words, dropped=0,
            score=sum(1 for r in results if r["correct"]))

    cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    by_topic = games.by_topic(cards)
    questions = games.odd_one_out_round(
        by_topic, words, _topic_sections(_visible_sections()))

    if not questions:
        return _cannot_run(
            activity, topics,
            "These topics can't make a question yet.",
            "A question is three words from one topic and a stranger from "
            "another, so one of the topics you ticked needs at least three "
            "cards and another needs a word it does not already share.")

    return render_template(
        "game_odd_one_out.html", activity=activity, topics=topics,
        questions=questions, results=None, score=None, words=words,
        dropped=0, short=words - len(questions))


def _spell_it_round(activity, topics):
    """A round of #270: the meaning and how the word starts, type the word.

    The direction the site does not otherwise test. The quiz goes from an
    English word to a translation, the deck shows a word and reveals its
    meaning, #235 hides a word inside its own sentence — nothing goes from
    *meaning* to *spelling*, which is the harder direction and the one that
    fails in an exam.

    The hint mode is settled **before** the draw, because it decides
    eligibility as well as the mask: with the last letter shown, a four-letter
    word is two-thirds given, so that mode asks for a longer one.

    Nothing is written to the database.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    hint = _round_hint(activity)

    if request.method == "POST":
        results = []
        cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
        for card, given, correct in _graded_answers(cards, _typed_the_word):
            results.append({
                "word": card["word"],
                "pos": card.get("pos"),
                # Shown in full now — the round is over, and a learner who got
                # it wrong should read the meaning against the actual word.
                "explanation_en": card.get("explanation_en"),
                "explanation_source": card.get("explanation_source"),
                "user_answer": given,
                # #267's rule: capitals, a doubled space, a trailing full stop
                # and a hyphen typed as a space are forgiven. `resigned` for
                # `resign` is **wrong** — a different word, and this is the one
                # game where being approximately right is what is being tested
                # against.
                "correct": correct,
            })
        _round_played(activity, topics, results)
        return render_template(
            "game_spell_it.html", activity=activity, topics=topics,
            questions=None, results=results, words=words, hint=hint,
            dropped=0, score=sum(1 for r in results if r["correct"]))

    # One card per word (#101 keeps one per word *and part of speech*, and
    # this deck holds true duplicates besides). Before the eligibility rule,
    # so a duplicate never reaches `dropped` -- it is usable, just already
    # asked.
    cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    cards = games.one_per_word(cards)
    usable, dropped = games.playable(
        cards,
        lambda card: bool((card.get("explanation_en") or "").strip())
        and games.spellable(card.get("word"), hint))

    if not usable:
        return _cannot_run(
            activity, topics,
            "No words here can be spelled out yet.",
            "This game shows what a word means and asks you to spell it, so it "
            "needs cards with an English explanation and a headword of at "
            f"least {games.MIN_SPELLED[hint]} letters.")

    questions = []
    for card, _ in games.sample(usable, words):
        word = card["word"].strip()
        questions.append({
            "id": card["id"],
            "pos": card.get("pos"),
            "mask": games.mask_word(word, hint),
            # Masked with the same matcher #235 and #237 use. Oxford's
            # explanations routinely contain the headword or an inflection of
            # it, and "the act of resigning from a position" would print the
            # answer above the dashes.
            "explanation": games.mask_in_text(card["explanation_en"], word,
                                              hint),
        })

    return render_template(
        "game_spell_it.html", activity=activity, topics=topics,
        questions=questions, results=None, score=None, words=words,
        hint=hint, dropped=dropped)


def _rebuild_the_sentence_round(activity, topics):
    """A round of #271: one example sentence, its words shuffled.

    **Not #133 with bigger pieces.** Scrambled trains spelling; this trains
    *word order*, which is where Ukrainian- and Russian-speaking learners
    actually lose marks — both first languages permit orders English does not,
    so a sentence that feels natural to write comes out wrong. It is the one
    exercise in either wave aimed squarely at that gap.

    **Tap to place, not drag and drop.** Drag is the obvious interaction and
    the expensive one: pointer events, a touch fallback and a keyboard path,
    and it is the part most likely to be subtly broken on a phone. The chips
    are ordinary `<button>` elements, so a mouse, a finger and a keyboard all
    work with no code for any of them.

    **Graded on the server**, from the assembled string in a hidden field —
    the interaction being in the browser is not a reason to move correctness
    there.

    Nothing is written to the database.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    # One card per word (#101 keeps one per word *and part of speech*, and
    # this deck holds true duplicates besides). Before the eligibility rule,
    # so a duplicate never reaches `dropped` -- it is usable, just already
    # asked.
    cards = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    cards = games.one_per_word(cards)

    if request.method == "POST":
        results = []
        for card, given, correct in _graded_answers(
                cards, _rebuilt_the_sentence):
            # The sentence travels with the answer: which example was drawn and
            # how it was shuffled are both random, so nothing here could be
            # rebuilt from the card.
            sentence = request.form.get(f"sentence_{card['id']}", "")
            results.append({
                "word": card["word"],
                "sentence": sentence,
                "examples_source": card.get("examples_source"),
                "given": given,
                # **The assembled string, not chip positions.** That falls out
                # of the duplicate-token case and gets it right for free: a
                # sentence containing `the` twice has two genuinely
                # interchangeable chips, and grading by position would mark one
                # of two identical words wrong for sitting in the other's slot.
                # #267's normalisation on both sides, so a doubled space
                # between chips cannot fail a correct sentence.
                "correct": correct,
            })
        _round_played(activity, topics, results)
        return render_template(
            "game_rebuild_the_sentence.html", activity=activity, topics=topics,
            questions=None, results=results, words=words, dropped=0,
            score=sum(1 for r in results if r["correct"]))

    usable, dropped = games.playable(
        cards, lambda card: games.rebuildable(card.get("examples_en")))

    if not usable:
        return _cannot_run(
            activity, topics,
            "None of these cards has a sentence to rebuild.",
            "This game shuffles the words of a real example sentence, so it "
            f"needs cards with an English example of {games.SENTENCE_MIN} to "
            f"{games.SENTENCE_MAX} words.")

    questions = []
    for card, (sentence, chips) in games.sample(usable, words):
        questions.append({
            "id": card["id"],
            "word": card["word"],
            "sentence": sentence,
            "chips": chips,
            # This game *is* a card's example sentence, so it shows dictionary
            # text with no definition beside it -- and it is the examples'
            # credit that belongs here, not the explanation's (#390).
            "examples_source": card.get("examples_source"),
        })

    return render_template(
        "game_rebuild_the_sentence.html", activity=activity, topics=topics,
        questions=questions, results=None, score=None, words=words,
        dropped=dropped)


def _answer_index(key):
    """Sort answer_<n> fields back into the order they were asked.

    Real-or-fake is the one typed-answer-shaped round #267 deliberately left
    out of `games.asked()`. Its items are **invented words with no row behind
    them**, so its fields are keyed by position rather than by card id, and
    there is nothing to look up. Folding it in would mean telling the shared
    helper which of two shapes it is being used in, and a helper that has to be
    told that is two functions wearing a hat.
    """
    tail = key.rsplit("_", 1)[-1]
    return int(tail) if tail.isdigit() else -1

# slug -> the view that renders one round, as `f(activity, topics)`. Every
# registered activity has an entry; a game ticket replaces its stub with the
# real round and touches nothing else.

GAME_ROUNDS = {activity.slug: _round_stub
               for activity in games.ACTIVITIES.values()
               if activity.kind in games.GAMES_URL_KINDS}
GAME_ROUNDS["scrambled"] = _scrambled_round
GAME_ROUNDS["real_or_fake"] = _real_or_fake_round
GAME_ROUNDS["read_a_text"] = _read_a_text_round
GAME_ROUNDS["fill_the_gap"] = _fill_the_gap_round
GAME_ROUNDS["multiple_choice"] = _multiple_choice_round
GAME_ROUNDS["listen_and_type"] = _listen_and_type_round
GAME_ROUNDS["odd_one_out"] = _odd_one_out_round
GAME_ROUNDS["spell_it"] = _spell_it_round
GAME_ROUNDS["rebuild_the_sentence"] = _rebuild_the_sentence_round


WORKSHEET_SLUG = "read_a_text"


@app.route("/games/read_a_text/worksheet")
def worksheet():
    """A printable gap-fill sheet made from the text already held (#340).

    **Outside `GAME_ROUNDS` deliberately.** The chassis dispatches
    `/games/<slug>` (the picker) and `/games/<slug>/play` (a round); a
    worksheet is neither, and registering it as a round would put it in the
    picker and on the front-page tile, both of which are wrong.

    **GET only, and it never reaches `textgen.generate()`.** It re-renders the
    passage in the session, so #237's ceilings, its anonymous nudge and
    `_generation_refusal()` are untouched and no new guard is needed. That is
    the property to keep: the moment this route can spend, it needs all of
    #237's accounting and it is no longer a printable page.

    It asks for **no topic selection**. The round takes one because it may
    generate; this takes the text the learner is holding, whatever produced it,
    which is why `_held_generation()` is right here and `_held_for()` is not.
    """
    activity = _reachable_activity(WORKSHEET_SLUG)
    if activity is None:
        abort(404)
    held = _held_generation()
    hint = _round_hint(activity)
    items, answers = [], []
    title_items, title_answers = [], []
    if held and held.get("text"):
        # The title is gapped too, and numbered first. `textgen.mark()` counts
        # a word in the title as used (#315), so leaving the title intact would
        # print an answer two lines above its own blank -- the exact failure
        # #235's "never show the sentence ungapped" rule exists to prevent.
        title_items, title_answers = games.worksheet_blanks(
            held.get("title_segments") or [], hint)
        # Numbering continues from the title, so the key reads 1..n once.
        items, answers = games.worksheet_blanks(
            held.get("segments") or [], hint, start=len(title_answers))
        answers = title_answers + answers
    # The way back, built from what produced the held text rather than from
    # this request, which carries no selection of its own (#340).
    back_url = url_for("game_play", game=activity.slug,
                       topic=(held or {}).get("topics") or None,
                       words=(held or {}).get("length") or None,
                       about=(held or {}).get("instruction") or None)
    return render_template(
        "worksheet.html", activity=activity, held=held, hint=hint,
        back_url=back_url,
        hints=games.GAP_HINTS, hint_labels=games.HINT_LABELS,
        title_items=title_items, items=items, answers=answers)


@app.route("/games/<game>/play", methods=["GET", "POST"])
def game_play(game):
    """A round of one game over the selected topics (#250).

    The selection is resolved here rather than in each game, so every game
    inherits #233's rules for free: page order, topics that have since gone
    dropped in silence, and **no `topic` parameter meaning the whole visible
    deck** — which is what keeps a bare link to this URL, the kind the topic
    page's activity row will build, meaningful.

    Playing also remembers the selection, so the picker opens on it next time.
    """
    activity = _reachable_activity(game)
    round_view = GAME_ROUNDS.get(game)
    if activity is None or round_view is None:
        abort(404)
    # **Only a request that named topics expresses a preference** (#342).
    #
    # `resolve_selection()` turns no `topic` parameter into the whole visible
    # deck, which is the right answer for *this round* and keeps a bare link
    # meaningful. Remembering that expansion is a different claim: it rewrites
    # "I named no topics" as "I chose all of them", and the picker then opens
    # fully ticked forever after -- inventing a preference on the learner's
    # behalf, which is exactly what `remembered_selection()` refuses to do.
    #
    # An explicit *Select all* still submits every name, so a learner who
    # really did choose everything still gets it back.
    requested = request.args.getlist("topic")
    topics = games.resolve_selection(
        requested, games.visible_topic_names(_visible_sections()))
    if requested:
        games.remember_selection(session, topics)
    # The round length is remembered **beside the selection, and for the same
    # reason** (#233): the picker opens on what was played last.
    #
    # Here rather than in each round because no game ever wrote it back --
    # only `/quiz` did. A number typed into a game's picker was read once and
    # then forgotten, so the box reopened on whatever the last quiz had stored,
    # and a learner who never takes the quiz could not change it at all.
    games.remember_word_count(
        session,
        games.word_count(request.args.get("words"),
                         games.remembered_word_count(session, activity.words),
                         activity.words),
        activity.words)
    # Checked here rather than in each round, so every game inherits it, and
    # checked at all because the picker is not the only way in: a hand-typed or
    # shared `?topic=` reaches this URL without passing the Start button that
    # would have been disabled (#266).
    if not topics:
        # No topics at all, which is its own situation rather than a shortfall:
        # every activity needs at least one, so saying "needs at least 1 topics"
        # would be arithmetic where a sentence was wanted.
        return _cannot_run(
            activity, topics, "No topics are selected.",
            "Pick the topics you want to play over and this round will deal "
            "from them.")
    if len(topics) < activity.min_topics:
        return _cannot_run(
            activity, topics,
            f"{activity.name} needs at least {activity.min_topics} topics, "
            f"and {'only one is' if len(topics) == 1 else 'none is'} selected.",
            activity.too_few_topics)
    return round_view(activity, topics)


def _quiz_lang(prefs, langs):
    """Which language this quiz runs in (#113).

    Without an explicit ?lang=, the identity's preference; the in-page switch
    still overrides it, and a preference for a language hidden in Settings
    (#46/#79) falls back to a visible one.
    """
    default_lang = QUIZ_LANG_CODES.get(prefs["quiz_lang"], "ukr")
    lang = request.args.get("lang") or default_lang
    if lang not in langs:
        lang = default_lang if default_lang in langs else next(iter(langs))
    return lang


# How many of a selection are named under a quiz's title before the rest
# become an ellipsis. Three fits one line on a phone, which is the constraint;
# the count in the title is what answers "how many" exactly.
NAMED_TOPICS = 3


def _topic_summary(topics):
    """The topic names to print under a quiz's title, or **None**.

    None for a single topic, because the title already names it — "Quiz: Work"
    above a line reading "Work" is the same word twice. The title of a several-
    topic run says how many, which is the one thing this line cannot: it is
    truncated, and a truncated list that also had to be countable would have to
    show every name.
    """
    if len(topics) < 2:
        return None
    shown = ", ".join(topics[:NAMED_TOPICS])
    return f"{shown} …" if len(topics) > NAMED_TOPICS else shown


def _run_quiz(topics, heading, self_url, back, words):
    """Render or grade a quiz over `topics` — one of them or several (#250).

    `self_url(lang=...)` builds this quiz's own URL, because the language
    switch, the form action and "Try again" all need it and only the caller
    knows which of the two route shapes it is. `back` is the crumb link, which
    is the topic page for one topic and the picker for a selection.

    Everything about the quiz itself is unchanged (#233 asked for exactly one
    change): typed answers, the same grading, `quiz_lang`, and skipping cards
    with no translation in the chosen language. Several topics grade as one run
    because they are one list of cards by the time they get here.
    """
    prefs = web.current_settings()
    langs = _visible_quiz_langs(prefs)
    # `activity` rides along for #266's shared shortfall sentence, which reads
    # `needs` off it. The quiz's own page predates the chassis and never needed
    # it before -- it takes a `heading` because /quiz/<topic> names one topic
    # and /quiz names several, which the declaration cannot say.
    common = {"heading": heading, "self_url": self_url, "back": back,
              "langs": langs, "topic_summary": _topic_summary(topics),
              "activity": games.ACTIVITIES["quiz"]}
    if not langs:
        # Both languages hidden in Settings (#46/#79) — nothing to quiz on.
        return render_template(
            "quiz.html", cards=[], lang=None, lang_name=None,
            results=None, dropped=0, **dict(common, langs={}))

    lang = _quiz_lang(prefs, langs)
    field = f"translation_{lang}"
    # A card with no translation in this language cannot be asked, so it is
    # dropped *before* the draw — the sample can only contain answerable words.
    # How many were dropped is worth saying, though: the picker counts cards,
    # not cards with a Ukrainian translation, so a learner who ticked 41 and
    # was asked 20 has no way to tell that from the word limit. 74 of the 569
    # cards in production have no Ukrainian and 38 no Russian, so this is a
    # number people will actually meet.
    in_selection = utils.get_flashcards_by_topics(topics, web.cards_owner_filter(), **web.viewer())
    usable, untranslated = games.playable(in_selection,
                                          lambda card: card.get(field))
    cards = [card for card, _ in usable]

    results = score = None
    if request.method == "POST":
        # Grade the questions that were **asked**, not a fresh draw — the rule
        # and the reasoning now live in games.asked() (#267), which scrambled
        # and wave two's typed rounds share.
        #
        # The *comparison* deliberately stays on this path, in
        # `_matched_a_variant()`. A quiz answer is matched
        # against a stored translation, which is a comma-separated list of
        # synonyms (`_answer_variants()`) and can carry a Cyrillic `ё` — both
        # facts about a translation and neither one about an English headword,
        # so games.normalise_answer() has no business knowing them.
        graded = _graded_answers(
            cards, lambda card, given: _matched_a_variant(card, given, field))
        # Narrowed to what was actually asked, because the template's
        # empty-state guard reads `cards` and a POST that graded nothing is
        # not a quiz with questions on it.
        cards = [card for card, _given, _correct in graded]
        results = []
        for card, user_answer, correct in graded:
            results.append({
                "word": card["word"],
                "pos": card.get("pos"),
                "user_answer": user_answer,
                "expected": card[field],
                "correct": correct,
            })
        score = sum(1 for r in results if r["correct"])
        _round_played(games.ACTIVITIES["quiz"], topics, results)
    else:
        # A round is `words` questions drawn uniformly from every card in the
        # selection — so a topic with 36 cards contributes more of them than
        # one with 20, which is what drawing from the words rather than from
        # the topics means. Fewer cards than asked for is simply a shorter
        # round.
        cards = games.sample(cards, words)

    return render_template(
        "quiz.html", cards=cards, lang=lang, lang_name=QUIZ_LANGS[lang],
        results=results, score=score, dropped=untranslated, **common)


@app.route("/quiz/<topic>", methods=["GET", "POST"])
def quiz(topic):
    """Quiz on one topic — the original route, unchanged and still linked.

    Three templates build `url_for('quiz', topic=...)`, and #233 requires that
    none of them break. Which is why the several-topic quiz is a **separate
    endpoint** rather than a second rule on this one: with both shapes on one
    endpoint, `url_for` has to choose between the path converter and a repeated
    query parameter, and it picks the converter — making the multi-topic URL
    unbuildable.
    """
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    return _run_quiz(
        [topic],
        heading=topic,
        self_url=lambda lang: url_for("quiz", topic=topic, lang=lang,
                                      words=words),
        back=(url_for("flashcards", topic=topic), f"Flashcards: {topic}"),
        words=words,
    )


@app.route("/quiz", methods=["GET", "POST"])
def quiz_topics():
    """The picker, or a quiz over the topics it submitted (#250).

    One URL doing both, because #233 specified `/quiz` for the picker and
    `/quiz?topic=A&topic=B` for the run. **No `topic` parameter means the
    picker**, which is the one place this differs from a game: a game's round
    has its own `/play` URL, so there an absent parameter can safely mean the
    whole deck. Here the two would be the same URL, and a picker nobody can
    reach is worse than a shortcut nobody has asked for — ticking "Select all"
    is the way to quiz on everything.
    """
    requested = request.args.getlist("topic")
    if not requested:
        return _render_picker(
            games.ACTIVITIES["quiz"], url_for("quiz_topics"))

    topics = games.resolve_selection(
        requested, games.visible_topic_names(_visible_sections()))
    words = games.word_count(request.args.get("words"),
                             games.remembered_word_count(session))
    games.remember_selection(session, topics)
    games.remember_word_count(session, words)
    heading = topics[0] if len(topics) == 1 else f"{len(topics)} topics"
    return _run_quiz(
        topics,
        heading=heading,
        self_url=lambda lang: url_for("quiz_topics", topic=topics, lang=lang,
                                      words=words),
        back=(url_for("quiz_topics"), "Choose topics"),
        words=words,
    )
