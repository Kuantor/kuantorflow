"""My progress (#493): a learner's own report, built from what the site keeps.

Nothing here is recorded for it. Every checked answer of a signed-in learner
is already in `recall_answers` (#338) and every word they have answered has a
`recall_schedule` row (#479); this turns the two into a page the learner can
read and -- through the browser's *Save as PDF* -- send to a teacher. The
student decides who sees it, which is why the app needs no teacher role (#492
is that road, kept for a class-wide view).

**Pure, like `recall.py`.** Rows in, a report out: no request, no database.
The route reads the rows and the visible deck and hands them over, so every
number on the page is a function over plain data that a test can pin.

**The statuses.** A word's state is one of three, decided by its schedule:

* **known** -- its interval has reached a week (`KNOWN_INTERVAL`), SM-2's own
  measure of "will still know it next week";
* **struggling** -- forgotten twice or more (`STRUGGLING_LAPSES`) and not yet
  back to a week. The second half matters: a word that lapsed twice in its
  first month and has since climbed to forty days is known, not struggling;
* **learning** -- everything else answered.

**Due today is not a fourth state** but a count across them, because a known
word can be due: ticket #493 listed it beside the three, and as one exclusive
group its count would disagree with the *Review* button, which counts every
due word whatever its state.
"""

from collections import defaultdict
from datetime import timedelta

import recall

KNOWN_INTERVAL = 7
STRUGGLING_LAPSES = 2
ACTIVITY_DAYS = 14

# Display order: what needs attention first.
STATUSES = ("struggling", "learning", "known")

STATUS_KEY = ("Known: you will be asked again in a week or more. "
              "Learning: answered, with a shorter gap. "
              "Struggling: forgotten twice or more and not yet back to a week. "
              "Due today: next asked today or earlier.")


def status(interval_days, lapses):
    """One word's state from its schedule row (see the module docstring)."""
    if interval_days >= KNOWN_INTERVAL:
        return "known"
    if lapses >= STRUGGLING_LAPSES:
        return "struggling"
    return "learning"


def _round_key(answer):
    """A round is the instant its rows share plus the game (#338): one INSERT
    per round, so every row of it carries one `UTC_TIMESTAMP()`."""
    return answer["answered_at"], answer["game"]


def build(schedule, answers, word_topics, today, topics=None):
    """The whole report.

    `schedule` -- this learner's `recall_schedule` rows as dicts (`word`,
    `pos`, `reps`, `lapses`, `interval_days`, `due_on`).
    `answers` -- their `recall_answers` rows as dicts (`word`, `pos`, `game`,
    `correct`, `answered_at`, UTC).
    `word_topics` -- `{word_key: [topic, ...]}` from the visible deck, in page
    order; a word whose card is gone has none.
    `topics` -- the learner's filter, or None for everything. Every section
    narrows to the words of those topics.
    """
    wanted = set(topics) if topics else None

    def kept(key):
        return wanted is None or bool(wanted & set(word_topics.get(key, ())))

    answered = defaultdict(lambda: [0, 0])          # key -> [times, right]
    for answer in answers:
        key = recall.word_key(answer["word"], answer["pos"])
        answered[key][0] += 1
        answered[key][1] += bool(answer["correct"])

    words = []
    for row in schedule:
        key = recall.word_key(row["word"], row["pos"])
        if not kept(key):
            continue
        times, right = answered.get(key, (0, 0))
        words.append({
            "word": row["word"], "pos": row["pos"] or "",
            "status": status(row["interval_days"], row["lapses"]),
            "due_on": row["due_on"],
            "due": recall.is_due(row["due_on"], today),
            "lapses": row["lapses"],
            "times": times, "right": right,
            "topics": list(word_topics.get(key, ())),
        })
    words.sort(key=lambda w: (STATUSES.index(w["status"]), w["due_on"],
                              w["word"].casefold()))

    summary = {name: 0 for name in STATUSES}
    for word in words:
        summary[word["status"]] += 1
    summary["due"] = sum(1 for word in words if word["due"])
    summary["words"] = len(words)

    by_topic = defaultdict(lambda: dict.fromkeys(STATUSES + ("due",), 0))
    order = []
    for word in words:
        for topic in word["topics"]:
            if wanted is not None and topic not in wanted:
                continue
            if topic not in by_topic:
                order.append(topic)
            by_topic[topic][word["status"]] += 1
            by_topic[topic]["due"] += word["due"]

    kept_answers = [a for a in answers
                    if kept(recall.word_key(a["word"], a["pos"]))]
    return {
        "summary": summary,
        "words": words,
        "by_topic": [(topic, by_topic[topic]) for topic in order],
        "activity": activity(kept_answers, today),
        "games": per_game(kept_answers),
        "first_day": min((recall.learner_day(a["answered_at"])
                          for a in kept_answers), default=None),
    }


def activity(answers, today, days=ACTIVITY_DAYS):
    """The last `days` learner-days with any answers, newest first:
    `[{day, rounds, answers, right}, ...]`, plus how many days were active."""
    first = today - timedelta(days=days - 1)
    per_day = defaultdict(lambda: {"rounds": set(), "answers": 0, "right": 0})
    for answer in answers:
        day = recall.learner_day(answer["answered_at"])
        if first <= day <= today:
            entry = per_day[day]
            entry["rounds"].add(_round_key(answer))
            entry["answers"] += 1
            entry["right"] += bool(answer["correct"])
    rows = [{"day": day, "rounds": len(entry["rounds"]),
             "answers": entry["answers"], "right": entry["right"]}
            for day, entry in sorted(per_day.items(), reverse=True)]
    return {"days": days, "active": len(rows), "rows": rows}


def per_game(answers):
    """Rounds, answers and right answers per game, all time, most played
    first. A self-marked game (*Fill the gap*) is flagged: its "right" is the
    learner's own tick, which the schedule already weighs lower (#484)."""
    totals = defaultdict(lambda: {"rounds": set(), "answers": 0, "right": 0})
    for answer in answers:
        entry = totals[answer["game"]]
        entry["rounds"].add(_round_key(answer))
        entry["answers"] += 1
        entry["right"] += bool(answer["correct"])
    rows = [{"game": game, "rounds": len(entry["rounds"]),
             "answers": entry["answers"], "right": entry["right"],
             "self_marked": game in recall.SELF_MARKED_GAMES}
            for game, entry in totals.items()]
    rows.sort(key=lambda row: (-row["rounds"], row["game"]))
    return rows


def percent(right, total):
    """Accuracy as a whole percentage, or None with nothing to divide."""
    return round(100 * right / total) if total else None
