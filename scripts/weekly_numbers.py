r"""Production's numbers for the weekly development report, in one file (#607).

    venv/bin/python scripts/weekly_numbers.py                     # the last 7 days
    venv/bin/python scripts/weekly_numbers.py --since 2026-10-17  # from a date
    venv/bin/python scripts/weekly_numbers.py --out ~/numbers.txt

Run on PythonAnywhere when a report is being written. It writes **one text
file**, by default `~/weekly-numbers-<today>.txt`, and prints where: download
it from the Files tab and pass it on. Read-only throughout.

The five sections are what each edition used to need run by hand:

1. **AI usage** per day, feature and model -- `model_usage_report.py`'s table;
2. **requests** per endpoint (count, median and 95th-percentile ms, database
   connections), the statuses, anonymous against signed-in, the slowest ten,
   and **the paths that answered 404**;
3. **translations dropped** by the alphabet check (#544);
4. **admin emails** by kind and result (#591);
5. **visits** per day (#598), and the accounts created.

**Nothing in it names a person.** `requests.log` holds account ids, never
addresses; notices are counted by kind; no email address is printed. **A
section that cannot be read says so, and the others carry on**, so a missing
log or table never costs the whole file. Log times are the server's, which is
UTC on PythonAnywhere; `--since` is compared with them as a date.
"""

import argparse
import re
import sys
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

import applog
import model_usage_report
import utils

LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{2}:\d{2}:\d{2}) (\S+)(.*)$")
FIELD = re.compile(r"(\w+)=('(?:[^'\\]|\\.)*'|\S+)")
MYKOLA_MODEL = "claude-opus-5"


def _lines(logs_dir, name, since):
    """(day, time, action, fields) for each line of `<name>.log*` on or after `since`."""
    for path in sorted(Path(logs_dir).glob(f"{name}.log*")):
        try:
            text = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        for line in text.splitlines():
            found = LINE.match(line)
            if found and found.group(1) >= since:
                yield (found.group(1), found.group(2), found.group(3),
                       dict(FIELD.findall(found.group(4))))


def _pct(values, p):
    values = sorted(values)
    return values[int(p * (len(values) - 1))] if values else 0


# --- the sections -------------------------------------------------------------

def ai_usage(logs_dir, since):
    totals = model_usage_report.summarise(
        model_usage_report.read_records(logs_dir, MYKOLA_MODEL), since)
    return model_usage_report.render(totals) if totals else "No model usage logged."


def requests_summary(logs_dir, since):
    by_endpoint, statuses, who, slow, missing = defaultdict(list), Counter(), Counter(), [], Counter()
    for day, clock, action, fields in _lines(logs_dir, applog.REQUESTS, since):
        if action != "REQUEST":
            continue
        try:
            ms, db = float(fields.get("ms", 0)), int(fields.get("db", 0))
        except ValueError:
            continue
        endpoint = fields.get("endpoint", "-")
        by_endpoint[endpoint].append((ms, db))
        statuses[fields.get("status", "?")] += 1
        who["anonymous" if fields.get("user_id") == "anonymous" else "signed-in"] += 1
        slow.append((ms, db, f"{day} {clock}", fields.get("path", "?")))
        if fields.get("status") == "404":
            missing[fields.get("path", "?")] += 1
    total = sum(len(v) for v in by_endpoint.values())
    if not total:
        return "No requests logged."
    lines = [f"{total} requests; status {dict(sorted(statuses.items()))}; "
             f"{dict(sorted(who.items()))}", "",
             f"{'endpoint':28} {'n':>5} {'p50':>7} {'p95':>7} {'max':>7} {'db50':>5} {'dbmax':>5}"]
    for endpoint, rows in sorted(by_endpoint.items(), key=lambda kv: (-len(kv[1]), kv[0])):
        ms = [r[0] for r in rows]
        db = [r[1] for r in rows]
        lines.append(f"{endpoint[:28]:28} {len(rows):5} {_pct(ms, .5):7.0f} {_pct(ms, .95):7.0f} "
                     f"{max(ms):7.0f} {_pct(db, .5):5} {max(db):5}")
    lines += ["", "slowest 10:"]
    for ms, db, when, path in sorted(slow, reverse=True)[:10]:
        lines.append(f"  {ms:7.0f} ms  db={db:<3} {when}  {path}")
    if missing:
        lines += ["", f"404s by path ({sum(missing.values())}):"]
        for path, n in sorted(missing.items(), key=lambda kv: (-kv[1], kv[0]))[:20]:
            lines.append(f"  {n:4}  {path}")
    return "\n".join(lines)


def translations_dropped(logs_dir, since):
    n = sum(1 for _, _, action, _ in _lines(logs_dir, applog.DICT, since)
            if action == "TRANSLATE-DROPPED")
    return f"{n} translation(s) dropped by the alphabet check."


def admin_emails(logs_dir, since):
    counts = Counter((fields.get("kind", "?"), fields.get("result", "?"))
                     for _, _, action, fields in _lines(logs_dir, applog.NOTIFY, since)
                     if action == "NOTIFY")
    if not counts:
        return "No admin emails attempted."
    return "\n".join(f"{n:5}  kind={kind} result={result}"
                     for (kind, result), n in sorted(counts.items()))


def db_numbers(since):
    """Visits per day and accounts, straight from the database. Read-only."""
    start = datetime.combine(date.fromisoformat(since), datetime.min.time())
    conn = utils.get_db_connection()
    try:
        cursor = conn.cursor()
        cursor.execute(
            "SELECT DATE(started_at), COUNT(*), SUM(user_id IS NULL), "
            "COUNT(DISTINCT visitor), SUM(active_seconds), SUM(pages) "
            "FROM visits WHERE started_at >= %s GROUP BY DATE(started_at) "
            "ORDER BY DATE(started_at)", (start,))
        visits = [tuple(int(v) if v is not None and not isinstance(v, date) else v
                        for v in row) for row in cursor.fetchall()]
        cursor.execute("SET time_zone = '+00:00'")
        cursor.execute("SELECT COUNT(*), SUM(created_at >= %s) FROM users", (start,))
        accounts, new = cursor.fetchone()
        cursor.close()
    finally:
        conn.close()
    return {"visits": visits, "accounts": int(accounts or 0), "new": int(new or 0)}


def visits_and_accounts(since):
    numbers = db_numbers(since)
    lines = [f"{numbers['accounts']} accounts in all, {numbers['new']} created since {since}.", ""]
    if not numbers["visits"]:
        return "\n".join(lines + ["No visits recorded."])
    lines.append(f"{'day':10} {'visits':>6} {'anon':>5} {'visitors':>8} {'active min':>10} {'pages':>6}")
    for day, n, anonymous, visitors, seconds, pages in numbers["visits"]:
        lines.append(f"{day!s:10} {n:6} {anonymous or 0:5} {visitors:8} "
                     f"{(seconds or 0) / 60:10.1f} {pages or 0:6}")
    return "\n".join(lines)


SECTIONS = (
    ("1. AI usage, per day, feature and model", lambda logs, since: ai_usage(logs, since)),
    ("2. Requests per endpoint", lambda logs, since: requests_summary(logs, since)),
    ("3. Translations dropped (#544)", lambda logs, since: translations_dropped(logs, since)),
    ("4. Admin emails (#591)", lambda logs, since: admin_emails(logs, since)),
    ("5. Visits (#598) and accounts", lambda logs, since: visits_and_accounts(since)),
)


def build(logs_dir, since):
    """The whole file's text; a section that fails says why and the rest carry on."""
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    parts = [f"KuantorFlow production numbers since {since}, read {now}."]
    for title, section in SECTIONS:
        try:
            body = section(logs_dir, since)
        except Exception as exc:  # noqa: BLE001 - one section never costs the file
            body = f"(could not be read: {type(exc).__name__}: {exc})"
        parts += ["", "=" * 72, title, "=" * 72, body]
    return "\n".join(parts) + "\n"


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Write production's numbers for the weekly report to one file (#607).")
    parser.add_argument("--since", type=date.fromisoformat,
                        default=date.today() - timedelta(days=7),
                        help="the first day to count, YYYY-MM-DD (default: 7 days ago)")
    parser.add_argument("--out", help="where to write it (default: ~/weekly-numbers-<today>.txt)")
    parser.add_argument("--logs", default=None, help="the logs directory (default: the app's)")
    args = parser.parse_args(argv)

    out = Path(args.out).expanduser() if args.out else \
        Path.home() / f"weekly-numbers-{date.today().isoformat()}.txt"
    text = build(Path(args.logs or applog.LOGS_DIR), args.since.isoformat())
    out.write_text(text, encoding="utf-8")
    print(f"Wrote {out}: download it from the Files tab and pass it on.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
