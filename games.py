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
