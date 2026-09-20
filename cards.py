"""The card routes: the deck, the pages that read it, and everything that
writes to it (#418).

The second-largest block in the ticket, and the one holding `_save_and_log()`
-- the single path every card write in this project goes through. That is why
it moved **before** `chat.py`, reversing the order the ticket gives: Mykola's
card saver is a card save, so `chat.py` has to be able to import this module,
and a module cannot import one that does not exist yet.

The dependency therefore runs `web.py` <- `icons.py` <- `cards.py` <-
`chat.py` <- `app.py`, and nothing in it imports `app.py`.

#406's topic builder is here rather than in a module of its own: it proposes a
word list and then saves cards through `_save_and_log()` like everything else,
so it is a way of *making* cards rather than a feature beside them.
"""

import json
import time

from flask import (
    Response,
    abort,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    stream_with_context,
    url_for,
)

import applog
import icons
import parsers
import topicgen
import utils
import web
from parsers import lookup_word, parse_notes_preview
from web import app


def _save_and_log(entry, source, fills=None, allow_duplicate=False):
    """Save one card and record the outcome in logs/cards.log (#30).

    Every card written by the app goes through here or through the explicit
    applog calls next to the other utils.save_flashcard() call sites — keep it that
    way when adding a new save path.

    Returns True when a row was actually written (False = duplicate).

    Being the single funnel is also what makes #89 one change instead of four:
    every save path records its owner here.

    It is also where #125 is enforced, for the same reason: a save path that
    forgets to ask `can_add_cards()` first fails loudly here instead of
    quietly writing. Callers that face a person check beforehand, so the
    visitor gets the sign-in prompt rather than an error.
    """
    refusal = web.add_refusal()
    if refusal:
        applog.card_add_denied(entry, source=source, user=web._current_email(),
                               reason="blocked" if web.is_blocked() else "anonymous")
        raise PermissionError(refusal)
    # Which card this one is about to sit beside (#379), read *before* the
    # write while "the card with this word and pos" still names exactly one
    # row. Only for a deliberate duplicate, and only so the log line can say
    # what it duplicated -- a second row is otherwise indistinguishable later
    # from the accident #101 exists to prevent.
    alongside = None
    if allow_duplicate:
        try:
            existing = utils.find_duplicate(entry.get("word"), entry.get("pos"))
            alongside = existing[0] if existing else None
        except Exception:
            app.logger.exception("Could not read the card being duplicated")
    card_id = utils.save_flashcard(entry, added_by_user_id=web._current_user_id(),
                             allow_duplicate=allow_duplicate)
    if card_id is None:
        # A duplicate, but this lookup may still carry what the stored card is
        # missing -- which is the exit from #349's trap: a card saved during a
        # translator outage could otherwise never be improved, because looking
        # the word up again is exactly what #101 refuses.
        #
        # Still returns False. A fill is **not** a save, and saying otherwise
        # is how #308 had Mykola confirming a card that was never written.
        filled = utils.fill_missing_fields(entry)
        if filled:
            applog.card_filled(entry, filled, source=source,
                               user=web._current_email())
            if fills is not None:
                fills.append(filled)
        else:
            applog.card_skipped(entry, source=source, user=web._current_email())
        return False
    applog.card_created(entry, source=source, user=web._current_email(),
                        card_id=card_id, alongside=alongside)
    return True


# What a fill actually filled, in the words the popup uses for those fields
# (#377). A map rather than the labels on the card, because a language hidden
# in Settings travels as a hidden input with no label to borrow -- and a card
# whose Russian was filled should say so whether or not that field is on
# screen.
FILLED_FIELD_LABELS = {
    "explanation_en": "English explanation",
    "examples_en": "English examples",
    "translation_ukr": "Ukrainian translation",
    "examples_ukr": "Ukrainian examples",
    "translation_rus": "Russian translation",
    "examples_rus": "Russian examples",
}

# #186's sentence, said in two places since #377: after a save was skipped as a
# duplicate, and on the chip that says the card is a duplicate before anything
# is pressed. One string, because they are one fact told at two moments.
HIDDEN_DUPLICATE_NOTE = ("It is in the shared deck, hidden from you by your "
                         "'Use only individual cards' setting.")


def duplicate_notice(entries):
    """Extra sentence for a save skipped as a duplicate (#186), or None.

    Duplicate prevention (#101) is global, but #127 hides other people's cards
    — so "already in the database" can be said about a card the visitor cannot
    see, which reads as the app contradicting itself. Naming the setting turns
    a puzzle into a choice they can act on.

    Only ever an *addition* to the existing message: the plain wording is
    correct whenever the blocking card is one they can actually find.
    """
    if not web.current_settings()["individual_cards"]:
        return None
    owner = web._current_user_id()
    try:
        for entry in entries:
            existing = utils.find_duplicate(entry.get("word"), entry.get("pos"))
            if existing and existing[1] != owner:
                return HIDDEN_DUPLICATE_NOTE
    except Exception:
        # A dead database here costs a nicety, not the save path's answer.
        app.logger.exception("Could not check whether the duplicate is hidden")
    return None


def _word_already_saved(word):
    """Whether the word already has cards (issue #145). A DB error is treated
    as 'unknown' → no warning, so lookups keep working when the DB is down."""
    try:
        return utils.flashcard_word_exists(word)
    except Exception:
        return False


def _mark_already_saved(cards):
    """Tell each proposed card what the deck already holds for it (#377).

    The review popup used to say nothing until Add was pressed, and then said
    it on the button -- so with a dozen cards parsed from a file, finding out
    which ones were worth pressing cost a dozen presses.

    Two states, because #101 deduplicates on **word + part of speech** and a
    card parsed from notes often carries no part of speech at all:

    * `card` -- the same word and part of speech is already saved, so Add
      writes nothing. Not nothing at all: `utils.fill_missing_fields()` still fills
      what the stored card left empty (#349), and the sentence says so rather
      than implying a second copy;
    * `word` -- the word is saved under some other part of speech. This card
      *will* be added, as a second card, which is what #145 warns about one
      step earlier in the lookup panel.

    Telling them apart is the point. A chip reading "already in DB" on a card
    that is about to be added perfectly well is the same lie in the other
    direction.

    The sentence is built here rather than in the template or the script
    because it is shown in both -- as the chip's tooltip and as the question
    the confirmation asks -- and two copies of a sentence is two wordings by
    the end of the month.
    """
    try:
        states = utils.find_saved_words([(card.get("word"), card.get("pos"))
                                   for card in cards])
        # #186: duplicate detection is global while #127 hides other people's
        # cards, so "already in DB" can be said about a card the visitor
        # cannot find. Read once for the popup rather than per card.
        hidden_matters = web.current_settings()["individual_cards"]
        owner = web._current_user_id()
    except Exception:
        # Unknown, so nothing is claimed. The same answer #145's
        # `_word_already_saved()` gives to an unreachable database, for the
        # same reason: a popup that says less is better than one that does not
        # open. Everything the chips need is read in here for that reason --
        # the cards were parsed or looked up before this ran, and losing them
        # to a failed nicety would be the expensive half of the request thrown
        # away for the cheap one.
        app.logger.exception("Could not check which proposed cards are saved")
        return

    for card, state in zip(cards, states):
        card.update(_saved_mark(card.get("word"), card.get("pos"), state,
                                hidden_matters, owner))


def _saved_mark(word, pos, state, hidden_matters, owner):
    """The chip and the sentence for one card, or `{}` for a word nobody has.

    Its own function because #380 asks the same question again: the pencil
    changes the word in the browser, and the mark rendered here was an answer
    about the word the *parser* produced. `/saved.json` re-asks and hands back
    what this returns, so a renamed card cannot end up wearing a mark from a
    different word -- which would be #101 refusing in silence again, the
    failure #377 and #379 both exist to end.
    """
    pos = (pos or "").strip()
    if state["exact"]:
        detail = ("You already have a card for “%s”%s. Adding "
                  "this one keeps that card and saves this text beside it, "
                  "as a second card."
                  % (word, " (%s)" % pos if pos else ""))
        if hidden_matters and state["exact"][1] != owner:
            detail += " " + HIDDEN_DUPLICATE_NOTE
        return {"already": "card", "already_label": "Already in DB",
                "already_detail": detail}
    if state["others"]:
        saved_as = ", ".join(other or "no part of speech"
                             for other in state["others"])
        detail = ("You already have “%s” saved as %s. %s"
                  % (word, saved_as,
                     "This card is %s, so it will be added as a separate "
                     "card." % pos if pos else
                     "This card has no part of speech, so it will be added "
                     "as a separate card."))
        return {"already": "word", "already_label": "Word already saved",
                "already_detail": detail}
    return {}


# Every value `explanation_source` may hold (#390): the dictionaries the
# registry knows about, plus Reverso, which `lookup_word()` still falls back to
# for definitions without being a choice anybody can make.
EXPLANATION_SOURCES = frozenset(parsers.DICTIONARY_SLUGS) | {"reverso"}


def _text_source(field):
    """Which dictionary the dialog says wrote one of the fields it submits.

    `field` is `explanation_source` or `examples_source` -- two credits, because
    the two texts are edited separately (#390).

    Validated against the registry rather than trusted, because it arrives in a
    form field like everything else and a credit is a claim about somebody
    else's writing. An unrecognised value becomes **no credit**, which is the
    safe direction: a missing one shows nothing, where a wrong one puts a
    dictionary's name on a sentence it never wrote.

    Both dialogs clear the matching hidden field as soon as anybody types in
    the box beside it, so edited text arrives here with nothing to claim --
    which is the same answer `utils.update_flashcard()` reaches on its own.
    """
    value = (request.form.get(field) or "").strip().lower()
    return value if value in EXPLANATION_SOURCES else None


def _example_list(field):
    """One submitted examples field as a list of sentences, or None.

    Two shapes reach this and both are the app's own: the hidden JSON a card
    has carried since #134, and the one-per-line text of a textarea. The edit
    dialog has always sent lines (#176) and since #357 the review popup does
    too - but either dialog can still submit untouched JSON, so a reader that
    knows only one shape drops the other in silence, which is exactly how the
    popup's examples would be lost the moment they became editable.

    Not a general parser: anything that is neither shape counts as no
    examples, the same as an empty box.
    """
    raw = (request.form.get(field) or "").strip()
    if not raw:
        return None
    if raw.startswith("["):
        try:
            value = json.loads(raw)
        except (ValueError, TypeError):
            value = None
        if isinstance(value, list):
            items = [str(x).strip() for x in value if str(x).strip()]
            return items or None
    items = [line.strip() for line in raw.splitlines() if line.strip()]
    return items or None


# Where a card goes when nobody chose a topic (#414).
#
# **One declaration**, for the reason `TRANSLATORS` and `ACTIVITIES` are each
# declared once: this used to be the string "general" typed out at every use,
# and #407 renamed that topic in both databases without the code noticing. Every
# lookup saved with the box left empty then recreated the topic #407 had just
# removed -- the consolidation quietly undoing itself, two cards at a time.
#
# It is also injected into Mykola (see `get_mykola()`), so a card saved from a
# conversation lands in the same place as one saved from the page.
DEFAULT_TOPIC = "General knowledge"


@app.route("/topics.json")
def topics_json():
    """Topic tile data for the Browse flashcards section — fetched by the
    Mykola widget to refresh the tiles after a card is added from chat
    (issue #53). Same DB-unreachable fallback as the index page.

    Two shapes, and both are load-bearing (#218). `sections` groups the topics
    the way the index page renders them; `topics` is the flat list, still what
    the move dialog offers as suggestions (#177). Adding the first without
    keeping the second would have emptied that dialog's datalist.
    """
    owner = web.cards_owner_filter()
    try:
        topics = utils.get_topics(owner, **web.viewer())
        sections = web._sections_for_visitor(owner)
    except Exception:
        topics, sections = [], []
    # Icons ride alongside as a name -> URL map rather than as a third element
    # in each pair (#223). The pair is what `utils.get_topics_by_section()` returns and
    # what the move dialog reads; widening it would push a presentation concern
    # into the database layer and into every existing reader.
    icon_map = {name: icons.topic_icon(name)
                for _, pairs in sections for name, _ in pairs
                if icons.topic_icon(name)}
    # The padlocks ride along like the icons (#382/#223): `refreshBrowseTopics()`
    # rebuilds the very block index.html renders, so a mark drawn on one and not
    # the other would vanish the moment a card was saved from chat.
    return jsonify({"topics": topics, "sections": sections, "icons": icon_map,
                    "private": web._private_marks()})


@app.route("/", methods=["GET", "POST"])
def index():
    """
    Landing page: look up a Reverso word or upload a notes file
    (.txt / .docx / .mht, #137).
    Successful submissions save flashcards and redirect to the topic page.
    """
    message = None
    proposed = None
    proposed_topic = None
    proposed_degraded = False   # the cards carry no translations (#349)
    source_content = None  # readable text of an upload, shown beside its cards
    duplicate_warning = None  # the word to warn about before looking it up (#145)
    write_refusal = None  # why a write was refused, if one was (#125/#126)
    sign_in_refusal = None  # a lookup refused where signing in helps (#388)
    if request.method == "POST":
        action = request.form.get("action")
        topic = ((request.form.get("topic") or DEFAULT_TOPIC).strip()
                 or DEFAULT_TOPIC)
        try:
            if action == "parse_word":
                word = (request.form.get("word") or "").strip()
                if not word:
                    message = "Please enter a word."
                elif not request.form.get("force_lookup") and _word_already_saved(word):
                    # Early duplicate warning (#145): the word already has
                    # cards, so ask before the (slow) lookup and review dialog.
                    duplicate_warning = word
                    proposed_topic = topic
                else:
                    # #388. Asked here: after #145's duplicate warning, so
                    # a word the learner has not decided about costs
                    # nothing, and before any provider, so a refusal
                    # spends nothing at all. Asked **once** and kept --
                    # `_lookup_refusal()` claims the slot as it answers,
                    # so a second call would take a second slot.
                    lookup_refusal = web._lookup_refusal()
                    if lookup_refusal:
                        # A ceiling an account has already reached is not
                        # something signing in fixes; the anonymous ones
                        # are, and they say so through the dialog #125's
                        # write refusal already uses.
                        if lookup_refusal["sign_in"]:
                            sign_in_refusal = lookup_refusal["message"]
                        else:
                            message = lookup_refusal["message"]
                    else:
                        prefs = web.current_settings()
                        # The provider-by-provider detail is logged by the parser;
                        # this line carries the identity it cannot see (#30).
                        applog.lookup_started(
                            word, prefs["translator"],
                            prefs["explanatory_dictionary"], user=web._current_email())
                        entries = parsers.lookup_word(
                            word, topic=topic,
                            translator=prefs["translator"],
                            explanatory_dictionary=prefs["explanatory_dictionary"],
                        )
                        # #349: the dictionary answered and no translator did, so
                        # these cards carry an explanation and no translations.
                        #
                        # **Where this is said depends on where the learner is
                        # looking.** A page-level banner is unreadable behind the
                        # review popup -- `.modal-overlay` is fixed, inset 0, 75%
                        # opaque and blurred -- and that popup is the default, so
                        # the first version of this notice was invisible to most
                        # people while passing a test that only checked the HTML.
                        # The banner is kept for the automatic save, which has no
                        # popup at all; the popup carries its own, next to the
                        # empty fields and phrased around what to do about them.
                        degraded = bool(entries) and not any(
                            entry.get("translation_ukr")
                            or entry.get("translation_rus")
                            for entry in entries)
                        if prefs["cards_automatically"] and not web.can_add_cards():
                            # #125/#126: nothing may be written, so the automatic
                            # save cannot happen. The lookup already succeeded, so
                            # show its cards in the review popup rather than
                            # throwing the work away — signing in from the prompt
                            # leaves them there to be added.
                            applog.card_add_denied(
                                {"word": word}, source="automatic add",
                                user=web._current_email(),
                                reason="blocked" if web.is_blocked() else "anonymous")
                            proposed = entries
                            proposed_topic = topic
                            proposed_degraded = degraded
                            write_refusal = web.add_refusal()
                        elif prefs["cards_automatically"]:
                            # 'Add cards automatically' is on (#13): skip the
                            # review popup, write the cards straight to the DB.
                            # Duplicates are skipped and reported (issue #101).
                            if degraded:
                                # Said as *the service is unavailable* rather than
                                # *this word has no translations*, which is the
                                # wrong sentence that sent #348's investigation at
                                # the wrong provider.
                                flash(("No translation service is answering just "
                                       f"now, so '{word}' was saved with its "
                                       "English explanation and examples only. "
                                       "Look it up again later and the "
                                       "translations will be filled in.", None))
                            fills = []
                            added = sum(
                                1 for entry in entries
                                if _save_and_log(entry, source="automatic add",
                                                 fills=fills)
                            )
                            skipped = len(entries) - added
                            # A duplicate that gained something is not "nothing
                            # added" (#349), and a learner repeating a lookup to
                            # repair a card needs to hear that it worked.
                            completed = (f" Completed {len(fills)} of them with "
                                         "this lookup." if fills else "")
                            if not added:
                                note = duplicate_notice(entries)   # #186
                                flash((f"All {skipped} card(s) for '{word}' are "
                                       "already in the database — nothing added."
                                       + completed
                                       + (f" {note}" if note else ""), None))
                            elif skipped:
                                flash((f"Added {added} card(s) for '{word}' "
                                       f"automatically, skipped {skipped} already "
                                       "in the database." + completed, topic))
                            else:
                                flash((f"Added {added} card(s) for '{word}' automatically.", topic))
                            return redirect(url_for("index"))
                        # Don't save yet: show the cards for review/editing first.
                        proposed = entries
                        proposed_topic = topic
                        proposed_degraded = degraded

            elif action == "upload_notes":
                # Parsing costs money before it costs anything else (#200):
                # a Reverso .mht/.docx sends its glued translations to Claude
                # (_split_glued_translations), and that happens *before* the
                # save this visitor may not be allowed to make. Ask the same
                # guard the save routes ask, at the door — after this point the
                # money is spent on cards #125 would refuse to store.
                #
                # Every upload, not only the expensive kinds: which file calls
                # Claude cannot be known without parsing it, and parsing is the
                # thing being paid for.
                write_refusal = web.add_refusal()
                file = request.files.get("notes_file")
                # The account's own day (#447). #200 made this account-only and
                # gave it no number, which was right while the keyword gate
                # meant an account was somebody you had handed a keyword to.
                #
                # Claimed here, beside #125's refusal and before `file.read()`,
                # for #200's own reason: which file calls Claude cannot be known
                # without parsing it, and parsing is the thing being paid for.
                # A refusal past the ceiling therefore costs nothing either.
                if not write_refusal:
                    write_refusal = web.account_refusal(
                        utils.UPLOAD, utils.UPLOAD_ALL,
                        web.UPLOAD_USER_DAILY, web.UPLOAD_ALL_DAILY,
                        web.UPLOAD_USER_LIMIT_PROMPT)
                if write_refusal:
                    pass          # refused: the file is not even read
                elif file is None or not file.filename:
                    message = "Please choose a .txt, .docx or .mht file."
                else:
                    # Don't save yet: show the parsed cards next to the file
                    # content for review/editing, like the word lookup does.
                    # The parser is picked by extension (#137).
                    data = file.read()
                    try:
                        with applog.Timer() as timer:
                            entries, source_content = parsers.parse_notes_preview(
                                file.filename, data, topic=topic)
                    except Exception as e:
                        applog.file_rejected(file.filename, e,
                                             user=web._current_email())
                        raise
                    applog.file_parsed(file.filename, len(data), len(entries),
                                       topic=topic, user=web._current_email(),
                                       elapsed_ms=timer.ms)
                    if not entries:
                        message = "No vocabulary entries found in that file."
                    else:
                        proposed = entries
                        proposed_topic = topic

        except Exception as e:
            message = f"Error: {e}"

    if proposed:
        # #377. Here rather than in either branch above: both the word lookup
        # and the notes upload end at the same popup, and what the deck already
        # holds is a question about the cards, not about where they came from.
        _mark_already_saved(proposed)

    try:
        sections = web._sections_for_visitor()
    except Exception:
        sections = []  # DB unreachable (e.g. locally) — page still works

    return render_template(
        "index.html", message=message, sections=sections,
        private_marks=web._private_marks(),
        proposed=proposed, proposed_topic=proposed_topic,
        proposed_degraded=proposed_degraded,
        source_content=source_content, duplicate_warning=duplicate_warning,
        write_refusal=write_refusal, sign_in_refusal=sign_in_refusal,
    )


@app.route("/cards/add", methods=["POST"])
def add_card():
    """
    Save one reviewed (possibly edited) card from the lookup popup.
    Returns JSON so the popup can stay open for the remaining cards.
    """
    def cleaned(field):
        return (request.form.get(field) or "").strip() or None

    word = cleaned("word")
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    entry = {
        "word": word,
        "pos": cleaned("pos"),
        "topic": cleaned("topic") or DEFAULT_TOPIC,
        "explanation_en": cleaned("explanation_en"),
        "explanation_source": _text_source("explanation_source"),
        "examples_en": _example_list("examples_en"),
        "examples_source": _text_source("examples_source"),
        "translation_ukr": cleaned("translation_ukr"),
        "examples_ukr": _example_list("examples_ukr"),
        "translation_rus": cleaned("translation_rus"),
        "examples_rus": _example_list("examples_rus"),
    }
    refusal = web.add_refusal()
    if refusal:
        # #125/#126. Answered here rather than by hiding the button: these
        # forms are ordinary POSTs and a hand-made one goes straight past the
        # UI. `sign_in_required` is what tells the popup to show the message
        # instead of its generic "saving failed" alert.
        applog.card_add_denied(entry, source="review popup",
                               user=web._current_email(),
                               reason="blocked" if web.is_blocked() else "anonymous")
        return {"ok": False, "sign_in_required": True, "error": refusal}, 403
    # The learner was shown the card they already have and answered "add it
    # anyway" (#379). #101 is lifted for this one press: it exists to stop
    # repeated lookups piling up rows nobody asked for, and somebody who has
    # read the confirmation is not making that mistake. Everything else --
    # every other save path, and this one without the flag -- still refuses.
    #
    # Read from the form because the popup is the only thing that can have
    # asked. A hand-made POST can set it too, and the cost is a second card in
    # the sender's own deck.
    anyway = (request.form.get("confirmed_duplicate") or "").strip() in (
        "1", "true", "yes")
    # What the press *did* when it did not write a card (#377). A skipped
    # duplicate still fills whatever the stored card left empty (#349), and
    # this route was the one surface that never said so: the automatic-add
    # path has reported "Completed N of them with this lookup" since #349,
    # while the popup's button said "Already in DB" over a card it had just
    # changed. Which reads as "nothing happened" -- the wrong half of the
    # truth, and the half that matters least to somebody who pressed Add.
    fills = []
    if not _save_and_log(entry, source="review popup", fills=fills,
                         allow_duplicate=anyway):
        # #101 skipped it; #186 explains when the blocking card is hidden.
        # The keys are present only when there is something extra to say, so
        # the ordinary duplicate answer keeps its existing shape.
        body = {"ok": True, "saved": False, "duplicate": True}
        if fills:
            body["filled"] = [FILLED_FIELD_LABELS.get(field, field)
                              for field in fills[0]]
        note = duplicate_notice([entry])
        if note:
            body["note"] = note
        return body
    return {"ok": True, "saved": True}


TOPIC_VISIBILITY_MESSAGES = {
    "changed": None,          # the page redraws; the select says it already
    "unchanged": None,
    "denied": "That topic is not yours to hide.",
    "nobodys": "A topic with no creator cannot be made private — there is "
               "nobody for it to belong to.",
    "shared": "This topic holds cards other people added, so it cannot be made "
              "private. Move those cards to another topic first, and everyone "
              "keeps what they saved.",
    "taken": "You already have a topic with that name in the other "
             "visibility. Rename one of them first.",
    "missing": "That topic no longer exists.",
}


@app.route("/topics/<int:topic_id>/visibility", methods=["POST"])
def topic_visibility(topic_id):
    """Make one topic public or private (#382).

    A write, so it is a POST and it is logged (#30) -- including its refusals,
    which are the two answers a learner will report as "it did nothing".

    The permission is `utils.set_topic_visibility()`'s, not this route's: the rule is
    "your own topic", and it belongs beside the UPDATE for the reason #162 and
    #176 put ownership in the statement rather than in a check before it. The
    template only decides what to draw.
    """
    if web.is_blocked():
        flash((web.blocked_notice(), None))
        return redirect(url_for("flashcards", topic=request.form.get("topic", "")))
    public = (request.form.get("visibility") or "public") == "public"
    outcome = utils.set_topic_visibility(topic_id, public, **web.viewer())
    name = request.form.get("topic", "")
    applog.topic_visibility_set(name, public, topic_id=topic_id,
                                user=web._current_email(), outcome=outcome)
    message = TOPIC_VISIBILITY_MESSAGES.get(outcome)
    if message:
        flash((message, None))
    return redirect(url_for("flashcards", topic=name))


@app.route("/flashcards/<topic>")
def flashcards(topic):
    """Display all flashcards saved under the given topic.

    Since #382 the topic is **resolved** before it is read, and a name that
    resolves to nothing this visitor may see is a 404. Leaving it out of every
    list is not enough: a name reaches this route from a URL somebody kept, a
    bookmark, or a guess, and the page would otherwise open empty and tell them
    the topic is there but has no cards.

    `?t=` settles which topic when a name matches two -- the public one and a
    private one -- which only the admin can ever see. It is checked against the
    same rule rather than trusted.
    """
    wanted = request.args.get("t", type=int)
    found = utils.resolve_topic(topic, topic_id=wanted, **web.viewer())
    if found is None:
        abort(404)
    cards = utils.get_flashcards_by_topic(found["name"], web.cards_owner_filter(),
                                    **web.viewer())
    # The move dialog's topic suggestions (#177) are fetched from
    # /topics.json when it first opens, rather than queried here: this page is
    # loaded by everyone and the list is only needed by someone who actually
    # moves a card.
    return render_template(
        "flashcards.html", topic=found["name"], cards=cards, topic_row=found,
        # Who may change it, which is not the same as who may see it: the
        # admin reads every topic (#382) and still does not own this one.
        can_set_visibility=(found["created_by_user_id"] is not None
                            and found["created_by_user_id"] == web._current_user_id()))


# A tiny sample deck so the card-deck activity (#78) can be opened and its
# flip animation previewed when the database is unreachable (e.g. local dev,
# where PythonAnywhere MySQL is not accessible).
# TODO(#78): remove this fallback once the deck can be exercised against a real
#   DB locally (fixtures / seeded local MySQL). It exists only for the demo.
_DEMO_DECK = [
    {"word": "streamline", "pos": "verb",
     "explanation_en": "to make a system or process work more simply and effectively",
     "translation_ukr": "оптимізувати", "translation_rus": "оптимизировать"},
    {"word": "resilient", "pos": "adjective",
     "explanation_en": "able to recover quickly from difficult conditions",
     "translation_ukr": "стійкий", "translation_rus": "устойчивый"},
    {"word": "insight", "pos": "noun",
     "explanation_en": "a clear, deep understanding of a complicated situation",
     "translation_ukr": "розуміння", "translation_rus": "понимание"},
]


def _deck_translation(prefs):
    """Which translation the deck shows, following the visibility settings
    (#46/#79/#111): Ukrainian when visible, else Russian; both hidden -> none.
    Per #78, when both languages are visible Ukrainian wins."""
    if prefs["show_ukrainian"]:
        return "translation_ukr", "Ukrainian"
    if prefs["show_russian"]:
        return "translation_rus", "Russian"
    return None, None


@app.route("/deck/<topic>")
def card_deck(topic):
    """Flashcards activity (#78): a browsable deck of flip cards for one topic.

    One card shows at a time — its word on the front; flipping reveals the
    explanation plus one translation. Left/Right arrows step through the deck.
    The flip animation is scoped to this page's template, so it stays local to
    this activity and doesn't affect the rest of the app.
    """
    prefs = web.current_settings()
    try:
        cards = utils.get_flashcards_by_topic(topic, web.cards_owner_filter(), **web.viewer())
        demo = False
    except Exception:
        # DB unreachable — fall back to the sample deck so the activity still
        # renders (see _DEMO_DECK). TODO(#78): drop this branch with a real DB.
        cards = _DEMO_DECK
        demo = True
    trans_field, trans_label = _deck_translation(prefs)
    return render_template(
        "cards.html", topic=topic, cards=cards,
        trans_field=trans_field, trans_label=trans_label, demo=demo,
    )


@app.route("/flashcards/<topic>/delete/<int:card_id>", methods=["POST"])
def delete_card(topic, card_id):
    """Delete one flashcard, if this visitor may (#162), and return to the topic.

    Enforced here rather than in the template: greying the cross is
    presentation, and a hand-made POST goes straight past it. Until this
    landed the route had no identity check at all, so anyone past the keyword
    gate could delete any card.
    """
    if web.is_blocked():
        # #126: a blocked account keeps its cards but may not remove them,
        # exactly as it may not add any. Checked before ownership, so the
        # answer does not depend on whose card it is.
        applog.card_delete_denied(card_id, topic=topic, user=web._current_email(),
                                  reason="blocked")
        flash((web.blocked_notice(), None))
        return redirect(url_for("flashcards", topic=topic))

    user_id = web._current_user_id()
    admin = web.is_admin()
    if not admin and user_id is None:
        # No identity at all — an anonymous visitor, or a sign-in whose users
        # row could not be written (#148). Nothing can be theirs, so this is
        # #125's sign-in prompt rather than #162's "someone else's card".
        applog.card_delete_denied(card_id, topic=topic, user=web._current_email(),
                                  reason="anonymous")
        flash((web.DELETE_SIGN_IN_PROMPT, None))
        return redirect(url_for("flashcards", topic=topic))

    word, outcome = utils.delete_flashcard(card_id, owner_id=user_id, admin=admin)
    if outcome == "deleted":
        applog.card_deleted(card_id, word, topic=topic, user=web._current_email())
        flash((f"Deleted card '{word}'.", None))
    elif outcome == "denied":
        applog.card_delete_denied(card_id, topic=topic,
                                  user=web._current_email(), reason="not owner")
        flash((web.DELETE_NOT_YOURS, None))
    else:
        applog.card_delete_missed(card_id, topic=topic, user=web._current_email())
        flash(("Card not found — it may have already been deleted.", None))
    return redirect(url_for("flashcards", topic=topic))


@app.route("/flashcards/<topic>/move/<int:card_id>", methods=["POST"])
def move_card(topic, card_id):
    """Move one card to another topic (#177), then go somewhere sensible.

    A redirect with a flash rather than JSON, unlike editing: the card leaves
    the page it was moved from, so there is nothing to re-render in place and
    the useful feedback is a sentence naming where it went.
    """
    to_topic = (request.form.get("to_topic") or "").strip()

    if web.is_blocked():
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="blocked")
        flash((web.blocked_notice(), None))
        return redirect(url_for("flashcards", topic=topic))
    user_id = web._current_user_id()
    admin = web.is_admin()
    if not admin and user_id is None:
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="anonymous")
        flash((web.MOVE_SIGN_IN_PROMPT, None))
        return redirect(url_for("flashcards", topic=topic))
    if not to_topic:
        flash(("Choose a topic to move the card to.", None))
        return redirect(url_for("flashcards", topic=topic))

    outcome, detail = utils.move_flashcard(card_id, to_topic, owner_id=user_id,
                                     admin=admin)
    if outcome == "denied":
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="not owner")
        flash((web.MOVE_NOT_YOURS, None))
        return redirect(url_for("flashcards", topic=topic))
    if outcome == "missing":
        flash(("Card not found — it may have already been deleted.", None))
        return redirect(url_for("flashcards", topic=topic))
    if outcome == "unchanged":
        flash((f"That card is already in '{topic}'.", None))
        return redirect(url_for("flashcards", topic=topic))

    word, from_topic = detail
    # Both ends of the move (#161). `from_topic` was already being unpacked here
    # and then dropped, so the log could say where a card had landed but never
    # where it came from — the one thing a move is actually about.
    applog.card_moved(card_id, word, from_topic, to_topic,
                      user=web._current_email())
    flash((f"Moved '{word}' to '{to_topic}'.", to_topic))

    # Moving the last card out of a topic makes that topic cease to exist —
    # there is no topics table. Landing back on a page that no longer has
    # anything to show, for a topic that has vanished from the chips, reads as
    # a bug; the topic list is the honest destination.
    try:
        remaining = [name for name, _ in utils.get_topics(web.cards_owner_filter(), **web.viewer())]
    except Exception:
        remaining = [from_topic]      # DB unreachable: stay put rather than guess
    if from_topic not in remaining:
        return redirect(url_for("index"))
    return redirect(url_for("flashcards", topic=from_topic))


# How many words one session may have checked against a lexicon in an hour
# (#258). Not a money guard -- both lookups are free -- but Wikimedia
# rate-limits what looks like a scraper, and being throttled would turn every
# dispute into "could not check" for everybody. A round offers at most five
# disputes, so this is roughly eight rounds' worth back to back.
WORD_CHECKS_PER_HOUR = 40


def _word_check_allowed():
    """Whether this session may spend another lexicon lookup (#258).

    Counted in the session rather than the database: it protects our own
    politeness rather than anything a learner owns, and a counter that resets
    when a cookie does is the right weight for that. A confirmed word is
    answered from the table without a lookup at all, so the cap is only ever
    reached by a genuine run of new disputes -- or by somebody driving the
    endpoint, which is what it is for.
    """
    now = time.time()
    started, count = session.get("word_checks", (0, 0))
    if now - started > 3600:
        started, count = now, 0
    if count >= WORD_CHECKS_PER_HOUR:
        return False
    session["word_checks"] = (started, count + 1)
    return True


@app.route("/games/word-check.json", methods=["POST"])
def word_check():
    """Settle a word *Real or fake* called invented (#258).

    The learner disputes; a real lexicon answers; the answer is kept so the
    word is never offered as invented again. Three outcomes and only one of
    them is a verdict -- `real: false` means both lexicons answered and
    neither had it, which is **not** proof the word was invented, and
    `real: null` means nothing could be reached at all.

    A word already in the table is answered from it, with no request to
    anybody: a settled question stays settled, and the second learner to
    dispute it pays nothing.
    """
    data = request.get_json(silent=True) or {}
    word = (data.get("word") or "").strip()
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    if web.is_blocked():
        return {"ok": False, "error": web.blocked_notice()}, 403

    if word.lower() in utils.confirmed_words():
        return {"ok": True, "real": True, "known": True,
                "source": "a check somebody already made"}
    if not _word_check_allowed():
        return {"ok": True, "real": None,
                "error": "That is a lot of words to check at once. "
                         "Try again a little later."}

    verdict = parsers.confirm_word(word)
    if verdict.get("real"):
        try:
            first = utils.remember_confirmed_word(word, verdict.get("source", ""))
            applog.word_confirmed(word, verdict.get("source", ""),
                                  user=web._current_email(), first=first)
        except Exception:
            # The confirmation still stands for this learner and this round;
            # it simply was not remembered for the next one.
            app.logger.exception("Could not remember a confirmed word")
    return dict({"ok": True}, **verdict)


@app.route("/saved.json", methods=["POST"])
def saved_json():
    """Whether one word is already in the deck, as JSON (#380).

    #377 marks every proposed card when the popup is built, on the server, for
    the word the *parser* produced. The pencil changes that word afterwards in
    the browser, so the card asks this and re-marks itself: the chip appears,
    changes or goes, and the confirmation #379 made worth answering asks about
    the word that will actually be saved. Without it a rename onto a word the
    deck already holds would be refused in silence by #101 -- the failure both
    of those tickets exist to end.

    A **read**, and cheaper than the page that asks it: one query, no provider
    call, nothing written. So it asks for no permission the review popup does
    not already have -- unlike `/lookup.json`, which spends money and wants an
    account for it (#125).

    An unreachable database answers `known: false` rather than an error, and
    the card drops its mark instead of wearing one from a different word: the
    same "say nothing rather than something wrong" `_mark_already_saved()`
    answers with, and #145 before it.
    """
    data = request.get_json(silent=True) or {}
    word = (data.get("word") or "").strip()
    pos = (data.get("pos") or "").strip()
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    try:
        state = utils.find_saved_words([(word, pos or None)])[0]
        hidden_matters = web.current_settings()["individual_cards"]
        owner = web._current_user_id()
    except Exception:
        app.logger.exception("Could not check whether a renamed word is saved")
        return {"ok": True, "known": False, "mark": {}}
    return {"ok": True, "known": True,
            "mark": _saved_mark(word, pos, state, hidden_matters, owner)}


@app.route("/lookup.json", methods=["POST"])
def lookup_json():
    """One word, looked up with this visitor's providers, as JSON (#191).

    A **read**. Nothing is stored: the edit dialog fills its own fields from
    the answer and `edit_card()` remains the only way a card changes, keeping
    its ownership rule, its duplicate check and its logging as the single
    write path. That is the whole design constraint of #191 -- a second write
    path would be a second place to get permissions wrong.

    Guarded like the dialog it serves rather than like the home page's lookup.
    A lookup used to be free scraping and #125 lets an anonymous visitor read;
    since #353 it is a licensed API call that costs money per word, so this is
    not something to leave open, and the edit dialog behind it is signed-in
    only anyway (#176).

    **The part of speech is matched here**, not in the browser. #228's synonym
    map lives in `parsers` because a translator and a dictionary name the same
    thing differently, and a second copy of it in JavaScript would drift from
    the first within a month. The answer carries both halves: `match` for the
    part of speech that was asked about, and `entries` for everything the
    lookup found, so the dialog can offer a choice when nothing matched.
    """
    if web.is_blocked():
        return {"ok": False, "error": web.blocked_notice()}, 403
    if not web.is_admin() and web._current_user_id() is None:
        return {"ok": False, "error": web.EDIT_SIGN_IN_PROMPT}, 403

    payload = request.get_json(silent=True) or {}
    word = (payload.get("word") or "").strip()
    if not word:
        return {"ok": False, "error": "word is required"}, 400
    pos = (payload.get("pos") or "").strip()

    # #388's account ceiling applies here too, and leaving it out would be a
    # hole in it rather than a smaller cap: this spends the same providers on
    # the same key, and a learner past their day's lookups could carry on
    # through the edit dialog. Anonymous visitors never reach it -- refused
    # above by #191 -- so only the per-account row is ever claimed.
    refusal = web._lookup_refusal()
    if refusal:
        return {"ok": False, "error": refusal["message"]}, 429

    prefs = web.current_settings()
    applog.lookup_started(word, prefs["translator"],
                          prefs["explanatory_dictionary"],
                          user=web._current_email())
    try:
        entries = parsers.lookup_word(
            word,
            translator=prefs["translator"],
            explanatory_dictionary=prefs["explanatory_dictionary"],
        )
    except Exception:
        # The same tolerance the home page has: a provider outage is not a
        # 500, and the dialog says so with its fields untouched.
        app.logger.exception("Look up & update failed for %r", word)
        return {"ok": False, "error": (
            "The lookup did not answer. Your card is unchanged.")}, 502

    entries = [{k: v for k, v in entry.items() if k != "topic"}
               for entry in entries]
    wanted = parsers._pos_key(pos.lower()) if pos else None
    match = next((e for e in entries
                  if wanted and parsers._pos_key((e.get("pos") or "").lower())
                  == wanted), None)
    return {"ok": True, "match": match, "entries": entries}


@app.route("/flashcards/<topic>/edit/<int:card_id>", methods=["POST"])
def edit_card(topic, card_id):
    """Change a saved card's content (#176). JSON, so the dialog can stay open
    and show a refusal in place rather than losing what was typed.

    Enforced here rather than in the template for the same reason as #162:
    greying the pencil is presentation, and a hand-made POST goes past it.
    """
    def cleaned(field):
        return (request.form.get(field) or "").strip() or None

    if web.is_blocked():
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="blocked")
        return {"ok": False, "error": web.blocked_notice()}, 403
    user_id = web._current_user_id()
    admin = web.is_admin()
    if not admin and user_id is None:
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="anonymous")
        return {"ok": False, "error": web.EDIT_SIGN_IN_PROMPT}, 403

    word = cleaned("word")
    if not word:
        return {"ok": False, "error": "word is required"}, 400

    # Only what was actually submitted: a field the dialog did not render —
    # a language this visitor has hidden (#46/#79/#111) — must be left alone,
    # not blanked. `update_flashcard` reads a missing key as "don't touch".
    readers = {
        "word": lambda: word,
        "pos": cleaned,
        "explanation_en": cleaned,
        "translation_ukr": cleaned,
        "translation_rus": cleaned,
        "examples_en": _example_list,
        "examples_ukr": _example_list,
        "examples_rus": _example_list,
    }
    entry = {field: (read() if field == "word" else read(field))
             for field, read in readers.items() if field in request.form}
    # A credit travels with the text it belongs to or not at all (#390).
    # #191's dialog fills these boxes from a fresh lookup, so either really can
    # hold a dictionary's words -- and it clears the matching hidden field the
    # moment somebody edits a box, which leaves this None and
    # `utils.update_flashcard()` clearing the stored credit for that field alone.
    for text, credit in (("explanation_en", "explanation_source"),
                         ("examples_en", "examples_source")):
        if text in entry:
            entry[credit] = _text_source(credit)

    outcome, detail = utils.update_flashcard(card_id, entry, owner_id=user_id,
                                       admin=admin)
    if outcome == "updated":
        applog.card_edited(entry, source="card page", user=web._current_email(),
                           card_id=card_id, changed=detail)
        return {"ok": True, "changed": detail}
    if outcome == "unchanged":
        return {"ok": True, "changed": []}
    if outcome == "duplicate":
        _, dup_word, dup_pos = detail
        named = f"'{dup_word}'" + (f" ({dup_pos})" if dup_pos else "")
        return {"ok": False, "error": (
            f"Another card for {named} already exists, so this one cannot be "
            "renamed to it.")}, 409
    if outcome == "denied":
        applog.card_edit_denied(card_id, topic=topic, user=web._current_email(),
                                reason="not owner")
        return {"ok": False, "error": web.EDIT_NOT_YOURS}, 403
    return {"ok": False, "error": "Card not found — it may have been deleted."}, 404


# --- building a topic from an idea (#406) ---------------------------------
#
# Four routes and a session key, in the shape #237 already uses: the expensive
# half is post/redirect/get so a refresh cannot pay for it twice, and the part
# that takes a minute streams rather than sitting on a request.
#
#   GET  /topics/generate          the form
#   POST /topics/generate          propose a title and a word list -- nothing
#                                  written, no lookup spent
#   POST /topics/generate/start    claim the lookups, hold the approved list
#   GET  /topics/generate/filling  the progress page, which opens...
#   GET  /topics/generate/stream   ...the SSE fill that does the work
#
# The approved list lives in the **session**, as #237's held text does: a title
# and twenty short words is a few hundred bytes of the signed cookie's ~4 KB,
# and holding it is what lets the fill be a GET that EventSource can open.
TOPIC_PLAN_KEY = "topic_plan"

# A pause between words, `seed_topics.PAUSE`'s value and its reason: this fans
# out at no dictionary and is in no hurry. Twenty words is a minute of somebody
# else's bandwidth, which is why the page streams rather than waiting.
TOPIC_FILL_PAUSE = 1.0


def _lookup_budget(wanted):
    """What this fill would cost, in the learner's own daily terms.

    Stated on the approve screen rather than discovered afterwards, which is
    the whole of #406's decision about the ceiling: a mid-run refusal becomes a
    number somebody read before pressing the button.
    """
    user_id = session.get("user", {}).get("id")
    limit = web.LOOKUP_USER_DAILY if user_id is not None else web.LOOKUP_ANON_DAILY
    if not limit or limit <= 0:
        return {"limit": 0, "used": 0, "left": None, "wanted": wanted,
                "affordable": True}
    try:
        used = utils.lookups_used_today(user_id)
    except Exception:
        # An unreachable counter cannot answer and the claim itself will
        # decide. Better a screen with no number than one with a wrong number.
        app.logger.exception("Could not read today's lookup count")
        return {"limit": limit, "used": None, "left": None, "wanted": wanted,
                "affordable": True}
    left = max(limit - used, 0)
    return {"limit": limit, "used": used, "left": left, "wanted": wanted,
            "affordable": wanted <= left}


def _vet_proposal(title, words, idea, count):
    """The proposal with the free checks already done (#391's idea, #389's tool).

    Two things are settled before the learner spends anything, because both are
    free:

    * **a word no lexicon has** is usually the model inflecting or inventing,
      and #221 is what it costs to find out afterwards -- a word with no
      dictionary entry becomes a card carrying translations and no explanation,
      which is invisible locally because Reverso covers the gap.
      `parsers.wiktionary_pages()` answers the whole list in one request (#389),
      and an unreachable lexicon leaves them simply unflagged;
    * **a word the deck already holds** is worth saying out loud, because a
      lookup that produces nothing still costs a slot.

    Only the first is **unticked**. A word already in the deck is a *note*, not
    a veto: #101 keeps one card per word and part of speech, so a deck holding
    `tip` the noun still gains `tip` the verb, and unticking it would be the
    screen claiming something it does not know. Nothing is removed from the
    list either way -- the one thing this must not do is quietly shorten a list
    somebody asked for.
    """
    try:
        have = utils.existing_words()
    except Exception:
        app.logger.exception("Could not read the deck's words")
        have = set()
    found = parsers.wiktionary_pages(words)

    entries = []
    for word in words:
        missing = found is not None and word.lower() not in found
        entries.append({
            "word": word,
            "already": word.lower() in have,
            "unknown": missing,
            "use": not missing,
        })
    wanted = sum(1 for entry in entries if entry["use"])
    return {
        "title": title or idea[:topicgen.TITLE_MAX_CHARS],
        "entries": entries,
        "idea": idea,
        "count": count,
        "checked": found is not None,
        "budget": _lookup_budget(wanted),
    }


@app.route("/topics/generate", methods=["GET", "POST"])
def generate_topic():
    """Propose a topic from an idea. Writes nothing and spends no lookup.

    **The write guard runs before the model call.** That is #200's rule and the
    reason `upload_notes` asks `add_refusal()` at the door before it reads the
    file: an anonymous visitor cannot save a card (#125), so proposing twenty
    of them would be paying for a refusal.
    """
    if not web._generation_available():
        abort(404)

    refusal = web.add_refusal()
    proposal, message = None, None

    if request.method == "POST" and not refusal:
        idea = topicgen.clean_idea(request.form.get("idea"))
        count = topicgen.word_count(request.form.get("count"))
        if not idea:
            message = "Please describe the topic you have in mind."
        else:
            spend = web._generation_refusal()
            if spend:
                message = spend["message"]
            else:
                title, words = topicgen.propose(idea, count)
                if not words:
                    message = ("That topic could not be proposed just now. "
                               "Please try again in a moment.")
                else:
                    proposal = _vet_proposal(title, words, idea, count)

    return render_template(
        "generate_topic.html", proposal=proposal, message=message,
        refusal=refusal, idea=request.form.get("idea", ""),
        count=topicgen.word_count(request.form.get("count")),
        min_words=topicgen.MIN_WORDS, max_words=topicgen.MAX_WORDS,
        idea_max=topicgen.IDEA_MAX_CHARS,
        title_max=topicgen.TITLE_MAX_CHARS)


@app.route("/topics/generate/start", methods=["POST"])
def start_topic_fill():
    """Claim the lookups for an approved list and hold it for the fill.

    **The claim is here, before the redirect, and it is all-or-nothing.** That
    is #406's decision about the ceiling: the approve screen states the cost,
    this takes it in one statement, and a refusal arrives while the learner is
    still looking at the list rather than halfway through a half-built topic.

    Post/redirect/get afterwards, #237's shape, so a refresh of the progress
    page re-reads the held plan instead of claiming a second batch.
    """
    if not web._generation_available():
        abort(404)
    refusal = web.add_refusal()
    if refusal:
        flash((refusal, None))
        return redirect(url_for("generate_topic"))

    title = " ".join((request.form.get("title") or "").split())
    title = title[:topicgen.TITLE_MAX_CHARS]
    # The words that were **ticked**, in the order the form sent them. A word
    # the learner unticked is not looked up and not paid for.
    words, seen = [], set()
    for word in request.form.getlist("word"):
        word = (word or "").strip().lower()
        if word and word not in seen and topicgen.HEADWORD.match(word):
            seen.add(word)
            words.append(word)

    if not title or not words:
        flash(("Choose a title and at least one word first.", None))
        return redirect(url_for("generate_topic"))

    user_id = session.get("user", {}).get("id")
    try:
        allowed, scope, used = utils.claim_word_lookups(
            user_id, len(words), web.LOOKUP_USER_DAILY,
            web.LOOKUP_ANON_DAILY, all_limit=web.LOOKUP_ALL_DAILY)
    except Exception:
        # #237's rule for an unreachable counter: it cannot enforce a ceiling,
        # and the same outage has already made the deck unwritable.
        app.logger.exception("Could not claim the lookups for a topic")
        allowed, scope = True, None

    if not allowed:
        limit = web.LOOKUP_USER_DAILY if scope == "user" else web.LOOKUP_ANON_DAILY
        applog.anonymous_limit_hit(scope, used, limit)
        flash((f"That would need {len(words)} lookups and you have "
               f"{max(limit - used, 0)} left today. Untick a few words, or "
               "come back tomorrow.", None))
        return redirect(url_for("generate_topic"))

    session[TOPIC_PLAN_KEY] = {"title": title, "words": words}
    return redirect(url_for("filling_topic"))


@app.route("/topics/generate/filling")
def filling_topic():
    """The progress page. Opens the stream that does the work."""
    if not web._generation_available():
        abort(404)
    plan = session.get(TOPIC_PLAN_KEY)
    if not plan:
        return redirect(url_for("generate_topic"))
    return render_template("filling_topic.html", plan=plan)


@app.route("/topics/generate/stream")
def stream_topic_fill():
    """Look each approved word up and save it, reporting as it goes.

    **Streamed rather than waited on.** `seed_topics.py`'s docstring says why
    twenty words is not a request: it pauses between lookups and fans out at no
    dictionary, so this is a minute of work. `/mykola/chat/stream` proved SSE
    reaches the browser through PythonAnywhere's proxy unbuffered, and this
    reuses its headers for the same reason.

    **The lookups were already claimed** by `start_topic_fill()`, before this
    response opened -- a session write after the first byte never reaches the
    browser, which is the trap that shape avoids.

    **Saved as it goes, so it is resumable.** Each card is committed on arrival,
    so a dropped connection leaves the words that worked, and #101's duplicate
    rule makes "ask for it again" the whole of the recovery. A failed lookup is
    a skipped word rather than a dead run: `lookup_word()` raises when nothing
    comes back, and Reverso and Merriam-Webster are blocked from PythonAnywhere.
    """
    if not web._generation_available():
        abort(404)
    plan = session.get(TOPIC_PLAN_KEY) or {}
    words = list(plan.get("words") or [])
    title = plan.get("title") or ""
    prefs = web.current_settings()
    user = web._current_email()
    # Read before the response opens: `session` is not writable from inside a
    # generator, and `add_refusal()` reads the request context.
    refusal = web.add_refusal()

    def events():
        if refusal or not words or not title:
            yield web._sse({"type": "error", "message": refusal or
                        "There is nothing to build."})
            return
        saved = skipped = failed = 0
        for index, word in enumerate(words, start=1):
            try:
                entries = parsers.lookup_word(
                    word, prefs.get("translator"),
                    prefs.get("explanatory_dictionary"))
            except Exception as error:
                failed += 1
                applog.lookup_failed(word, error)
                yield web._sse({"type": "word", "word": word, "index": index,
                            "total": len(words), "outcome": "failed"})
                time.sleep(TOPIC_FILL_PAUSE)
                continue

            added = 0
            for entry in entries:
                entry["topic"] = title
                if _save_and_log(entry, source="topic generator"):
                    added += 1
            saved += added
            if not added:
                skipped += 1
            yield web._sse({"type": "word", "word": word, "index": index,
                        "total": len(words), "cards": added,
                        "outcome": "saved" if added else "skipped"})
            time.sleep(TOPIC_FILL_PAUSE)

        applog.topic_generated(title, len(words), saved, skipped=skipped,
                               failed=failed, user=user)
        yield web._sse({"type": "done", "title": title, "saved": saved,
                    "skipped": skipped, "failed": failed,
                    "url": url_for("flashcards", topic=title)})

    return Response(
        stream_with_context(events()),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
