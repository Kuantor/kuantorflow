"""The starting vocabulary `seed_topics.py` builds cards from (issue #203).

**Content, checked in as data.** Deliberately not generated at run time: a list
that came from a model on each run would give the local database and
PythonAnywhere's *different* decks, could not be reviewed in a pull request, and
would keep adding new words for ever instead of settling. A bad word here is
fixed by an edit and a diff.

**The level lives in the words, not the headings.** Every topic below is one a
B1 learner recognises; the words are ones they would not. Under "Environment and
climate" that means *mitigate* and *depletion*, not *tree* and *rain*. That
entry criterion is why there is no "Advanced Vocabulary" topic — it would be a
level pretending to be a theme, and would duplicate all eighteen others (see the
#138 analysis on #203).

**Order is load-bearing, twice.**

1. It is the order the script looks words up in. 360 lookups over the network
   *will* be interrupted, and because a re-run only adds what is missing, an
   interrupted run leaves a prefix. Everyday-and-exam topics come first so that
   prefix is the useful half.
2. It becomes `topics.position` within the section (#215), so it is also the
   order the tiles appear in on the browse page. A section that really is
   ordered numbers its topics from 1, and this is that section.

A dict, because Python keeps insertion order and this reads better than a list
of pairs — but it is an *ordered* mapping and nothing may sort it.

Words are lower case and single-token by design: `lookup_word()` sends each to a
translator and a dictionary, and a multi-word phrase is not what either is for.
No word appears under two topics — deduplication is global by word + part of
speech (#101), so a repeat would simply be skipped under the second topic and
the count would silently not add up. `python seed_topics.py --check` proves it.
"""

# Topic name -> its twenty words. See the module docstring before reordering.
SEED_WORDS = {
    "Work and careers": [
        "appraisal", "delegate", "headhunt", "incentive", "mentor",
        "outsource", "promotion", "redundancy", "resign", "retention",
        "shortlist", "stagnate", "streamline", "subordinate", "tenure",
        "understaffed", "vacancy", "vocation", "workload", "burnout",
    ],
    "Daily life and routines": [
        "chore", "commute", "declutter", "errand", "pantry",
        "spotless", "laundry", "linger", "mundane", "oversleep",
        "procrastinate", "punctual", "rummage", "sluggish", "meticulous",
        "unwind", "upkeep", "groggy", "wander", "habitual",
    ],
    "Education and study": [
        "coursework", "cram", "curriculum", "dissertation", "enrol",
        "expel", "grasp", "invigilate", "literacy", "memorise",
        "plagiarism", "quotation", "recite", "revision", "scholarship",
        "seminar", "syllabus", "truancy", "tuition", "undergraduate",
    ],
    "Health and medicine": [
        "ailment", "chronic", "contagious", "diagnosis", "dosage",
        "fatigue", "immunity", "inflammation", "nausea", "outbreak",
        "prescription", "prognosis", "recuperate", "referral", "relapse",
        "sedentary", "symptom", "therapy", "vaccination", "wellbeing",
    ],
    "Social interaction and small talk": [
        "acquaintance", "banter", "blunt", "chatty", "condescending",
        "courtesy", "eavesdrop", "etiquette", "flattery", "gossip",
        "hedge", "interrupt", "mingle", "outspoken", "rapport",
        "reticent", "affable", "tactful", "withdrawn", "gregarious",
    ],
    "Technology and the internet": [
        "algorithm", "bandwidth", "breach", "metadata", "encryption",
        "firewall", "glitch", "interface", "malware", "obsolete",
        "outage", "paywall", "phishing", "prompt", "seamless",
        "streaming", "subscription", "troubleshoot", "latency", "scalable",
    ],
    "Money and the economy": [
        "collateral", "arrears", "austerity", "creditor", "dividend",
        "expenditure", "frugal", "inflation", "instalment", "invoice",
        "lucrative", "subsidy", "overdraft", "recession", "reimburse",
        "revenue", "splurge", "surplus", "taxation", "thrifty",
    ],
    "Travel and tourism": [
        "accommodation", "backpacking", "transit", "onward", "customs",
        "diversion", "excursion", "expedition", "itinerary", "disembark",
        "layover", "secluded", "overbooked", "embassy", "picturesque",
        "sightseeing", "souvenir", "stopover", "turbulence", "voyage",
    ],
    "Food and lifestyle": [
        "additive", "appetite", "bland", "cuisine", "palatable",
        "edible", "ferment", "garnish", "leftover", "marinate",
        "nourishing", "nutrition", "organic", "portion", "processed",
        "savoury", "seasoning", "simmer", "stale", "vegetarian",
    ],
    "City life and housing": [
        "affordability", "amenity", "commuter", "congestion", "deposit",
        "gentrify", "landlord", "lease", "mortgage", "neighbourhood",
        "outskirts", "pavement", "pedestrian", "renovate", "residential",
        "sprawl", "suburb", "tenant", "utilities", "zoning",
    ],
    "Relationships and emotions": [
        "affection", "apprehensive", "bicker", "compassion", "confide",
        "empathy", "estranged", "grief", "grudge", "infatuated",
        "jealousy", "loathe", "nostalgic", "reconcile", "resentment",
        "sympathy", "trustworthy", "vulnerable", "yearn", "betray",
    ],
    "Media and the news": [
        "anchor", "bias", "bulletin", "censorship", "coverage",
        "editorial", "exclusive", "headline", "hoax", "journalism",
        "leak", "outlet", "propaganda", "publicity", "retract",
        "scandal", "sensational", "subscriber", "tabloid", "verify",
    ],
    "Environment and climate": [
        "biodiversity", "carbon", "conservation", "deforestation", "depletion",
        "drought", "emission", "endangered", "erosion", "extinction",
        "greenhouse", "habitat", "landfill", "mitigate", "offset",
        "contaminate", "compost", "renewable", "sustainable", "wildlife",
    ],
    "Science and research": [
        "analysis", "correlation", "empirical", "inference", "experiment",
        "findings", "hypothesis", "laboratory", "methodology", "observation",
        "anomaly", "plausible", "quantify", "replicate", "sample",
        "specimen", "statistics", "theory", "validate", "variable",
    ],
    "Crime and justice": [
        "acquit", "alibi", "arson", "bail", "burglary",
        "convict", "custody", "defendant", "evidence", "fraud",
        "guilty", "interrogate", "lawsuit", "offence", "parole",
        "prosecute", "sentence", "suspect", "testimony", "verdict",
    ],
    "Politics and society": [
        "abstain", "ballot", "campaign", "candidate", "coalition",
        "constituency", "corruption", "democracy", "diplomacy", "electorate",
        "inequality", "legislation", "lobby", "incumbent", "manifesto",
        "petition", "policy", "referendum", "reform", "sanction",
    ],
    "Art and culture": [
        "abstract", "aesthetic", "acclaim", "choreography", "composition",
        "critic", "curator", "exhibition", "genre", "heritage",
        "improvise", "masterpiece", "mural", "narrative", "figurative",
        "rehearsal", "sculpture", "spectator", "evocative", "venue",
    ],
    "Sport and competition": [
        "amateur", "prowess", "doping", "endurance", "fixture",
        "forfeit", "handicap", "sideline", "knockout", "opponent",
        "penalty", "qualifier", "referee", "contender", "stamina",
        "substitute", "tactics", "tournament", "underdog", "gruelling",
    ],
}

WORDS_PER_TOPIC = 20


def duplicates():
    """Words appearing under more than one topic: `{word: [topics]}`.

    Deduplication is global by word + part of speech (#101), so a repeat is not
    a crash — it is a card silently filed under whichever topic reached it first,
    and a second topic quietly twenty words short. Cheaper to catch here.
    """
    seen = {}
    for topic, words in SEED_WORDS.items():
        for word in words:
            seen.setdefault(word, []).append(topic)
    return {word: topics for word, topics in seen.items() if len(topics) > 1}


def problems():
    """Everything wrong with the list above, as a list of complaints.

    Read by `seed_topics.py --check` and by the test suite, so the shape of the
    data is asserted in one place rather than in each.
    """
    found = []
    for topic, words in SEED_WORDS.items():
        if len(words) != WORDS_PER_TOPIC:
            found.append(f"{topic}: {len(words)} words, expected {WORDS_PER_TOPIC}")
        if len(set(words)) != len(words):
            repeated = sorted({w for w in words if words.count(w) > 1})
            found.append(f"{topic}: repeats {', '.join(repeated)}")
        for word in words:
            if not word.isalpha() or word != word.lower():
                found.append(f"{topic}: {word!r} is not a single lower-case word")
    for word, topics in sorted(duplicates().items()):
        found.append(f"{word!r} appears under {len(topics)}: {', '.join(topics)}")
    return found
