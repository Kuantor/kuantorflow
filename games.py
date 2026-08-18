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
        ),
        Activity(
            slug="multiple_choice",
            name="Multiple choice",
            kind="game",
            picker_heading="Choose the topics to be tested on",
            # Four answers to a question, so four cards before one can be
            # built. The exact rule -- four *distinct* translations -- is
            # #130's, asked on its own page where it has the cards.
            min_cards=4,
            too_small="Tick topics holding at least four cards.",
            tagline="Pick from four",
            ticket="#130",
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
        ),
        Activity(
            slug="scrambled",
            name="Scrambled",
            kind="game",
            picker_heading="Choose the topics to scramble words from",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Unscramble the letters",
        ),
        Activity(
            slug="fill_the_gap",
            name="Fill the gap",
            kind="game",
            picker_heading="Choose the topics to take sentences from",
            min_cards=1,
            too_small="Tick at least one topic to start.",
            tagline="Guess the missing word",
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

import re

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


def gap_sentence(sentence, word):
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
        out.append(GAP)
        at = end
    out.append(sentence[at:])
    return "".join(out)


def gapped_example(examples, word, rng=None):
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
        gapped = gap_sentence(sentence, word)
        if gapped:
            return gapped
    return None
