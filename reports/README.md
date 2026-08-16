# Reports

Documents written for people rather than for the app. Two kinds live here:

- **Weekly development reports** — `YYYY-MM-DD-weekly-report.{md,pdf}`, one
  per week, covering all three repositories (issue #63).
- **One-off documents** — an idea written up (`2026-07-14-spaced-repetition-idea`),
  a code walkthrough (`2026-07-13-code-snippets`), and the `mht/` source notes.

The Markdown is the original and the PDF is a render of it; both are committed
together, and `reports/scripts/README.md` documents the converters.

**Weekly reports stopped shipping a DOCX after 15 August 2026.** The PDF is
rendered from the Markdown directly, so the third file was a third copy of the
same words with nothing that only it could say. Editions up to that date keep
theirs, and `md_to_docx.py` stays — a one-off document may still want DOCX, and
`md_to_pdf.py` imports its Markdown parser.

Verification reports for individual pull requests are a **different** thing and
live in the other repository, under `kuantorflow_automation/test_reports/`.

## Producing a weekly report

The figures are gathered, never recalled. Everything below is mechanical; the
part worth a person's attention is deciding what the week *meant*, which is why
this is a checklist and not a script.

### 1. Find the cutoff

A report covers everything merged since the previous one, and **the previous
report's own footer names its window** — the last line of the newest
`*-weekly-report.md`. That is the boundary; there is nowhere else to look it up.

Sync all three repositories first. The local checkout can be a day behind if
work happened elsewhere.

### 2. Merged pull requests, per repository

```bash
for r in Kuantor/kuantorflow Kuantor/kuantorflow_automation Kuantor/ai_agent; do
  echo "=== $r"
  gh pr list --repo $r --state merged --limit 60 \
    --json number,title,mergedAt \
    -q '.[] | select(.mergedAt >= "CUTOFF") | "  #\(.number)  \(.mergedAt[0:10])  \(.title)"'
done
```

Replace `CUTOFF` with an ISO timestamp just after the previous report's last
merge, e.g. `2026-08-08T21:00:00Z`. Raise `--limit` above the expected count.

### 3. Closed issues, with the reason

Every edition has reported how many issues were closed *and* how many were closed
as **not planned** — a specified idea that was thought through and declined is
worth as much as one delivered. `stateReason` is what tells them apart:

```bash
gh issue list --repo REPO --state closed --limit 80 \
  --json number,title,closedAt,stateReason \
  -q '.[] | select(.closedAt >= "CUTOFF") | "  #\(.number)  \(.stateReason)  \(.title)"'
```

### 4. Commit authorship — a standing check

Every report states the commit count, that all commits were authored by Kuantor,
and **explicitly that no GitHub Copilot commits were found**. This is a standing
request, not an editorial flourish; run it across all three repositories:

```bash
git log --since=DATE --format="%an <%ae>" origin/main | sort | uniq -c
git log --since=DATE --format="%b" origin/main | grep -i "^Co-Authored-By" | sort | uniq -c
```

### 5. Suite size

Run it. Do not quote `kuantorflow_automation/docs/test-catalog.md` — its own
totals footer has gone stale before (that repo's issue #61):

```bash
venv/Scripts/python -m pytest -m "not live" -q | tail -2        # the offline figure
RUN_DB_ROUNDTRIP=1 venv/Scripts/python -m pytest -q | tail -2   # plus db and live
```

Quote the **offline** number as the headline, since that is what the previous
reports compare against, and mention the opt-in database tests separately.

### 6. Plans for next week

From the **KuantorFlow Improvements** project board, read by column. The report
gives each active column its own table — *Highest Prio*, *In Progress*, *Nice To
Have*, *Todo*, *Human* — and skips the several `Done *` columns. Editions have
ordered those tables slightly differently; leading with *Highest Prio* is the
only part that has been constant.

Add a table of anything filed during the week that is **not yet on the board**:
tickets written while investigating are easy to lose otherwise, and they are
usually the best-specified items available.

```bash
gh project item-list 2 --owner Kuantor --limit 100 --format json
```

### 7. Sections, in the established order

Title, period and repository links · Executive Summary, with the
repository/PR table, the issues-closed line and the authorship note in italics ·
Completed Work by Theme, numbered · Technical Highlights — principles, not a
second changelog · Lessons Learned, numbered · Plans for Next Week · an italic
footer naming the merged-PR window, which becomes the **next** report's cutoff.

Read the most recent edition before writing and match its voice. Prose is written
as one long line per paragraph, as in the existing reports.

### 8. Render, and read the verification line

```bash
venv/Scripts/python reports/scripts/md_to_pdf.py reports/DATE-weekly-report.md
```

`md_to_pdf.py` prints `verified: N page(s), N characters` — **read it.** Headless
Edge returns before it has rendered anything, and a report was once committed as
a one-page PDF of the browser's own error page with a successful exit code
(#211). See `reports/scripts/README.md`.

Its check reads the finished PDF back and refuses a render that *is* the error
page — one page holding almost nothing but the browser's message. It is scoped
to that shape on purpose (#282), so a report is free to **quote the error string
in its own prose**; the 15 August edition was rejected twice for doing so before
that was fixed. There is no flag to skip the check, and there is not meant to
be: it is what makes a silent bad render impossible.

Then a `docs/DATE-weekly-report` branch, all three files in one commit, and a PR.

## Traps

- **Issues and pull requests share one number sequence.** A gap in the PR numbers
  is an issue, not a missing PR. Do not report it as one.
- **Verify every issue title you cite** with `gh issue view N`. Quoting one from
  memory has produced a wrong title in a report before.
- **`--limit` is a silent truncation.** If the count looks suspiciously round,
  raise it and re-run.
