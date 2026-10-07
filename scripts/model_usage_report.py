r"""What the paid model calls used, per day, feature and model (#562).

    venv/Scripts/python scripts/model_usage_report.py
    venv/Scripts/python scripts/model_usage_report.py --days 30
    venv/Scripts/python scripts/model_usage_report.py --rates rates.json

Reads the activity logs and adds up the tokens, so the AI bill can be totalled
from what happened rather than modelled (CLAUDE.md's "about $8 a day" is a
model). Two sources:

* `model_usage.log` -- one `MODEL-USAGE` line per call this repo makes itself:
  the word lookup's translator, the generated text, the topic builder and the
  notes splitter, each with its model;
* `mykola.log` -- ai_agent's usage line for every call Mykola makes (the chat
  and the recap). Those lines name no model, so they are reported under
  `mykola` and the model `--mykola-model` (default `agent`).

Rotated files (`model_usage.log.2026-10-07`, ...) are read too.

**No prices in code.** Rates change, so the report prints tokens, and
`--rates` turns them into money when given a JSON file of US dollars per
million tokens, keyed by model:

    {"claude-haiku-4-5-20251001": {"in": 1.0, "out": 5.0,
                                   "cache_write": 1.25, "cache_read": 0.1}}

A model with no rate shows `-` in the cost column rather than a guess.

ASCII output, like the other console scripts. Read-only; not part of a deploy.
"""

import argparse
import json
import re
from collections import defaultdict
from datetime import date, timedelta
from pathlib import Path

# `scripts/` is what `sys.path` gets when this file is run, not the repo
# root -- so the app's modules below need the root put there first (#442).
import _bootstrap  # noqa: F401

import applog

TOKEN_FIELDS = ("in", "out", "cache_write", "cache_read")
LINE = re.compile(r"^(\d{4}-\d{2}-\d{2}) \d{2}:\d{2}:\d{2} (.*)$")
FIELD = re.compile(r"(\w+)=('(?:[^'\\]|\\.)*'|\S+)")


def parse_line(line, mykola_model="agent"):
    """One usage record `{day, feature, model, in, out, cache_write,
    cache_read}` from a log line, or None for any other line."""
    match = LINE.match(line.strip())
    if not match:
        return None
    day, rest = match.groups()
    fields = dict(FIELD.findall(rest))
    if rest.startswith("MODEL-USAGE "):
        feature, model = fields.get("feature"), fields.get("model")
    elif rest.startswith("in=") and "out" in fields:
        feature, model = "mykola", mykola_model      # ai_agent's usage line
    else:
        return None
    record = {"day": day, "feature": feature or "?", "model": model or "?"}
    for name in TOKEN_FIELDS:
        try:
            record[name] = int(fields.get(name, 0))
        except ValueError:
            record[name] = 0
    return record


def read_records(logs_dir, mykola_model="agent"):
    records = []
    for pattern in ("model_usage.log*", "mykola.log*"):
        for path in sorted(Path(logs_dir).glob(pattern)):
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                record = parse_line(line, mykola_model)
                if record:
                    records.append(record)
    return records


def summarise(records, since=None):
    """`{(day, feature, model): {calls, in, out, cache_write, cache_read}}`
    for the records on or after `since` (an ISO date string)."""
    totals = defaultdict(lambda: dict.fromkeys(("calls",) + TOKEN_FIELDS, 0))
    for record in records:
        if since and record["day"] < since:
            continue
        row = totals[(record["day"], record["feature"], record["model"])]
        row["calls"] += 1
        for name in TOKEN_FIELDS:
            row[name] += record[name]
    return dict(totals)


def cost(row, rate):
    """US dollars for one row at `rate` (dollars per million tokens), or None."""
    if not rate:
        return None
    return sum(row[name] * rate.get(name, 0) for name in TOKEN_FIELDS) / 1_000_000


def render(totals, rates=None):
    rates = rates or {}
    header = (f"{'day':10}  {'feature':9}  {'model':28}  {'calls':>5}  {'in':>9}  "
              f"{'out':>9}  {'cache_w':>9}  {'cache_r':>9}  {'cost $':>8}")
    lines = [header, "-" * len(header)]
    grand = 0.0
    priced_everything = True
    for (day, feature, model), row in sorted(totals.items()):
        money = cost(row, rates.get(model))
        if money is None:
            priced_everything = False
        else:
            grand += money
        lines.append(
            f"{day:10}  {feature:9}  {model[:28]:28}  {row['calls']:>5}  "
            f"{row['in']:>9}  {row['out']:>9}  {row['cache_write']:>9}  "
            f"{row['cache_read']:>9}  {'-' if money is None else f'{money:.4f}':>8}")
    if rates:
        note = "" if priced_everything else "  (rows without a rate not included)"
        lines.append(f"total cost: ${grand:.4f}{note}")
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Tokens used by the paid model calls, per day, feature "
                    "and model (#562). Read-only.")
    parser.add_argument("--days", type=int, default=7,
                        help="how many days back, today included (default 7)")
    parser.add_argument("--rates", help="JSON file: dollars per million tokens, by model")
    parser.add_argument("--logs", default=str(applog.LOGS_DIR),
                        help="the logs directory (default: the app's)")
    parser.add_argument("--mykola-model", default="agent",
                        help="the model name to report Mykola's lines under")
    args = parser.parse_args(argv)

    rates = None
    if args.rates:
        with open(args.rates, encoding="utf-8") as f:
            rates = json.load(f)
    since = (date.today() - timedelta(days=max(args.days, 1) - 1)).isoformat()
    totals = summarise(read_records(args.logs, args.mykola_model), since)
    if not totals:
        print(f"no model usage logged since {since}")
        return
    print(render(totals, rates))


if __name__ == "__main__":
    main()
