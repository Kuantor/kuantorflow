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


# --- how long a round is -------------------------------------------------
#
# A quiz over the whole curriculum was 93 typed answers, which is not a round
# so much as an afternoon. Ten is the default; the picker offers a box.
#
# Ten rather than the twenty this shipped with: a round should be finishable in
# one sitting, and the learner who wants a longer one only has to say so, while
# the learner who finds twenty too long has already abandoned it.
#
# Remembered in the session beside the topic selection and for the same reason
# (#233): it is a per-round preference, not a per-account setting, so it needs
# no `DEFAULTS` entry, works identically signed in or not, and does not write a
# `SETTINGS` line to `cards.log` every time somebody starts a quiz. That is why
# it lives here, beside the selection, rather than beside the activities.
#
# The box means **different things to different activities**, which is what
# `Words` is for. A quiz's number is questions asked (1-200); #237's is words of
# prose (50-400), and its floor is not 1 because fifty words is the shortest
# thing worth calling a passage. One box, two meanings, so the bounds travel
# with the activity rather than being one pair of constants the picker reaches
# for regardless of what it is rendering.

from collections import namedtuple

# `hint` is what the picker prints beside the box, or "" for nothing. The
# browser enforces `low`/`high` through the input's min and max either way; the
# hint is for the learner who wants to know the range before being corrected by
# it, which matters for #237's 50 and not for a quiz's 1.
Words = namedtuple("Words", "key default low high hint")

QUIZ_WORDS_DEFAULT = 10
QUIZ_WORDS_MIN = 1
QUIZ_WORDS_MAX = 200

WORDS_KEY = "quiz_words"

QUIZ_WORDS = Words(WORDS_KEY, QUIZ_WORDS_DEFAULT, QUIZ_WORDS_MIN,
                   QUIZ_WORDS_MAX, "")

# #237's passage. 150 words is a paragraph or two -- long enough to put a dozen
# words in context, short enough to read before losing interest -- and 400 is
# where a generated text stops being a demo and starts being homework. The
# ceiling is also what bounds `max_tokens`, so it is the only thing standing
# between a bug and a runaway response.
GENERATED_WORDS_DEFAULT = 150
GENERATED_WORDS_MIN = 50
GENERATED_WORDS_MAX = 400

GENERATED_WORDS_KEY = "generated_words"

GENERATED_WORDS = Words(
    GENERATED_WORDS_KEY, GENERATED_WORDS_DEFAULT,
    GENERATED_WORDS_MIN, GENERATED_WORDS_MAX,
    f"{GENERATED_WORDS_MIN}–{GENERATED_WORDS_MAX} words")


def word_count(raw, remembered=None, words=QUIZ_WORDS):
    """How long a round should be, from a query parameter.

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
        return max(words.low, min(words.high, value))
    return words.default


def remembered_word_count(store, words=QUIZ_WORDS):
    """The last length this visitor asked for, or the default."""
    return word_count(store.get(words.key), words=words)


def remember_word_count(store, count, words=QUIZ_WORDS):
    store[words.key] = int(count)


# --- how much of a hidden word is shown (#270, #334) ---------------------
#
# Declared up here rather than beside the games that use them, because the
# activities themselves name their modes and a dataclass default is evaluated
# where it is written.

HINT_NONE = "none"
HINT_FIRST = "first"
HINT_FIRST_LAST = "first_last"

# Which modes each game offers, because they are not the same set. *Spell it*
# has no "none": it always shows at least the first letter, since a bare "type
# the word for this meaning" is a different exercise. *Fill the gap* must have
# it, and must **default** to it -- the game existed for months without a hint
# and falling back to one would change it for everyone who never asked (#334).
HINTS = (HINT_FIRST, HINT_FIRST_LAST)
GAP_HINTS = (HINT_NONE, HINT_FIRST, HINT_FIRST_LAST)

# What the picker calls them.
HINT_LABELS = {
    HINT_NONE: "No hint",
    HINT_FIRST: "The first letter",
    HINT_FIRST_LAST: "First and last",
}

# The session key the hint mode is remembered under, beside the selection and
# the round length and for the same reason (#233): a per-round preference, not
# a per-account setting.
HINT_KEY = "spell_hint"

# #334's, and a **separate** key on purpose -- see `remember_hint()`.
GAP_HINT_KEY = "gap_hint"


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
import re
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

    #266 gives that split a name and two fields. `needs` says in words what a
    card must carry, for the one sentence a round prints when it dropped some;
    `min_topics` is the check `min_cards` cannot express at all, since three
    words from one topic and a stranger from another is unbuildable out of a
    single ticked topic however many cards it holds.
    """

    slug: str
    name: str
    kind: str            # "quiz", "game" or "reader" -- which panel it is in
    picker_heading: str
    min_cards: int
    too_small: str
    # The tile's second line, under its name, where a topic tile shows its card
    # count. A game has no count worth showing, and a tile with a name and a
    # picture but nothing else reads as unfinished.
    tagline: str = ""
    # The ticket that will build the round. Present while the activity is a
    # stub and removed when it lands, so the stub page can say who owns it
    # rather than apologising vaguely.
    ticket: str = ""
    # Whether the picker offers a translation language (#113's quiz_lang).
    # The quiz asks for a translation, so which one is a choice worth making
    # *before* the words are drawn -- switching afterwards re-draws the round.
    # Nothing else has that question yet: a game that shows a word and asks
    # about its spelling has no translation in it at all. False by default so a
    # new game inherits no control it cannot explain.
    picks_language: bool = False
    # What the picker's number box counts, and between which bounds -- see
    # `Words` above. Questions asked, for everything that asks questions; #237
    # overrides it because its number is words of prose.
    words: Words = QUIZ_WORDS
    # How many **topics** a round needs, where `min_cards` counts cards (#266).
    # A separate field rather than a cleverer `min_cards`, because the two are
    # different units and give different advice: "tick topics holding at least
    # four cards" and "tick at least two topics" send a learner to different
    # parts of the same page, and one number cannot say either reliably.
    #
    # One by default, so every activity that existed before #266 is unchanged
    # and a new game inherits no constraint it has not asked for -- the rule
    # `picks_language` already follows.
    min_topics: int = 1
    # What the picker says when too few *topics* are ticked. Only read when
    # `min_topics` is above one, so an activity that does not care leaves it
    # empty rather than writing a sentence nobody will see.
    too_few_topics: str = ""
    # What a card must carry for this game to use it, in words, for the one
    # sentence a round prints when it had to drop some (#266). Empty means
    # every card qualifies -- odd one out asks nothing of a card beyond its
    # word and its topic -- and then the sentence never appears.
    needs: str = ""
    # Which hint modes the picker offers, or () for an activity with no such
    # control. A tuple rather than a bool because the two games that have one
    # offer **different sets**: #270 has no "no hint" (it always shows the
    # first letter) and #334 must have one and must start there.
    #
    # On the picker rather than in the round for two reasons. In #270 the mode
    # decides *eligibility* as well as the mask -- with the last letter shown a
    # four-letter word is two-thirds given. In both, a switch on the round page
    # would let a learner reveal the last letter of a word they are stuck on,
    # which is not a hint but the answer arriving late.
    hint_modes: tuple = ()
    # Whether the picker offers the free-text line describing what the round
    # should be about (#237). Only a round that *writes* something has anything
    # to do with it, which is why it is off by default: a quiz has no use for
    # "a letter of complaint to a hotel".
    asks_instruction: bool = False


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
            tagline="Type the translation",
            needs="a translation in the language you chose",
        ),
        Activity(
            slug="multiple_choice",
            name="Multiple choice",
            kind="game",
            picker_heading="Choose the topics to be tested on",
            # Four answers to a question, so four cards before one can be
            # built. The exact rule -- four distinct options, one of them a
            # generated misspelling -- is #130's, asked on its own page where
            # it has the cards.
            min_cards=4,
            too_small="Tick topics holding at least four cards.",
            tagline="Pick from four",
            needs="a translation in the language you chose",
            # The prompt is a translation, so which language it is in is a
            # choice worth making before the words are drawn -- the same
            # question the quiz asks, answered by the same code (#113).
            picks_language=True,
        ),
        Activity(
            slug="real_or_fake",
            name="Real or fake",
            kind="game",
            picker_heading="Choose the topics to draw real words from",
            # Half a round is real words from the selection, so a handful is
            # needed before there is a round at all. The invented half is
            # modelled on the whole deck, not on these.
            min_cards=4,
            too_small="Tick topics holding at least four cards.",
            tagline="Spot the invented word",
            needs="a single word of seven letters or more",
        ),
        Activity(
            slug="scrambled",
            name="Scrambled",
            kind="game",
            picker_heading="Choose the topics to scramble words from",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Unscramble the letters",
            needs="four letters or more, with two different ones in the middle",
        ),
        Activity(
            slug="fill_the_gap",
            name="Fill the gap",
            kind="game",
            picker_heading="Choose the topics to take sentences from",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Guess the missing word",
            needs="an example sentence that uses the word itself",
            hint_modes=GAP_HINTS,
        ),
        # --- wave two (#265), registered as stubs by #266 -----------------
        #
        # Each carries `ticket`, so the tile greys (#261), the picker and the
        # topic-page row fill in, and the stub page names who will build it --
        # all before a round exists. The game tickets state these values; this
        # only writes them down.
        Activity(
            slug="odd_one_out",
            name="Odd one out",
            kind="game",
            picker_heading="Choose the topics to find the stranger among",
            # Three words from one topic and an intruder from another, so a
            # home topic needs three cards and there must be somewhere else to
            # draw the stranger from. The card count is the weaker half of
            # that; `min_topics` is the half `min_cards` cannot say at all.
            min_cards=4,
            too_small="Tick topics holding at least four cards.",
            min_topics=2,
            too_few_topics="Tick at least two topics — the odd word out has to "
                           "come from somewhere else.",
            tagline="Spot the stranger",
            # Nothing beyond a word and the topic it lives in, which is why
            # this is the one wave-two game that needs no card-level rule.
        ),
        Activity(
            slug="spell_it",
            name="Spell it",
            kind="game",
            picker_heading="Choose the topics to spell words from",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Meaning in, spelling out",
            needs="an English explanation",
            hint_modes=HINTS,
        ),
        Activity(
            slug="rebuild_the_sentence",
            name="Rebuild the sentence",
            kind="game",
            picker_heading="Choose the topics to take sentences from",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Put the words back in order",
            needs="an example sentence of five to fifteen words",
        ),
        Activity(
            slug="listen_and_type",
            name="Listen and type",
            kind="game",
            picker_heading="Choose the topics to listen to",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Hear it, write it",
            needs="a headword a voice can read",
        ),
        Activity(
            slug="read_a_text",
            name="Generate a text",
            kind="reader",
            picker_heading="Choose the topics I should take the words from",
            min_cards=1,
            too_small="Tick at least one topic to write about.",
            tagline="A passage built from your own words",
            words=GENERATED_WORDS,
            asks_instruction=True,
        ),
    )
}


def panel(kind):
    """The activities belonging in one panel, in declaration order.

    Declaration order is deliberate and is the panel's order: #233 puts the
    quiz first, because it is the oldest activity here and the one a returning
    learner is most likely to want.
    """
    return [a for a in ACTIVITIES.values() if a.kind == kind]


# Every activity except the quiz is reached at /games/<slug>. The quiz keeps
# its own URLs because /quiz/<topic> predates all of this and is linked from
# three templates (#250).
GAMES_URL_KINDS = ("game", "reader")


def activity(slug, kind=None):
    """The declared activity for `slug`, or **None** if there is no such thing.

    With `kind`, an activity of the wrong kind is also None: `/games/quiz` is
    not a way to reach the quiz, because the quiz has its own URL and two ways
    in would be two things to keep working.

    None rather than an exception, so a route turns an unknown slug into a 404
    -- which is what every game slug is until its ticket lands.
    """
    found = ACTIVITIES.get(slug)
    if kind is not None and isinstance(kind, str):
        kind = (kind,)
    if found is None or (kind is not None and found.kind not in kind):
        return None
    return found


# --- three words from one topic, and a stranger (#269) -------------------
#
# The one game built on the deck's own *structure* rather than on what a card
# holds. #207 and #215 turned a topic from a text label into a real grouping
# with sections, and this is the cheapest test of whether a puzzle made from
# that lands at all.

# Words drawn from the home topic, beside one intruder.
GROUP = 3

# How many times to try building a question before giving up on the selection.
# A round asks for ten and a thin selection can refuse most draws -- two topics
# of four words each run out quickly -- so this bounds the loop rather than
# letting a hopeless selection spin.
ATTEMPTS = 40


def by_topic(cards):
    """`{topic: [word, ...]}`, deduplicated, skipping anything unusable.

    Deduplicated because #101 keeps a card per word *and part of speech*, so
    `work` the noun and `work` the verb are two cards -- and the same word
    twice among four options is a free mark and looks like a mistake.
    """
    found = {}
    for card in cards:
        topic = (card.get("topic") or "").strip()
        word = (card.get("word") or "").strip()
        if not topic or not word:
            continue
        words = found.setdefault(topic, [])
        if word.casefold() not in {w.casefold() for w in words}:
            words.append(word)
    return found


def _intruder_topics(home, by_topic, sections, rng):
    """Topics a stranger may come from, the better ones first.

    **A topic in a different section is preferred**, which is #236's difficulty
    knob taken at the easy end: *Sport* against *Law* is a fair question, while
    *Business and work* against *Money and finance* is a coin flip. Where the
    selection offers nothing outside the home's section, a same-section
    intruder is accepted rather than refusing to build a question -- a learner
    who ticked two neighbouring topics asked for exactly that.

    `sections` maps a topic to its section name and may be empty, in which case
    every other topic is equally good and the order is simply shuffled.
    """
    others = [t for t in by_topic if t != home]
    rng.shuffle(others)
    if not sections:
        return others
    home_section = sections.get(home)
    far = [t for t in others if sections.get(t) != home_section]
    near = [t for t in others if sections.get(t) == home_section]
    return far + near


def odd_one_out(by_topic, sections=None, rng=None, avoid=()):
    """One question, or **None** if this selection cannot build another.

    Returns `{"words", "answer", "home", "intruder_topic"}` with the four words
    already shuffled, so the caller renders them in order and the position of
    the stranger gives nothing away.

    Two rules keep a question answerable, and both come from the same fact:
    a word can honestly belong to two topics.

    * **An intruder whose word also exists in the home topic is never drawn.**
      #101 keeps one card per word *and part of speech*, so the same word
      really can sit in two topics -- and drawing it as the stranger makes the
      question unanswerable rather than hard.
    * `avoid` holds questions already asked, as frozensets of the four words,
      so a round does not deal the same four twice.

    None rather than a shorter question: three options is a different, easier
    game, and dealing one silently would make the score mean two things.
    """
    rng = rng or random
    homes = [t for t, words in by_topic.items() if len(words) >= GROUP]
    if not homes or len(by_topic) < 2:
        return None
    rng.shuffle(homes)

    for home in homes:
        family = by_topic[home]
        taken = {w.casefold() for w in family}
        for topic in _intruder_topics(home, by_topic, sections or {}, rng):
            strangers = [w for w in by_topic[topic]
                         if w.casefold() not in taken]
            if not strangers:
                continue
            for _ in range(ATTEMPTS):
                three = rng.sample(family, GROUP)
                stranger = rng.choice(strangers)
                words = three + [stranger]
                if frozenset(w.casefold() for w in words) in avoid:
                    continue
                rng.shuffle(words)
                return {"words": words, "answer": stranger, "home": home,
                        "intruder_topic": topic}
    return None


def odd_one_out_round(by_topic, count, sections=None, rng=None):
    """Up to `count` questions, and as many as the selection can build.

    Fewer than asked for is not an error -- two topics of four words each run
    out quickly, and refusing a round because it yields seven questions
    instead of ten would be the wrong call (#235's rule, #266's sentence).
    """
    rng = rng or random
    asked, seen = [], set()
    for _ in range(count):
        question = odd_one_out(by_topic, sections, rng, avoid=seen)
        if question is None:
            break
        seen.add(frozenset(w.casefold() for w in question["words"]))
        asked.append(question)
    return asked

# --- a headword a voice can read (#272) ----------------------------------

# Letters, and the three things that hold a real headword together: a space
# between the words of an expression, a hyphen in `well-being`, an apostrophe
# in `don't`. Anything else -- a bracketed note, a digit, an abbreviation's
# full stops -- is a poor thing to dictate and worse to type back.
SPEAKABLE = re.compile(r"^[A-Za-z]+(?:[ '’-][A-Za-z]+)*$")


def speakable(word):
    """Whether a voice can be asked to read this headword (#272).

    **Multi-word expressions are kept.** "Take for granted" is a perfectly good
    listening question and arguably a better one than a single word, since the
    learner has to catch the unstressed middle -- so the space is allowed
    rather than being the easy thing to exclude.

    Pure, and about the *word* rather than the card, so a caller passes
    `card["word"]` and this needs no database to test.
    """
    return bool(SPEAKABLE.match((word or "").strip()))

# --- one typed-answer path (#267) ----------------------------------------
#
# Four rounds take a typed answer -- the quiz, scrambled, and wave two's spell
# it and listen and type -- and before this there were two copies of "read back
# what was asked" and two different ideas of what counts as the same answer.

# Punctuation a learner types around an answer without meaning it: a trailing
# full stop, quotes pasted with the word, a stray comma. Stripped from both
# ends and not from the middle, because `don't` and `well-being` are spellings
# and `resign.` is a habit.
ANSWER_PUNCTUATION = ".,;:!?\"'`()[]{}«»…-–—"


def normalise_answer(text):
    """A typed answer reduced to what it actually says (#267).

    Trimmed, casefolded, inner whitespace collapsed, hyphens folded to spaces,
    surrounding punctuation stripped. The cases are all things a learner really
    types, and marking any of them wrong teaches nothing about English:

        "Resign "            -> "resign"
        "take  for granted"  -> "take for granted"
        "resign."            -> "resign"
        "well being"         -> "well being"   (and so does "well-being")

    **`resigned` does not become `resign`.** Nothing here touches the middle of
    a word, because that is a different word and these are spelling games. The
    line this draws is between how a phrase was *typed* and how it was
    *spelled*, and only the first is forgiven.

    Deliberately not language-specific. The quiz's Cyrillic `ё`/`е` fold belongs
    to a stored *translation* and stays on the quiz's own path, applied over
    this rather than inside it -- an English headword has no `ё` in it, and a
    rule that fires for one caller does not belong in the shared one.
    """
    folded = str(text or "").replace("-", " ").replace("‑", " ")
    parts = [p.strip(ANSWER_PUNCTUATION) for p in folded.split()]
    return " ".join(p for p in parts if p).casefold()


def same_answer(given, expected):
    """Whether a typed answer matches, both sides normalised the same way.

    Both sides, which is the point: the stored word can carry a hyphen or a
    double space just as easily as the typed one, and normalising only what the
    learner wrote would mark `well-being` wrong for a card spelled that way.
    """
    return normalise_answer(given) == normalise_answer(expected)


def asked(form, by_id, prefix="answer_"):
    """The items that were on the page, in the order their fields arrived.

    A round is a **random sample**, so re-drawing on POST would grade answers
    against words nobody saw. The submitted field names are the only record of
    what was asked, and this is the one place that reads them back.

    In submission order rather than database order, because the results list is
    numbered and a learner reading "3. wrong" has to find the third question
    they answered, not the third alphabetically.

    **Popped, not fetched.** A repeated field -- a doubled submit, a hand-built
    POST -- would otherwise ask the same question twice and score it twice.

    `form` is any mapping that iterates its keys in submission order and
    `by_id` is `{key: item}`, both plain data, so this needs no request context
    and no database to test.
    """
    found = []
    remaining = dict(by_id)
    for key in form:
        if not key.startswith(prefix):
            continue
        item = remaining.pop(key[len(prefix):], None)
        if item is not None:
            found.append(item)
    return found

# --- which cards a game can actually use (#266) --------------------------


def playable(cards, rule):
    """The cards this game can use, paired with what the rule made of them,
    and how many it had to drop.

    `min_cards` is the cheap check the picker can answer from counts alone
    (#248 built it on counts so a page render stays one query). It can only
    ever say *how many cards*, and most games want something a card either
    carries or does not: a translation in the chosen language, an explanation,
    an example that contains its own headword. A learner can tick a topic of
    twenty cards, satisfy `min_cards` comfortably, and meet an empty round --
    of production's 503 cards, 74 have no English explanation and 86 no
    examples, so this is a number people meet rather than a corner case.

    **The rule returns what it made, not just whether it could.** For a card
    the game cannot use it returns None (or False); for one it can, either True
    or something derived on the way -- the scrambled puzzle, the gapped
    sentence. That is what lets one call answer the eligibility question and
    produce its answer: asking "can this be scrambled?" and then "scramble it"
    would either state the rule twice, in two places that drift, or spend the
    random draw twice and shuffle a different word than it tested.

    The count comes back rather than being recomputed, because the caller has
    to print it and `len(cards) - len(kept)` at the call site is the same
    subtraction written a fifth time.

    Pure, and takes the cards rather than fetching them, so every rule in the
    app can be tested without a database.
    """
    kept = []
    for card in cards:
        made = rule(card)
        if made is None or made is False:
            continue
        kept.append((card, made))
    return kept, len(cards) - len(kept)

# --- drawing the round ---------------------------------------------------


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


# --- scrambling a word (#133) --------------------------------------------


def scramble(word, rng=None):
    """A word with its middle letters shuffled, or **None** if it cannot be.

    The Cambridge effect: the first and last letters are held, so the shape a
    reader recognises survives and only the inside is disturbed.

    None rather than the word unchanged, which is the whole subtlety here. A
    three-letter word has no middle to shuffle; `book` has a middle of two
    identical letters; `noon` shuffles to itself whatever the draw. Returning
    the original in those cases would put a word on screen that *is* the
    answer, which is worse than not asking it -- so the caller filters on None
    instead of having to notice.

    `rng` is injectable so a test can pin the shuffle.
    """
    if len(word) < 4:
        return None
    middle = list(word[1:-1])
    # Every arrangement the middle can take, minus the one it already has. If
    # that leaves nothing, no shuffle of this word can differ from it.
    if len(set(middle)) < 2:
        return None
    rng = rng or random
    for _ in range(20):
        shuffled = middle[:]
        rng.shuffle(shuffled)
        if shuffled != middle:
            return word[0] + "".join(shuffled) + word[-1]
    # Vanishingly unlikely, and cheaper to admit than to loop forever.
    return None


def scramble_entry(entry, rng=None):
    """Scramble every word of an entry, or None if none of them could be.

    A card's `word` may be an expression -- "take for granted" -- and each word
    in it is scrambled separately so the phrase keeps its shape and its word
    count. Anything too short or too repetitive is left alone, which is safe
    here in a way it is not for a single word: the round is still a puzzle as
    long as *something* moved.
    """
    parts = entry.split()
    if not parts:
        return None
    scrambled = [scramble(part, rng) for part in parts]
    if not any(scrambled):
        return None
    return " ".join(new or old for old, new in zip(parts, scrambled))


# --- inventing words that could have been real (#132) --------------------
#
# A character n-gram (Markov) model over the deck's own words. The learner is
# shown a mix of real words and invented ones and has to tell them apart, so
# the invented ones have to be *phonotactically* plausible English -- `plimber`
# rather than `xkqrtz` -- which is exactly what a model of which letters follow
# which produces.

VOWELS = set("aeiouy")

# Three characters of context. Two is close to random and makes obvious
# nonsense; four memorises the training words and hands them back, which in a
# deck of a few hundred is the more likely failure. Three is the setting that
# invents rather than recites.
NGRAM_ORDER = 3

# Padding, and the marker that a word has ended. Outside the alphabet on
# purpose so they cannot collide with a letter of a real word.
START = "\x02"
END = "\x03"


def _ngram_model(words, order):
    """`{context: [letters that followed it]}`, with repeats left in.

    Repeats are the weighting: a letter that followed a context four times
    appears four times, so choosing uniformly from the list samples the real
    distribution without counting anything.
    """
    model = {}
    for word in words:
        padded = START * order + word + END
        for i in range(len(word) + 1):
            model.setdefault(padded[i:i + order], []).append(padded[i + order])
    return model


def _invent(model, order, rng, max_length):
    """One word from the model, or None if it ran away.

    None rather than a truncated word: a model can wander into a context whose
    only continuations extend it forever, and half a word is not a plausible
    one.
    """
    context = START * order
    out = []
    while len(out) <= max_length:
        choices = model.get(context)
        if not choices:
            return None
        letter = rng.choice(choices)
        if letter == END:
            return "".join(out)
        out.append(letter)
        context = (context + letter)[-order:]
    return None


# Invented words shorter than this are not offered, and real ones are held to
# the same floor so the length is not a tell in either direction.
#
# Short output is where the model accidentally produces *real* English, and a
# fake that is really a word is marked wrong for being right. Runs over the
# seeded deck offered `out`, `sent` and `qual` at no floor, then `legate` and
# `embark` at six. Seven removes both of those and costs nothing measurable:
# the model still fills every round asked of it, and the deck still has plenty
# of real words this long.
MIN_INVENTED_LENGTH = 7

# An invented word sharing this many opening characters with a real one is
# assumed to be a variant of it rather than a new word. See `pseudowords()`.
STEM_LENGTH = 6


def vocabulary(cards):
    """Every English word the cards themselves contain, for rejecting fakes.

    A free and surprisingly effective dictionary. `explanation_en` and
    `examples_en` are both real English written by a real dictionary (#225), so
    the common words a trigram model stumbles onto are mostly in there --
    examples alone gave only about six hundred words and let `author` through;
    the explanations are the larger half by far.

    Still nowhere near a lexicon, so it narrows the problem rather than solving
    it, and `MIN_INVENTED_LENGTH` covers what is left.
    """
    seen = set()

    def add(text):
        for part in str(text).split():
            seen.add(part.strip(".,!?;:'\"()[]").lower())

    for card in cards:
        add(card.get("word") or "")
        add(card.get("explanation_en") or "")
        for sentence in card.get("examples_en") or ():
            add(sentence)
    return {word for word in seen if word.isalpha()}


def pseudowords(words, count, rng=None, order=NGRAM_ORDER, known=()):
    """`count` invented words that look like they came from `words`.

    Fewer than asked for if the model cannot produce them, which a small or
    repetitive deck really can cause -- the caller plays what it gets rather
    than looping forever.

    Three filters, and each is a way the game gives itself away:

    * **a word the deck already has is not invented**, it is remembered. The
      model is built from these words, so reproducing one is its most likely
      output, and it would be marked wrong for being right. `known` widens that
      to any English the cards contain -- see `vocabulary()` -- because the
      trap is not only the deck's headwords: a run over the seeded deck offered
      `out`, `sent` and `qual`, all real, none of them cards.
    * **no shared stem with a real word.** The model recombines real morphemes,
      so it invents `litigate` from `litigation` and `deposition` from
      `deposit` -- words that are perfectly real and would be marked wrong for
      being right. Rejecting anything sharing `STEM_LENGTH` opening characters
      with a known word is a blunt instrument that removes most of them.
    * **no vowel, no word.** `plimber` is arguable; `blkstr` is not, and a
      learner spots it without knowing any English.
    * length inside the range the real words occupy and at least
      `MIN_INVENTED_LENGTH`, because short output is where the model collides
      with real English. Some rare word will still slip through; this is a
      game, and the alternative is shipping a dictionary.

    Only single words are trained on and returned. An expression's spaces would
    have the model inventing phrases, and "is this a real *word*" is not the
    question being asked about `take for granted`.
    """
    rng = rng or random
    real = [w.lower() for w in words if w and " " not in w and w.isalpha()]
    if not real:
        return []
    forbidden = set(real) | {w.lower() for w in known}
    stems = {w[:STEM_LENGTH] for w in forbidden if len(w) >= STEM_LENGTH}
    longest = max(map(len, real))
    shortest = min(max(MIN_INVENTED_LENGTH, min(map(len, real))), longest)
    model = _ngram_model(real, order)

    found = []
    seen = set()
    # Bounded rather than "until we have enough": a deck of twenty words has
    # only so many words in it, and the alternative is a page that never
    # renders.
    for _ in range(count * 60):
        if len(found) == count:
            break
        word = _invent(model, order, rng, longest)
        if not word or word in forbidden or word in seen:
            continue
        if not (shortest <= len(word) <= longest):
            continue
        if not set(word) & VOWELS:
            continue
        if word[:STEM_LENGTH] in stems:
            continue
        seen.add(word)
        found.append(word)
    return found


# --- typos a learner would actually make (#131) --------------------------
#
# #130 needs wrong answers that are *tempting*, and the cheapest source of a
# tempting wrong answer is the mistake the learner would have made themselves:
# a finger landing one key over, or two letters arriving in the wrong order.
#
# A physical model rather than a hand-written neighbour table, because the
# table is thirty lines of data nobody can proofread and the geometry is four.
# Each row of a QWERTY keyboard is offset from the one above it — that is the
# stagger you can see looking down at the keys — so a key's neighbours are the
# ones whose centres are within a key's width of its own.
#
# English only. #130 shows a Ukrainian or Russian word and asks for the English
# one, so every option is English and every typo is made on a QWERTY keyboard;
# a ЙЦУКЕН model would be a second table with nothing reading it. It arrives
# with the reverse direction, if that is ever built.
QWERTY_ROWS = ("qwertyuiop", "asdfghjkl", "zxcvbnm")

# How far each row sits to the right of the one above, in key widths. These are
# the real offsets of a standard keyboard: `a` sits under the gap between `q`
# and `w`, and `z` under the gap between `a` and `s`, which is what makes
# `a`→`q` a likelier slip than `a`→`e`.
ROW_OFFSETS = (0.0, 0.25, 0.75)

# How far apart two key centres can be and still count as touching. Just over
# one key width, not exactly one: the keys either side of a letter in its own
# row are *exactly* 1.0 away, and they are the neighbours that matter most, so
# a strict `< 1.0` would drop `a`→`s` while keeping `a`→`q`.
KEY_REACH = 1.05


def _keyboard():
    """letter -> the letters whose keys touch it."""
    centres = {}
    for row, letters in enumerate(QWERTY_ROWS):
        for column, letter in enumerate(letters):
            centres[letter] = (row, column + ROW_OFFSETS[row] + 0.5)
    near = {}
    for letter, (row, x) in centres.items():
        near[letter] = tuple(
            other for other, (other_row, other_x) in centres.items()
            if other != letter and abs(other_row - row) <= 1
            and abs(other_x - x) < KEY_REACH)
    return near


KEYBOARD = _keyboard()

# Below this, a typo stops being a typo. `aid` mistyped is `sid` or `aif`, and
# four options that short read as four unrelated words rather than as one word
# and three near misses — the effect #131 exists to produce. The caller takes
# another real word instead, which is why this is a floor and not an error.
MIN_TYPO_LENGTH = 4


def typos(word):
    """Every one-slip misspelling of `word`, in no particular order.

    Two slips, which are the two #131 names: a finger on the neighbouring key,
    and two letters typed in the wrong order. Both are *single* edits, so every
    result is one Damerau-Levenshtein step from the word — the lower half of
    #130's "1–2 edits", and the half that actually looks like a mistake. Two
    edits from a nine-letter word is usually just a different word.

    Single tokens only: an expression's spaces are never mistyped, and a
    transposition buried in "take for granted" is invisible at a glance.
    Returns [] rather than raising for anything it cannot slip, so the caller
    falls back instead of having to check first.
    """
    word = (word or "").strip()
    if len(word) < MIN_TYPO_LENGTH or not word.isalpha():
        return []

    found = []
    lowered = word.lower()
    for index, letter in enumerate(lowered):
        for neighbour in KEYBOARD.get(letter, ()):
            found.append(word[:index] + neighbour + word[index + 1:])
    for index in range(len(word) - 1):
        if lowered[index] == lowered[index + 1]:
            # Swapping a double letter gives the word back — the trap
            # `scramble()` guards against for the same reason: an option
            # identical to the answer would be on screen twice, once marked
            # right and once wrong.
            continue
        found.append(word[:index] + word[index + 1] + word[index]
                     + word[index + 2:])
    return found


def typo(word, avoid=(), known=(), rng=None):
    """One misspelling of `word` that is safe to show, or **None**.

    `avoid` is what is already on the page; `known` is real English, and
    `vocabulary()`'s output serves — which is what #132 built it for. A typo
    that lands on a real word is rejected because it stops being a typo:
    offered beside the answer it is just a second English word, and if it
    happens to be a synonym it is a second *correct* answer.

    None when nothing survives, which the caller reads as "take another real
    word" rather than as a failure.
    """
    blocked = {str(item).casefold() for item in avoid}
    blocked.add(word.casefold())
    real = {str(item).casefold() for item in known}
    candidates = [t for t in typos(word)
                  if t.casefold() not in blocked and t.casefold() not in real]
    if not candidates:
        return None
    return (rng or random).choice(candidates)


# --- building one multiple-choice question (#130) ------------------------


def edit_distance(first, second, cap=None):
    """Levenshtein distance, stopped early once it passes `cap`.

    Hand-written rather than a dependency: this is the whole of it, and the
    alternative is another package in `requirements.txt` for fifteen lines.

    `cap` is what makes it cheap enough to run over the whole deck per
    question. #130 only ever asks "is this within two edits", and for most
    pairs the length difference settles that before a row is computed.
    """
    first, second = first.casefold(), second.casefold()
    if cap is not None and abs(len(first) - len(second)) > cap:
        return cap + 1
    previous = list(range(len(second) + 1))
    for i, a in enumerate(first, 1):
        current = [i] + [0] * len(second)
        for j, b in enumerate(second, 1):
            current[j] = min(previous[j] + 1, current[j - 1] + 1,
                             previous[j - 1] + (a != b))
        if cap is not None and min(current) > cap:
            return cap + 1
        previous = current
    return previous[-1]


# What "1–2 edits" means when picking a real word out of the deck. Two, as #130
# asks — but see `real_distractors()` for why it is a preference and never a
# requirement.
CLOSE_EDITS = 2

# How many answers a question offers, the correct one among them.
OPTIONS = 4


def real_distractors(answer, pool, count, rng=None):
    """Real words from the deck to offer beside `answer`, closest first.

    #130 asks for real card-set words within 1–2 edits and treats synthesis as
    the fallback. Measured against the deck, that is the wrong way round: of
    423 distinct headwords, 49 have even one other word within two edits and
    **two** have three. So this prefers a close word where one exists — #130's
    rule, honoured wherever it can fire — and fills the rest at random rather
    than reporting that it could not.

    Random is not a poor substitute. A wrong answer only has to be a real word
    the learner has met and can reject on meaning; being spelled like the
    answer makes it harder to eliminate at a glance, not more instructive.
    """
    rng = rng or random
    seen = {answer.casefold()}
    unique = []
    for word in pool:
        word = (word or "").strip()
        if word and word.casefold() not in seen:
            seen.add(word.casefold())
            unique.append(word)

    close, far = [], []
    for word in unique:
        near = edit_distance(word, answer, CLOSE_EDITS) <= CLOSE_EDITS
        (close if near else far).append(word)
    rng.shuffle(close)
    rng.shuffle(far)
    return (close + far)[:count]


# How often a question misspells 0, 1, 2 or 3 of its four options (#319).
#
# Weighted heavily toward none: four correctly spelled words is the plain form
# of the game, and a slip reads as an occasional intrusion rather than the norm.
# It also means a learner cannot assume a slip is present, which is what stops
# "find the odd-looking word" being a routine rather than a judgement.
#
# Seven in ten questions have no slip at all, and the rest halve away from
# there. That ratio is what keeps the misspellings cheap: **every slip on the
# page is an option a learner can strike off without knowing the word**, since
# a misspelling is never the correct spelling of the answer. That inference
# cannot be designed away — it is true of any version of this game that
# misspells anything — so the only thing that bounds it is how often a slip is
# there at all.
#
# What a learner with no vocabulary scores, striking off every slip and
# guessing among the rest:
#
#     40 / 30 / 20 / 10   40.0%    (one slip per question on average)
#     exactly one, always 33.3%    (what #130 shipped)
#     70 / 15 / 10 / 5    32.5%    (this -- half a slip per question)
#
# The three case stays in at a twentieth. Since the answer is always spelled
# correctly, three slips leave it the only clean word on the page, so those
# questions are a free mark — but at 5% they cost a point and a half of that
# 32.5%, and they buy the occasional wildly misspelled page, which is a texture
# the game has no other way to produce.
MISSPELLED_WEIGHTS = (70, 15, 10, 5)

# How often the answer goes to the front of the queue of words that might be
# mistyped, ahead of the wrong options.
#
# A slip is shown beside the word it was made from, so the page holds a
# near-identical pair, and what matters is which half of that pair is the
# answer. Sourcing uniformly from the words on the page would make it the
# answer about a third of the time, and a learner who noticed could bet
# against the tidy half twice as often as for it.
#
# **This is a knob, not the resulting rate.** The coin decides queue position
# rather than the outcome: a word that cannot be mistyped is skipped, and with
# three slips wanted the answer is the only word on the page to make the first
# one from. Measured over the deck, the nominal value runs about six points
# below the share of pairs it actually produces —
#
#     nominal   pairs whose tidy half is the answer
#      0.50                 56.0%
#      0.35                 49.8%
#      0.30                 48.0%
#      0.20                 44.3%
#
# 0.30 puts the answer just under half, which is deliberate: it is the word the
# learner is trying to learn, and a misspelling of it is the one slip on the
# page that could teach them the wrong spelling. A wrong option carries no such
# risk, so the two are not worth mistyping equally often.
#
# Not lower than this, though. The gap is what a learner gains by assuming the
# tidy half of a pair is wrong, and it turns in their favour as the answer
# supplies fewer of the pairs: worth -2.3 points at 0.50, -0.1 at 0.35, +0.5
# here, and +2.4 by 0.15. Near half in either direction the bet is worthless,
# which is the property being bought.
ANSWER_SOURCE_CHANCE = 0.30


def misspelled_count(rng=None):
    """How many of a question's options to misspell — 0 to 3 (#319).

    Drawn per **question**, not per round: two questions in the same round
    differ, which is what keeps the count from being something a learner can
    read off the first question and rely on for the rest.
    """
    rng = rng or random
    return (rng or random).choices(
        range(len(MISSPELLED_WEIGHTS)), weights=MISSPELLED_WEIGHTS)[0]


def _real_words(answer, pool, spare, count, rng):
    """Up to `count` real words to build a question from, closest first.

    `pool` before `spare` so a selection furnishes its own question where it
    can, and `real_distractors()` for both so #130's preference for words
    spelled like the answer survives.
    """
    words = real_distractors(answer, pool, count, rng=rng)
    if len(words) < count:
        taken = {answer.casefold()} | {w.casefold() for w in words}
        words += real_distractors(
            answer, [w for w in spare if (w or "").casefold() not in taken],
            count - len(words), rng=rng)
    return words


def _one_slip(answer, shown, hidden, taken, used, known, rng):
    """One misspelling and the word it was made from, or `(None, None)`.

    A word **already on the page** is tried first, because that is the pair
    #319 wants: `custody` beside `custodt` reads exactly like `customs` beside
    `vustoms`, so the shape stops meaning "the tidy one is the answer". The
    answer takes the front of that queue about half the time (see
    `ANSWER_SOURCE_CHANCE`), which is what balances the two kinds of pair.

    `hidden` is real words that are *not* shown, tried only when nothing on the
    page can be mistyped. That is not a rare fallback: with three slips wanted
    there is only one clean word on the page to make them from, so two of them
    have to come from here.

    `used` stops one word supplying two slips — two misspellings of `appraisal`
    side by side is a pair of a different and much sillier kind.
    """
    on_page = [w for w in shown if w not in used]
    rng.shuffle(on_page)
    if answer not in used:
        if rng.random() < ANSWER_SOURCE_CHANCE:
            on_page.insert(0, answer)
        else:
            on_page.append(answer)
    off_page = [w for w in hidden if w not in used]
    rng.shuffle(off_page)

    for source in on_page + off_page:
        slip = typo(source, avoid=taken, known=known, rng=rng)
        if slip:
            return slip, source
    return None, None


def question_options(answer, pool, spare=(), known=(), rng=None):
    """The four answers to one question, shuffled, or **None**.

    Real words from the deck, of which **0 to 3 are misspelled** (#319) by
    #131's keyboard model — and a slip is shown beside the word it was made
    from, which may be the correct answer.

    The history is worth keeping, because two plausible-looking versions of this
    were wrong in opposite directions. #130 shipped mistyping the answer every
    time, which made the round winnable with no English at all: every page held
    a near-identical pair and the tidily spelled half was always what was asked
    for. Excluding the answer removed the pair, but only by making the
    interesting shape never happen — and left a smaller tell in its place, that
    a misspelled option was reliably a wrong one.

    Showing the source beside its slip is what makes the pair *harmless* rather
    than absent. `customs`/`vustoms` and `custody`/`custodt` are the same shape
    and mean opposite things, so a learner reading a pair learns nothing from
    it.

    **The answer's correct spelling is always on the page.** A slip of the
    answer is an extra option, never a replacement, or the question would have
    nothing right to pick — which is also why three slips hand the answer over:
    it is then the only correctly spelled word there.

    The count is a **target, not a guarantee**. A word may be too short to
    mistype (`MIN_TYPO_LENGTH`) or every variant of it may be real English
    rejected through `vocabulary()`, and a question that comes out with fewer
    slips than it drew is simply a plainer question. Any slot a slip could not
    fill is topped up with a real word.

    None when four distinct options cannot be built at all, which is the
    eligibility rule: a three-option question is a different, easier game, and
    dealing one silently would make the score mean two things.
    """
    rng = rng or random
    wanted = misspelled_count(rng)

    # Enough real words for every wrong slot, plus a reserve to make slips from
    # and to fall back on when one cannot be made. Twice the question is ample
    # and costs nothing -- these are strings already in memory.
    reserve = _real_words(answer, pool, spare, 2 * OPTIONS, rng)
    if len(reserve) < OPTIONS - 1:
        return None

    # The clean wrong options come off the front, so they are the ones spelled
    # most like the answer (#130). What is left is available to mistype.
    clean = reserve[:OPTIONS - 1 - wanted]
    hidden = reserve[len(clean):]

    options = [answer] + list(clean)
    used = set()
    while len(options) < OPTIONS:
        slip, source = _one_slip(answer, clean, hidden, options, used, known, rng)
        if slip is None:
            break
        used.add(source)
        options.append(slip)

    # A slip that could not be made leaves a hole. Filling it with a real word
    # is what turns "three slips wanted, one available" into a plainer question
    # rather than a shorter one.
    for word in hidden:
        if len(options) >= OPTIONS:
            break
        if word.casefold() not in {o.casefold() for o in options}:
            options.append(word)

    if len(options) < OPTIONS:
        return None
    rng.shuffle(options)
    return options

# --- finding a headword inside real English (#235, #237) -----------------
#
# Two activities need the same awkward question answered: **where in this
# sentence is this card's word?** #235 cuts the word out of its own example to
# make a gap, and #237 picks the learner's own words out of a generated text to
# put them in bold. Neither can do it with `in` or `str.replace()`, because the
# text almost never holds the headword verbatim: Oxford's sentence for `resign`
# is "He *resigned* from the board", and a model asked to use `apply` writes
# "she applied".
#
# So it is built once, here, and both use it. Two implementations of "find this
# headword in this English sentence" would disagree within a month, and they
# would disagree in opposite directions -- #235 would show a sentence containing
# its own answer, #237 would report a word as unused that is on the screen.
#
# A **light stem match, not lemmatisation**. Regular inflections only: this is a
# card game, and the alternative is shipping a morphological analyser. `resign`
# finds `resigned`, `apply` finds `applies`, `plan` finds `planning`, `create`
# finds `creating`. `take` does not find `took`, and `resign` deliberately does
# not find `resignation` -- that is a different word, and #237 would rather
# report a word as unused than draw a box around something the learner was not
# studying.

# What a headword may be wearing, per stem. Kept separate rather than thrown
# into one list because a stem earns only the endings its own spelling rule
# produces: `appl` may become `applies`, but allowing it everything would also
# let it match `apples`.
#
# Inflections only, no derivations. `work` -> `worker` is a different word (and
# a different part of speech), and #235 gapping `worker` out of a sentence would
# be asking for an answer that is not the card's.
_BASE_ENDINGS = ("ing", "es", "ed", "s", "d", "")
_E_DROP_ENDINGS = ("ing", "ed")       # create -> creating
_Y_ENDINGS = ("ies", "ied")           # apply  -> applies
_DOUBLED_ENDINGS = ("ing", "ed")      # plan   -> planning


def _consonant(word, index):
    """Whether `word[index]` counts as a consonant for the spelling rules.

    The `u` of `qu` does not: it is spelling, not a vowel sound, which is why
    `acquit` doubles to `acquitted` and `equip` to `equipped` where the plain
    consonant-vowel-consonant test says neither should. Found by watching
    `acquit` be reported unused under a text saying "acquitted" twice.
    """
    letter = word[index]
    if letter not in VOWELS:
        return True
    return letter == "u" and index > 0 and word[index - 1] == "q"


def _stems(word):
    """`(stem, endings)` pairs covering the ways `word` regularly changes."""
    base = word.lower()
    pairs = [(base, _BASE_ENDINGS)]
    if len(base) > 2 and base.endswith("e"):
        pairs.append((base[:-1], _E_DROP_ENDINGS))
    if len(base) > 2 and base.endswith("y") and _consonant(base, len(base) - 2):
        pairs.append((base[:-1], _Y_ENDINGS))
    # The doubling rule, roughly: a word ending consonant-vowel-consonant
    # doubles that last consonant before a vowel ending. `plan` -> `planning`,
    # `stop` -> `stopped`. Over-generous on longer words (`visit` -> `visitt`),
    # which costs nothing: a stem no English word spells simply never matches.
    if (len(base) >= 3 and base[-1].isalpha()
            and _consonant(base, len(base) - 1)
            and not _consonant(base, len(base) - 2)
            and _consonant(base, len(base) - 3)):
        pairs.append((base + base[-1], _DOUBLED_ENDINGS))
    return pairs


def _one_word_pattern(word):
    """The regex source matching one word of a headword, however inflected."""
    return "|".join(
        f"{re.escape(stem)}(?:{'|'.join(endings)})" for stem, endings in _stems(word)
    )


# How many words may sit *between* the parts of an expression. English puts the
# object inside the phrase — "take **it** for granted", "make **your mind** up"
# — so a pattern demanding the parts be adjacent finds almost no real use of a
# phrasal expression at all, which would have #237 reporting a word as unused
# while it is on the screen.
#
# Two, and separated by plain words only: a comma or a full stop ends the
# search, so this widens the window rather than letting three words match across
# half a paragraph.
_INFIX_WORDS = 2
_JOIN = rf"\s+(?:\w+\s+){{0,{_INFIX_WORDS}}}"


def word_pattern(word):
    """A compiled pattern finding `word` in English text, or **None**.

    A multi-word expression is matched **whole** -- one pattern across "take for
    granted", not three -- because that is what #235 has to gap out in one piece
    and what #237 has to bold in one piece. Each word of it may inflect and the
    phrase may hold its object, so "takes it for granted" matches as one span;
    irregular forms like "took" do not, which is the documented edge of a stem
    match rather than a bug in it.
    """
    parts = str(word or "").split()
    if not parts:
        return None
    body = _JOIN.join(f"(?:{_one_word_pattern(part)})" for part in parts)
    return re.compile(rf"\b(?:{body})\b", re.IGNORECASE)


def find_word(text, word):
    """Every `(start, end)` in `text` where `word` appears. `[]` if it does not.

    Spans rather than a boolean or a rewritten string, so the two callers can
    each do their own thing with the same answer: #235 replaces one span with a
    gap, #237 wraps every span in bold. `[]` is #235's "this example is not
    eligible" and #237's "the model did not use this word".
    """
    pattern = word_pattern(word)
    if pattern is None:
        return []
    return [match.span() for match in pattern.finditer(text or "")]


def mark_words(text, words):
    """Cut `text` into runs, marking the ones that are a learner's own word.

    Returns `(segments, used, missing)`:

    * `segments` — `(run, is_word)` pairs covering the whole text in order, so
      a template renders them without any HTML being built in Python. Escaping
      stays Jinja's job, which is what keeps a model's output from becoming
      markup.
    * `used` / `missing` — which of `words` the text turned out to contain, in
      the order they were supplied.

    **Used is decided by the match, not by the markup.** A word swallowed by a
    longer overlapping expression is still a word the model used, and saying
    otherwise would be the same unverified claim #237 exists to avoid — just in
    the other direction.

    Overlaps are resolved longest-first so "take for granted" wins over
    "granted" and the page shows one phrase in bold rather than a phrase with a
    darker tail.
    """
    text = text or ""
    spans, used, missing = [], [], []
    for word in words:
        found = find_word(text, word)
        (used if found else missing).append(word)
        spans.extend(found)

    # Longest first at the same start, so the widest match claims the ground.
    spans.sort(key=lambda span: (span[0], -span[1]))
    segments, at = [], 0
    for start, end in spans:
        if start < at:          # inside a span already taken
            continue
        if start > at:
            segments.append((text[at:start], False))
        segments.append((text[start:end], True))
        at = end
    if at < len(text):
        segments.append((text[at:], False))
    return segments, used, missing


# --- putting a sentence back in order (#271) ------------------------------
#
# **This is not #133 with bigger pieces.** Scrambled shuffles letters inside a
# word and trains spelling; this shuffles words inside a sentence and trains
# *word order*, which is where Ukrainian- and Russian-speaking learners
# actually lose marks -- both first languages permit orders English does not,
# so a sentence that feels perfectly natural to write comes out wrong.

# A sentence has to be the right size to be a puzzle. Four words is not one;
# twenty-five is an afternoon.
SENTENCE_MIN = 5
SENTENCE_MAX = 15

# The marks that end a sentence rather than belonging to a word. Stripped,
# because a full stop travelling with the last token marks it as the last
# token -- half the giveaway, removed for nothing.
TERMINAL = ".!?…"


def sentence_tokens(sentence):
    """The words of `sentence` as chips, or **None** if it will not do.

    **Internal punctuation stays attached to its token.** A comma inside a
    clause is part of where that clause goes, and moving it separately is how a
    real sentence becomes a wrong one -- so `.split()` is the whole tokeniser,
    deliberately.

    **The terminal mark comes off.** It is punctuation rather than a word, and
    a chip reading `problems.` announces itself as the end of the sentence.

    **The opening capital stays.** Lowercasing the first token would mangle
    every sentence that opens with a proper noun or *I*, and the app cannot
    reliably tell which those are. A learner who uses the capital to find the
    start has still had to order everything after it, which is the exercise --
    a known hint is better than a clever rule that prints `she` as `She`
    somewhere in the middle of the pool.
    """
    text = str(sentence or "").strip().rstrip(TERMINAL).strip()
    tokens = text.split()
    if not (SENTENCE_MIN <= len(tokens) <= SENTENCE_MAX):
        return None
    return tokens


def shuffle_tokens(tokens, rng=None):
    """`tokens` in a different order, or **None** if they cannot differ.

    None rather than the original, which is the same subtlety `scramble()`
    documents: a pool that happens to come out in the right order is a question
    whose answer is already on screen, and returning it would leave the caller
    to notice.

    Vanishingly unlikely for a real sentence -- it needs every token identical
    -- but the loop is bounded rather than trusting that.
    """
    rng = rng or random
    if len(set(tokens)) < 2:
        return None
    for _ in range(20):
        shuffled = list(tokens)
        rng.shuffle(shuffled)
        if shuffled != list(tokens):
            return shuffled
    return None


def rebuildable(examples, rng=None):
    """`(sentence, chips)` from the first usable example, or **None**.

    `sentence` is the answer -- the tokens joined with single spaces, which is
    what the assembled string is compared against -- and `chips` is the pool.

    The examples are tried in random order, so a card with three usable
    sentences is not the same question every round; #225 gave cards their
    English examples and 86 of production's 503 have none at all, so plenty of
    cards yield nothing here and the caller moves on.
    """
    rng = rng or random
    usable = [str(s).strip() for s in (examples or []) if str(s or "").strip()]
    rng.shuffle(usable)
    for candidate in usable:
        tokens = sentence_tokens(candidate)
        if not tokens:
            continue
        chips = shuffle_tokens(tokens, rng)
        if chips:
            return " ".join(tokens), chips
    return None

# --- the meaning, and how the word starts (#270) --------------------------
#
# The direction the site does not otherwise test. The quiz goes from an English
# word to a translation, the deck shows a word and reveals its meaning, #235
# hides a word inside its own sentence. Nothing goes from *meaning* to
# *spelling*, which is the harder direction and the one that fails in an exam.

# What the learner is shown of the word. `first` is the default; `first_last`
# is the easier one, and is a mode rather than a toggle on the round page --
# switching mid-round would let a learner reveal the last letter of a word they
# are stuck on, which is not a hint, it is the answer arriving late.
# Below this a word is not a spelling exercise at B2-C1. With the last letter
# shown as well, a four-letter word is two-thirds given, so that mode asks for
# one more. Stated here rather than left for the caller to notice.
MIN_SPELLED = {HINT_FIRST: 4, HINT_FIRST_LAST: 5}

# How long a word has to be before the *last* letter is worth showing as well.
# Four leaves at least two letters hidden, which is the point at which a hint
# is still a hint.
MIN_LAST_LETTER = 4

# One dash per letter, spaced so the count is readable at a glance. Spaces
# between the dashes rather than a run of underscores, because `_______` is
# uncountable and the number of letters is the whole of what the mask gives.
DASH = "_"


def hint_mode(raw, remembered=None, allowed=HINTS, default=HINT_FIRST):
    """Which hint mode a round runs in, from a query parameter.

    Anything unrecognised falls back to `remembered` and then to `default` --
    the same "a stored value is a hint" rule the topic selection and the round
    length both follow, and for the same reason: this arrives from a URL
    anybody can edit.

    `allowed` and `default` are arguments rather than constants because the two
    games that use this genuinely differ: *Spell it* offers two modes and
    starts on the first letter, *Fill the gap* offers three and starts on
    none. A shared fallback would have given one of them a hint it never asked
    for.
    """
    for candidate in (raw, remembered):
        if candidate in allowed:
            return candidate
    return default


def remembered_hint(store, key=HINT_KEY, allowed=HINTS, default=HINT_FIRST):
    return hint_mode(store.get(key), allowed=allowed, default=default)


def remember_hint(store, mode, key=HINT_KEY, allowed=HINTS,
                  default=HINT_FIRST):
    """Remember the mode this visitor chose.

    **A key per game, not one shared key.** Sharing was considered and refused:
    the valid sets differ, and asking for the last letter in *Spell it* would
    silently soften *Fill the gap*, which is a different game the learner did
    not touch.
    """
    store[key] = hint_mode(mode, allowed=allowed, default=default)


def mask_word(word, hint=HINT_FIRST):
    """`unusual` as `u _ _ _ _ _ _`, or `u _ _ _ _ _ l` in `first_last`.

    **The length is shown, deliberately** -- one dash per letter. That is the
    exact opposite of #235's fixed-width gap, and the two are right for
    opposite reasons: there the answer is a *meaning* the learner has to
    retrieve from nothing, so a gap sized to the word is a free hint; here they
    have already been told the meaning and are being asked to spell it, and the
    number of letters is not the answer. A speller who knows the word gains
    nothing from the dash count; one who does not will not guess it from seven.

    A multi-word headword is masked word by word with the spaces kept, so the
    shape of the expression survives -- `take for granted` stays visibly three
    words.
    """
    parts = str(word or "").split()
    masked = []
    for part in parts:
        letters = list(part)
        shown = [letters[0]] if letters else []
        shown += [DASH] * max(0, len(letters) - 1)
        # The last letter only where the part can afford to lose it. The
        # headword's own floor (`MIN_SPELLED`) cannot cover this, because an
        # expression is masked **part by part**: `take for granted` clears that
        # floor comfortably and would still have rendered `for` as `f _ r`,
        # handing over a whole word of the answer. Short parts keep the first
        # letter only.
        if hint == HINT_FIRST_LAST and len(letters) >= MIN_LAST_LETTER:
            shown[-1] = letters[-1]
        masked.append(" ".join(shown))
    return "   ".join(masked)


def spellable(word, hint=HINT_FIRST):
    """Whether this headword makes a spelling question in this mode.

    Alphabetic, plus the space of an expression, the hyphen of `well-being` and
    the apostrophe of `don't` -- a bracketed note or an abbreviation cannot be
    typed reliably and is not a spelling question. Reuses #272's rule for that,
    since "letters a person can type back" is the same question both games ask.

    Then long enough to be worth asking, which depends on the mode: see
    `MIN_SPELLED`.
    """
    word = str(word or "").strip()
    if not speakable(word):
        return False
    letters = sum(1 for ch in word if ch.isalpha())
    return letters >= MIN_SPELLED.get(hint, MIN_SPELLED[HINT_FIRST])


def mask_in_text(text, word, hint=HINT_FIRST):
    """`text` with every occurrence of `word` masked the same way.

    Oxford's explanations routinely contain the headword or an inflection of
    it: "the act of resigning from a position" prints the answer above the
    dashes. So the explanation is masked with **the same matcher #235 and #237
    use** -- case-insensitive, common inflections, a multi-word expression
    taken whole.

    Unlike #235, a card is **not** made ineligible when the word appears.
    Masking is enough, and the masked occurrence is a second dash-run in the
    sentence, which is a *harder* prompt rather than a broken one.

    Every occurrence, not the first: an explanation really can use the word
    twice, and masking one of them would print the answer beside its own mask.
    """
    text = str(text or "")
    spans = find_word(text, word)
    if not spans:
        return text
    out, last = [], 0
    for start, end in spans:
        out.append(text[last:start])
        # Masked to the length of what was found rather than of the headword,
        # so an inflection is covered completely -- `resigning` must not leave
        # its `ing` showing beside the dashes.
        out.append(mask_word(text[start:end], hint))
        last = end
    out.append(text[last:])
    return "".join(out)

# --- cutting a word out of its own example (#235) ------------------------
#
# The card deck shows a word and reveals its meaning; this shows one of the
# card's own example sentences with that word cut out, and reveals the word.
#
# It rests entirely on `find_word()` above, which is why that was built as a
# shared helper rather than inside #237: a naive `sentence.replace(word, ...)`
# finds nothing in "He resigned from the board" and leaves the sentence whole,
# which hands the learner the answer.

# A fixed run, never one sized to the answer. A gap as long as the word is a
# free hint, and the flip is supposed to be the reveal. Cheap to lengthen if it
# turns out to be too hard in practice.
GAP = "______"

# The run shown between a hinted gap's letters. **Three, fixed, whatever the
# word** -- the whole point of #334 is that the length is not given away, and
# `r______n` between two real letters invites counting to eight however fixed
# the six actually is. Three is plainly too short to be most words, so it reads
# as a mark rather than a measurement, and every gap in a round is the same
# width, which settles it after the first one.
#
# Underscores rather than an ellipsis, so the hinted and unhinted modes look
# like the same game: `______` and `t___d` are visibly a hole to fill, where
# `t…d` reads as truncation. Never three dots -- those *are* countable and
# would be read as three letters, the worst of both.
GAP_RUN = "___"


def mask_gap(text, hint=HINT_NONE):
    """`text` as a gap, showing as much as `hint` asks for (#334).

    `none` is the plain fixed-width gap this game has always shown, so the
    default reproduces #235 exactly and a learner who never opens the control
    sees no change at all.

    Otherwise **word by word**, keeping the spaces, in the shape *Spell it*
    uses -- but with the letter counts removed:

        take for granted   ->   t___e   f___   g___d

    So the word count shows and the letter count does not. That is a real step
    back from hiding the length, taken deliberately: the shape of an expression
    is part of recognising it, and a gap that hides even the word count makes a
    collocation nearly unguessable rather than merely hard.

    A part below `MIN_LAST_LETTER` keeps its last letter hidden however the
    mode is set -- `for` shows `f___`. Both ends of a three-letter word is most
    of the word, whether or not the reader knows how short it is.
    """
    parts = str(text or "").split()
    if hint == HINT_NONE or not parts:
        return GAP
    masked = []
    for part in parts:
        letters = [ch for ch in part if ch.isalpha()]
        if not letters:
            masked.append(GAP_RUN)
            continue
        shown = letters[0] + GAP_RUN
        if hint == HINT_FIRST_LAST and len(letters) >= MIN_LAST_LETTER:
            shown += letters[-1]
        masked.append(shown)
    return "   ".join(masked)


def gap_sentence(sentence, word, hint=HINT_NONE):
    """`sentence` with `word` cut out, or **None** if it is not in there.

    **Every** occurrence goes, not just the first. A sentence that uses the
    word twice would otherwise print the answer beside its own gap, which is
    the one thing this game must never do — and it is why the caller can treat
    None as "not eligible" without also having to check what came back.

    A multi-word expression is one gap rather than one per word, because
    `find_word()` matches it whole (object and all: "takes **it** for
    granted").
    """
    spans = find_word(sentence, word)
    if not spans:
        return None
    out, at = [], 0
    for start, end in spans:
        out.append(sentence[at:start])
        # Masked from **what was matched**, not from the headword: the span may
        # be an inflection, and with an expression it carries the object too
        # ("takes **it** for granted"), which per-word masking then shows as
        # its own chunk. That is more structure than the headword alone
        # implies, and it is the honest rendering of what has been cut out.
        out.append(mask_gap(sentence[start:end], hint))
        at = end
    out.append(sentence[at:])
    return "".join(out)


def gapped_example(examples, word, rng=None, hint=HINT_NONE):
    """One of `examples` with `word` cut out, or **None** if none will do.

    The examples are tried in random order, so a card with three usable
    sentences is not the same question every round.

    A card whose examples never contain its own headword is simply not
    playable — #225 gave cards their English examples, and 86 of production's
    503 cards still have none at all. The caller moves on to another card
    rather than showing a sentence with nothing cut out of it.
    """
    usable = [str(s).strip() for s in (examples or []) if str(s or "").strip()]
    (rng or random).shuffle(usable)
    for sentence in usable:
        gapped = gap_sentence(sentence, word, hint)
        if gapped:
            return gapped
    return None
