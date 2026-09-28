"""The SM-2 schedule, derived from the answer log (#479).

**Pure**: a list of one word's answers in, that word's schedule out. No
database, no request -- the same line #389 drew for `games.py` -- so the rules
are testable on plain lists, and so the live update after a round and
`scripts/rebuild_schedule.py` are **the same function**. That is the whole
reason #338 keeps a log: the schedule is a cache of it, and a cache computed
two ways drifts. Change a rule here, run the rebuild, and every learner's
schedule is what the new rule would always have said -- which is how Anki moved
everyone to FSRS, by replaying `revlog`.

SM-2 (Wozniak, 1987) on **verified pass/fail** rather than a 0-5 self-grade,
settled on #338 on 18 September: a pass gives 1 day, then 6, then
`round(interval * ease)`; a fail resets to 1 day and counts a lapse. Ease
starts at 2.5, a fail takes 0.2 off it -- Anki's own figure -- and it never
falls below 1.3. A pass leaves ease alone (SM-2's q = 4), so with booleans ease
can only fall. That is conservative, not wrong: the cost is reviewing an easy
word more often than needed, never losing one. #481 is where it gets measured.

Four rules the original algorithm does not have, each because of what this app
is rather than what a flashcard is:

1. **One answer per learner-day moves the schedule** -- the first. Three rounds
   in an afternoon would otherwise take a word 1 -> 6 -> 15 days, which is the
   opposite of spacing. The first answer of the day is the one that measures
   recall after the gap; the later ones measure recall minutes after seeing
   the answer, which is #337's job.
2. **A day is Kyiv's, and it turns over at 04:00** (Anki's default rollover),
   because the learners are in Ukraine and the log is in UTC -- a UTC midnight
   falls in the middle of somebody's late session.
3. **A pass before the word is due is practice, not a review.** The games deal
   any word on any day, and #480 only makes due words *likelier*. If an early
   pass advanced the schedule, a word played on four consecutive days would
   reach 37 days without its spacing ever being tested. So an early pass
   changes nothing -- and an early *fail* is a real lapse, since forgetting
   sooner than scheduled is exactly what a lapse is.
4. **A self-marked answer is weaker evidence** (#484). *Fill the gap* is
   ticked by the learner, not checked by the site. On a day with any checked
   answer, the first checked one decides and the ticks are ignored. On a day
   with only ticks: an unticked card is a full lapse -- "I did not remember
   it" is an honest signal -- while a tick is a *weak pass*: half the growth a
   checked pass would give, and never past `WEAK_PASS_CAP` on its own.
"""

import math
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone

import games

START_EASE = 2.5
MIN_EASE = 1.3
LAPSE_EASE_PENALTY = 0.2
# Anki's default maximum interval, 100 years: past it a word is simply known.
MAX_INTERVAL = 36500
# How far ticks alone can carry a word (#484). Six days is SM-2's second step,
# so a word only ever ticked comes back weekly until a checked game confirms it.
WEAK_PASS_CAP = 6
# The learner's day starts at 04:00 local time.
DAY_ROLLOVER = timedelta(hours=4)

# The games whose answers are the learner's own marks, from the one
# declaration of them (`Activity.self_marked`). A slug the declaration no
# longer holds -- a game since removed -- counts as checked, which is what
# every game but Fill the gap has always been.
SELF_MARKED_GAMES = frozenset(slug for slug, activity in games.ACTIVITIES.items()
                              if activity.self_marked)


def _learners_zone():
    """Kyiv, however this machine spells it.

    `Europe/Kyiv` is the current name; a system time-zone database older than
    2022 knows only `Europe/Kiev`. Windows has no database at all, which is
    what the `tzdata` requirement is for. If none of them answers, a fixed
    UTC+2 is used: wrong by an hour in summer, which moves the 04:00 rollover
    to 05:00 and changes nothing a learner could notice -- better than a
    schedule that cannot be computed.
    """
    try:
        from zoneinfo import ZoneInfo, ZoneInfoNotFoundError
    except ImportError:                       # pragma: no cover -- Python < 3.9
        return timezone(timedelta(hours=2))
    for key in ("Europe/Kyiv", "Europe/Kiev"):
        try:
            return ZoneInfo(key)
        except (ZoneInfoNotFoundError, ValueError):
            continue
    return timezone(timedelta(hours=2))


LEARNERS_ZONE = _learners_zone()


def learner_day(answered_at):
    """The learner's calendar day for a UTC `answered_at` (naive, as MySQL
    returns a DATETIME): Kyiv time, with the day starting at 04:00."""
    local = answered_at.replace(tzinfo=timezone.utc).astimezone(LEARNERS_ZONE)
    return (local - DAY_ROLLOVER).date()


def today():
    """The learner's day right now -- what `due_on` is compared against."""
    return learner_day(datetime.now(timezone.utc).replace(tzinfo=None))


def word_key(word, pos):
    """How the schedule tells two words apart: the way its primary key does.

    `recall_schedule` is `utf8mb4_unicode_ci`, so `Tip` and `tip` are one key
    to MySQL, and the log's NULL `pos` is stored as ''. The writer and the draw
    both key through this, so a card finds its own schedule row.
    """
    return ((word or "").strip().casefold(), (pos or "").strip().casefold())


# --- the draw (#480) -------------------------------------------------------
#
# How likely a word is to be dealt, relative to the others in the selection.
# **Down-weighted, never excluded**: a topic where everything is known must
# still play, and excluding is how a deck shrinks to nothing -- #338 said
# "down-weights" from the start. A due word and a word never answered are
# equal, because both are what the learner needs next; a word scheduled for a
# later day is dealt a fifth as often. 0.2 is a starting point rather than a
# measurement, and #481 is where it gets checked.
DUE_WEIGHT = 1.0
UNSEEN_WEIGHT = 1.0
NOT_DUE_WEIGHT = 0.2


def draw_weight(due_by_word, on_day):
    """A `weight(card)` for `games.sample()`, from one learner's schedule.

    `due_by_word` is `{word_key: due_on}`. "Not due yet" is what SM-2 means by
    "known for now" -- which is why the schedule shipped before this: the draw
    needs no threshold of its own for when a word is learned.
    """
    def weight(card):
        due = due_by_word.get(word_key(card.get("word"), card.get("pos")))
        if due is None:
            return UNSEEN_WEIGHT
        return DUE_WEIGHT if due <= on_day else NOT_DUE_WEIGHT
    return weight


@dataclass(frozen=True)
class Answer:
    """One row of the log, as far as the schedule cares."""
    answered_at: datetime
    correct: bool
    self_marked: bool = False


@dataclass(frozen=True)
class Schedule:
    """One word's state for one learner -- a `recall_schedule` row."""
    reps: int = 0
    lapses: int = 0
    ease: float = START_EASE
    interval_days: int = 0
    due_on: date = None

    @property
    def ease_permille(self):
        """As the table stores it, the way Anki stores `factor`."""
        return int(round(self.ease * 1000))


def answer_from_row(answered_at, correct, game):
    """A log row's columns as an `Answer`."""
    return Answer(answered_at, bool(correct), game in SELF_MARKED_GAMES)


def _deciding_answers(answers):
    """One answer per learner-day, in day order: the first checked answer of
    the day, or -- on a day with none -- the first self-marked one."""
    by_day = {}
    for answer in sorted(answers, key=lambda a: a.answered_at):
        by_day.setdefault(learner_day(answer.answered_at), []).append(answer)
    deciding = []
    for day in sorted(by_day):
        checked = [a for a in by_day[day] if not a.self_marked]
        deciding.append((day, (checked or by_day[day])[0]))
    return deciding


def _passed_interval(reps, interval, ease):
    """SM-2's next interval after a pass that makes `reps` in a row."""
    if reps == 1:
        return 1
    if reps == 2:
        return 6
    return min(MAX_INTERVAL, max(1, int(round(interval * ease))))


def replay(answers):
    """The schedule one word's answers produce, oldest first or not.

    Returns a `Schedule`, or None for no answers at all -- a word nobody has
    answered has no row, which is different from a row that is due.
    """
    if not answers:
        return None
    reps, lapses, ease, interval, due = 0, 0, START_EASE, 0, None
    for day, answer in _deciding_answers(answers):
        if not answer.correct:
            # A fail is a lapse whenever it happens, early or on time, and
            # whether the site checked it or the learner admitted it.
            reps, lapses, interval = 0, lapses + 1, 1
            ease = max(MIN_EASE, round(ease - LAPSE_EASE_PENALTY, 2))
        elif due is not None and day < due:
            # Practice before the word is due: nothing moves (rule 3).
            continue
        elif answer.self_marked:
            reps += 1
            full = _passed_interval(reps, interval, ease)
            weak = min(WEAK_PASS_CAP, math.ceil((interval + full) / 2))
            # A tick never shortens an interval that checked answers earned.
            interval = max(interval, weak, 1)
        else:
            reps += 1
            interval = _passed_interval(reps, interval, ease)
        due = day + timedelta(days=interval)
    return Schedule(reps, lapses, ease, interval, due)
