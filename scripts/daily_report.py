r"""Email the admin what learners other than him did yesterday (#593).

    venv/bin/python scripts/daily_report.py                    # yesterday, emailed
    venv/bin/python scripts/daily_report.py --dry-run          # printed, not sent
    venv/bin/python scripts/daily_report.py --date 2026-10-14  # another day

Run once a day from PythonAnywhere's Tasks tab (the command is in
docs/dev/deploy.md). It covers **the previous Kyiv day, 04:00 to 04:00**, as
the rest of the app counts days (#447, `recall.day_start`).

**Nothing to report, nothing sent.** Every account in `ADMIN_EMAILS` is left
out -- it never appears and never makes the email go -- so a day on which only
Anton used the site, or nobody did, sends no email. It logs
`NOTIFY kind=daily result=skipped reason=no-activity` instead, so a missing
email can be told from a failed one. His blocks and unblocks are listed when
there is an email, but are not "activity": they are his own.

What it reads, all read-only:

* **the database:** new accounts (`users.created_at`), cards added
  (`flashcards.added_by_user_id`), answers given (`recall_answers`), and the
  paid actions -- Mykola messages, lookups, texts and topics, recaps, uploads
  -- from `action_usage`. Those counters are kept per **server day**
  (`CURDATE()`, UTC on PythonAnywhere), so they can differ from the Kyiv day
  by an hour or two; the email says so;
* **the logs:** rounds played, from `games.log` (one line per round, so it
  counts anonymous visitors and every game), and blocks and unblocks from
  `cards.log`. Rotated files are read too.

A learner is named by account number and `notify.mask_email()`, never the full
address, and nothing from a card or a conversation is ever included.
Anonymous visitors are totals.
"""

import argparse
import re
import sys
from collections import Counter
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

import applog
import notify
import recall
import utils

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}) (\S+)(.*)$")
FIELD = re.compile(r"(\w+)=('(?:[^'\\]|\\.)*'|\S+)")

# The account ceilings, in the order the email lists them, and their words.
PAID = (utils.CHAT, utils.LOOKUP, utils.GENERATE, utils.RECAP, utils.UPLOAD)
# The anonymous pools, read as the same three activities.
ANONYMOUS = {utils.CHAT_ANON: utils.CHAT, utils.LOOKUP_ANON: utils.LOOKUP,
             utils.GENERATE_ANON: utils.GENERATE}
WORDS = {
    "cards": ("card added", "cards added"),
    "rounds": ("round played", "rounds played"),
    "answers": ("answer", "answers"),
    utils.CHAT: ("Mykola message", "Mykola messages"),
    utils.LOOKUP: ("lookup", "lookups"),
    utils.GENERATE: ("text or topic", "texts and topics"),
    utils.RECAP: ("recap", "recaps"),
    utils.UPLOAD: ("upload", "uploads"),
}
COUNTS = ("cards", "rounds", "answers") + PAID


# --- the day ----------------------------------------------------------------

def day_window(day):
    """The UTC instants (naive, as the database and recall store them) that
    bound the Kyiv day `day`: 04:00 to the next 04:00."""
    return recall.day_start(day), recall.day_start(day + timedelta(days=1))


def default_day():
    """Yesterday, as a learner in Kyiv counts days."""
    return recall.today() - timedelta(days=1)


# --- reading ----------------------------------------------------------------

def _unquote(value):
    if len(value) >= 2 and value[0] == value[-1] == "'":
        return value[1:-1].replace("\\'", "'")
    return value


def _log_lines(name, start, end, logs_dir):
    """(action, fields) for each line of `<name>.log` and its rotations whose
    stamp falls in [start, end). The stamps are this machine's local time, as
    applog writes them, and are compared in UTC."""
    for path in sorted(Path(logs_dir).glob(f"{name}.log*")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            found = LINE.match(line)
            if not found:
                continue
            stamp = (datetime.strptime(found.group(1), "%Y-%m-%d %H:%M:%S")
                     .astimezone(timezone.utc).replace(tzinfo=None))
            if start <= stamp < end:
                yield found.group(2), {key: _unquote(value) for key, value
                                       in FIELD.findall(found.group(3))}


def gather_logs(start, end, logs_dir=None):
    """Rounds per player (an address, or `anonymous`), blocks and unblocks."""
    logs_dir = Path(logs_dir or applog.LOGS_DIR)
    rounds = Counter()
    for action, fields in _log_lines(applog.GAMES, start, end, logs_dir):
        # Fill the gap writes `dealt` and, if finished, `self-marked`: one round.
        if action == "ROUND" and fields.get("stage") != "self-marked":
            rounds[fields.get("user", "anonymous").lower()] += 1
    blocks, unblocks = [], []
    for action, fields in _log_lines(applog.CARDS, start, end, logs_dir):
        if action == "USER-BLOCK":
            blocks.append({k: fields[k] for k in ("id", "email", "reason") if k in fields})
        elif action == "USER-UNBLOCK":
            unblocks.append({k: fields[k] for k in ("id", "email") if k in fields})
    return {"rounds": rounds, "blocks": blocks, "unblocks": unblocks}


def gather_db(day, start, end, emails=()):
    """The day's rows, counted in SQL: no card text or message is read."""
    conn = utils.get_db_connection()
    try:
        cursor = conn.cursor()
        # TIMESTAMP columns come back in the session's zone; the window is UTC.
        cursor.execute("SET time_zone = '+00:00'")
        cursor.execute("SELECT id FROM users WHERE created_at >= %s AND created_at < %s",
                       (start, end))
        new = {row[0] for row in cursor.fetchall()}
        cursor.execute(
            "SELECT added_by_user_id, COUNT(*) FROM flashcards "
            "WHERE added_by_user_id IS NOT NULL AND created_at >= %s AND created_at < %s "
            "GROUP BY added_by_user_id", (start, end))
        cards = dict(cursor.fetchall())
        cursor.execute(
            "SELECT user_id, COUNT(*) FROM recall_answers "
            "WHERE answered_at >= %s AND answered_at < %s GROUP BY user_id", (start, end))
        answers = dict(cursor.fetchall())
        cursor.execute("SELECT user_id, action, used FROM action_usage WHERE day = %s",
                       (day,))
        usage = {(user_id, action): used for user_id, action, used in cursor.fetchall()}

        ids = (new | set(cards) | set(answers)
               | {uid for uid, _ in usage if uid != utils.ALL_ACCOUNTS})
        emails = sorted({e.lower() for e in emails if e and e != "anonymous"})
        accounts = {}
        if ids or emails:
            where, params = [], []
            if ids:
                where.append("id IN (" + ", ".join(["%s"] * len(ids)) + ")")
                params += sorted(ids)
            if emails:
                # The column's collation is case-insensitive, so no LOWER().
                where.append("email IN (" + ", ".join(["%s"] * len(emails)) + ")")
                params += emails
            cursor.execute("SELECT id, email, created_at FROM users WHERE "
                           + " OR ".join(where), params)
            accounts = {uid: {"email": email, "created_at": created}
                        for uid, email, created in cursor.fetchall()}
        cursor.close()
    finally:
        conn.close()
    return {"accounts": accounts, "new": new, "cards": cards, "answers": answers,
            "usage": usage,
            "emails": {a["email"].lower(): uid for uid, a in accounts.items() if a["email"]}}


def user_limits():
    """Each account ceiling's limit, from the same settings the site claims
    against. `web` is imported here, not at the top, because only this needs it."""
    import web
    return {utils.CHAT: web.CHAT_USER_DAILY, utils.LOOKUP: web.LOOKUP_USER_DAILY,
            utils.GENERATE: web.GENERATION_USER_DAILY, utils.RECAP: web.RECAP_USER_DAILY,
            utils.UPLOAD: web.UPLOAD_USER_DAILY}


# --- the report -------------------------------------------------------------

def build(db, logs, admins, limits):
    """The report as data: learners (admins left out), anonymous totals,
    blocks and unblocks."""
    admins = {a.lower() for a in admins}
    rounds_by_id, anonymous_rounds = Counter(), 0
    for player, count in logs["rounds"].items():
        if player == "anonymous":
            anonymous_rounds += count
        elif player in db["emails"]:
            rounds_by_id[db["emails"][player]] += count

    learners = []
    ids = (set(db["new"]) | set(db["cards"]) | set(db["answers"]) | set(rounds_by_id)
           | {uid for uid, _ in db["usage"] if uid != utils.ALL_ACCOUNTS})
    for uid in sorted(ids):
        account = db["accounts"].get(uid, {})
        email = (account.get("email") or "").strip().lower()
        if email in admins:
            continue
        counts = {"cards": db["cards"].get(uid, 0), "rounds": rounds_by_id.get(uid, 0),
                  "answers": db["answers"].get(uid, 0)}
        counts.update({action: db["usage"].get((uid, action), 0) for action in PAID})
        new = uid in db["new"]
        if not new and not any(counts.values()):
            continue
        reached = [(action, counts[action], limits[action]) for action in PAID
                   if limits.get(action) and counts[action] >= limits[action]]
        learners.append({"id": uid, "masked": notify.mask_email(email) if email else "(no account)",
                         "new": new, "counts": counts, "limits": reached})

    anonymous = {"rounds": anonymous_rounds, utils.CHAT: 0, utils.LOOKUP: 0, utils.GENERATE: 0}
    for (uid, action), used in db["usage"].items():
        if uid == utils.ALL_ACCOUNTS and action in ANONYMOUS:
            anonymous[ANONYMOUS[action]] += used

    def named(entries):
        return [(int(e["id"]) if str(e.get("id", "")).isdigit() else e.get("id"),
                 notify.mask_email(e.get("email")), e.get("reason")) for e in entries]

    return {"learners": learners, "anonymous": anonymous,
            "blocks": named(logs["blocks"]), "unblocks": named(logs["unblocks"])}


def has_activity(report):
    """Did anyone other than the admin do anything? Blocks are his own doing."""
    return bool(report["learners"]) or any(report["anonymous"].values())


def _counted(counts, keys):
    parts = []
    for key in keys:
        n = counts.get(key, 0)
        if n:
            one, many = WORDS[key]
            parts.append(f"{n} {one if n == 1 else many}")
    return ", ".join(parts) or "nothing counted"


def render(report, day):
    """(subject, body): plain text, a dozen lines on a normal day."""
    label = f"{day.day} {day.strftime('%b')}"
    n = len(report["learners"])
    who = f"{n} learner{'' if n == 1 else 's'}" if n else "anonymous visitors only"
    subject = f"KuantorFlow: activity on {label} ({who})"

    lines = [f"Activity on KuantorFlow on {day.strftime('%a')} {label} {day.year}, "
             "Kyiv time, 04:00 to 04:00. Your own accounts are left out.", ""]
    for learner in report["learners"]:
        head = f"#{learner['id']} {learner['masked']}"
        if learner["new"]:
            head += " (new account)"
        lines.append(head)
        lines.append("  " + _counted(learner["counts"], COUNTS))
        for action, used, limit in learner["limits"]:
            lines.append(f"  reached the {WORDS[action][1]} limit: {used} of {limit}")
    if any(report["anonymous"].values()):
        lines.append("")
        lines.append("Anonymous visitors: " + _counted(
            report["anonymous"], ("rounds", utils.CHAT, utils.LOOKUP, utils.GENERATE)))
    if report["blocks"] or report["unblocks"]:
        lines.append("")
    for title, entries in (("Blocked", report["blocks"]), ("Unblocked", report["unblocks"])):
        for uid, masked, reason in entries:
            lines.append(f"{title}: #{uid} {masked}" + (f" ({reason})" if reason else ""))
    lines += ["", "Mykola messages, lookups, texts, recaps and uploads are counted per "
              "server day (UTC), so they can differ from the Kyiv day by an hour or two."]
    return subject, "\n".join(lines) + "\n"


# --- running it -------------------------------------------------------------

def _day(text):
    try:
        return date.fromisoformat(text)
    except ValueError:
        raise argparse.ArgumentTypeError(f"not a date: {text!r} (want YYYY-MM-DD)")


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Email the admin yesterday's activity by learners other than "
                    "him (#593); nothing is sent on a day with none.")
    parser.add_argument("--date", type=_day, help="the Kyiv day to report, YYYY-MM-DD "
                        "(default: yesterday)")
    parser.add_argument("--dry-run", action="store_true",
                        help="print the email instead of sending it")
    parser.add_argument("--logs", default=None, help="the logs directory (default: the app's)")
    args = parser.parse_args(argv)

    day = args.date or default_day()
    start, end = day_window(day)
    logs = gather_logs(start, end, args.logs)
    db = gather_db(day, start, end, emails=list(logs["rounds"]))
    report = build(db, logs, notify.admin_addresses(), user_limits())
    subject, body = render(report, day)

    if not has_activity(report):
        print(f"Nothing to report for {day}: no activity by anyone but the admin.")
        if not args.dry_run:
            applog.notify(notify.KIND_DAILY, "skipped", subject, reason="no-activity")
        return 0
    if args.dry_run:
        print(subject + "\n\n" + body)
        return 0
    result = notify.send_admin_email(notify.KIND_DAILY, subject, body)
    print(f"{subject}: {result}")
    return 0 if result == "sent" else 1


if __name__ == "__main__":
    sys.exit(main())
