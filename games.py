"""Which topics a round plays over (#248, groundwork for #233).

#233 gives every activity the same question before it can show anything: *which
topics am I drawing from?* Three of its rules answer it, and they are one
question wearing three hats —

* repeated `?topic=` parameters name a selection,
* no `topic` parameter at all means the whole visible deck,
* and the picker opens with whatever was chosen last time.

All three end in a list of topic names, so all three go through
`resolve_selection()` rather than each route working it out again. The functions
here are pure and take the store as an argument, so the rules can be tested
without a request context and without a database.

**What is visible is always asked freshly.** A remembered selection is a hint,
never a source of truth: a topic can be deleted, renamed, or hidden by turning
on individual-cards mode (#127) between one round and the next. Every read
intersects with the topics the learner can see *now*, and a name that no longer
resolves is dropped in silence — it is a UI preference that has expired, not an
error worth showing anybody.

Names rather than ids, in keeping with every other producer in the app (#207's
note that the parsers, the review popup and Mykola's tool schema all speak
names). The list is disposable and re-checked on every read, so an id would buy
nothing and cost a translation step.
"""

# The session key the picker's selection is remembered under.
#
# The Flask session is a **signed cookie** — there is no server-side session
# store — so what goes in it is bounded by a ~4 KB ceiling that Werkzeug
# enforces by silently dropping the cookie, which would sign the learner out.
# A selection is safe there: only names that are currently visible are ever
# stored, so its size is bounded by the deck's own topic count. Anything
# larger, a generated text among them, needs somewhere else to live.
#
# #233 sets out why this is not `settings_store`: settings are read-only for
# anonymous visitors (#102) and games are not, every existing setting is a
# bool, an enum or an int validated by `sanitize()`, and since #161 a settings
# change writes a `SETTINGS` line to `cards.log` — which starting a round
# should not do.
SELECTION_KEY = "game_topics"


def visible_topic_names(sections):
    """The topic names a learner can currently see, in page order.

    `sections` is `get_topics_by_section()`'s shape —
    `[(section, [(topic, count), ...]), ...]` — already ordered by #215's
    `(section.position, topic.position, topic.name)`. Flattening preserves that,
    which is the order the picker renders and therefore the order a resolved
    selection comes back in.
    """
    return [name for _, topics in sections for name, _ in topics]


def resolve_selection(requested, visible):
    """The topics a round actually plays over.

    `requested` is what the URL asked for (possibly nothing); `visible` is what
    the learner can see now, in page order. The result is in **page order**, not
    the order the parameters arrived in: the picker renders `visible` as given
    and must not re-sort it (#215/#218), and a selection drawn from that page
    should read the same way.

    Nothing requested means **every visible topic** — #233's rule, and what
    keeps a bare `/games/<game>/play` link meaningful and gives the picker a
    sensible "just start" default.

    Matching is case-insensitive, because the database's own collation is: a
    hand-typed `?topic=work` finds the topic stored as `Work`, exactly as
    `get_flashcards_by_topics()` would. The **canonical** spelling is what comes
    back, since that is the one the topics row holds (#207) and the one the page
    shows.
    """
    order = list(visible)
    if not requested:
        return order
    wanted = {name.casefold() for name in requested if name}
    return [name for name in order if name.casefold() in wanted]


def remembered_selection(store, visible):
    """The last selection this visitor made, as it stands today.

    `store` is the Flask session, passed in rather than reached for so the rule
    is testable on a plain dict.

    **Nothing remembered returns nothing**, where nothing *requested* returns
    everything (`resolve_selection()` above). The two are one keystroke apart
    and mean opposite things, so they do not share a code path: a first-time
    visitor has expressed no preference, and opening their picker with the whole
    deck ticked would be inventing one on their behalf. A learner who really did
    choose everything last time has that stored as a full list of names, and
    gets it back.

    A stored selection whose topics have all since disappeared comes back empty
    for the same reason — every name was dropped, so nothing was remembered,
    and the picker opens as it would for a newcomer.
    """
    stored = store.get(SELECTION_KEY)
    if not stored or not isinstance(stored, list):
        return []
    return resolve_selection(stored, visible)


def remember_selection(store, names):
    """Remember `names` as this visitor's selection.

    Callers pass an already-resolved list — the output of `resolve_selection()`
    — so only names that were visible at the time are stored. That is what keeps
    the cookie's size bounded by the deck rather than by the query string, and
    it means a stale entry can only ever come from the deck changing underneath
    it, which the next read handles.
    """
    store[SELECTION_KEY] = list(names)


# --- the activities themselves -------------------------------------------
#
# #233 asks for **one declaration** of the activities, rendered by the
# front-page panel and by the topic page's activity row. This is the third of
# it the picker needs (#250): what an activity is called, what its picker says,
# and how big a selection it needs before it can start.
#
# The icon and the tile's sub-line are deliberately absent. They are the
# panel's and the row's, they arrive with them, and adding them here before
# anything renders them would be guessing at fields nobody has asked of yet --
# the same reason this declaration did not exist at all in #248.

import random
from dataclasses import dataclass


@dataclass(frozen=True)
class Activity:
    """One thing a learner can do with a selection of topics.

    `kind` separates the quiz from the games because their URLs differ, and
    they differ for a reason worth keeping: `/quiz/<topic>` predates all of
    this and is linked from three templates, so the quiz keeps its own routes
    while every game shares `/games/<slug>`.

    `min_cards` is the **cheap** check, the one answerable from the card counts
    the picker has. A game whose real rule needs to look at the cards -- #130
    wanting four distinct answers, #235 wanting an example that contains its own
    word -- asks that question on its own page, where it has them. Both messages
    are real; neither is a copy of the other.
    """

    slug: str
    name: str
    kind: str            # "quiz" or "game"
    picker_heading: str
    min_cards: int
    too_small: str
    # Whether the picker offers a translation language (#113's quiz_lang).
    # The quiz asks for a translation, so which one is a choice worth making
    # *before* the words are drawn -- switching afterwards re-draws the round.
    # Nothing else has that question yet: a game that shows a word and asks
    # about its spelling has no translation in it at all. False by default so a
    # new game inherits no control it cannot explain.
    picks_language: bool = False


# The quiz is the only entry today, and it is the loosest of the five: one card
# carrying a translation in the quiz language. Each game ticket adds its own
# line here plus a /games/<slug>/play route, and the picker needs no change.
ACTIVITIES = {
    activity.slug: activity
    for activity in (
        Activity(
            slug="quiz",
            name="Quiz",
            kind="quiz",
            picker_heading="Choose the topics to be quizzed on",
            min_cards=1,
            too_small="Tick at least one topic to start the quiz.",
            picks_language=True,
        ),
    )
}


def activity(slug, kind=None):
    """The declared activity for `slug`, or **None** if there is no such thing.

    With `kind`, an activity of the wrong kind is also None: `/games/quiz` is
    not a way to reach the quiz, because the quiz has its own URL and two ways
    in would be two things to keep working.

    None rather than an exception, so a route turns an unknown slug into a 404
    -- which is what every game slug is until its ticket lands.
    """
    found = ACTIVITIES.get(slug)
    if found is None or (kind is not None and found.kind != kind):
        return None
    return found


# --- how many words a round asks -----------------------------------------
#
# A quiz over the whole curriculum was 93 typed answers, which is not a round
# so much as an afternoon. Twenty is the default; the picker offers a box.
#
# Remembered in the session beside the topic selection and for the same reason
# (#233): it is a per-round preference, not a per-account setting, so it needs
# no `DEFAULTS` entry, works identically signed in or not, and does not write a
# `SETTINGS` line to `cards.log` every time somebody starts a quiz.

QUIZ_WORDS_DEFAULT = 20
QUIZ_WORDS_MIN = 1
QUIZ_WORDS_MAX = 200

WORDS_KEY = "quiz_words"


def word_count(raw, remembered=None):
    """How many words a round should ask, from a query parameter.

    Clamped rather than rejected: this arrives from a URL anybody can edit, and
    a round is not the place to argue about it. Anything unreadable falls back
    to `remembered`, then to the default — the same "a stored value is a hint"
    rule the topic selection follows.
    """
    for candidate in (raw, remembered):
        try:
            value = int(candidate)
        except (TypeError, ValueError):
            continue
        return max(QUIZ_WORDS_MIN, min(QUIZ_WORDS_MAX, value))
    return QUIZ_WORDS_DEFAULT


def remembered_word_count(store):
    """The last word count this visitor asked for, or the default."""
    return word_count(store.get(WORDS_KEY))


def remember_word_count(store, count):
    store[WORDS_KEY] = int(count)


def sample(cards, count, rng=None):
    """`count` cards drawn uniformly from `cards`, without replacement.

    Every card in the selection has the same chance whichever topic it came
    from, so a topic with 36 cards contributes more questions than one with 20
    — which is what "drawn from all the words in the selected topics" means.
    Weighting by topic instead would make a small topic's words several times
    more likely, and nobody asked for that.

    Fewer cards than asked for is not an error: the round is what there is.
    `rng` is injectable so a test can pin the draw.
    """
    if count >= len(cards):
        return list(cards)
    return (rng or random).sample(list(cards), count)
