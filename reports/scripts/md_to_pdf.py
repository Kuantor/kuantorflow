"""
Convert a KuantorFlow report written in Markdown into a styled .pdf,
using headless Microsoft Edge (ships with Windows 10/11) as the renderer.

Usage:
    python md_to_pdf.py <report.md> [output.pdf]

Requires: md_to_docx.py next to this script (shares its Markdown parser);
Microsoft Edge installed (checked in the standard locations); pypdf, which is
used to read the finished PDF back (see below).

**Edge exits before it has rendered anything** (issue #211). It hands the work
to a browser process and returns, so `subprocess.run` coming back says nothing
about whether a PDF was written. Two consequences shape this script:

* The HTML goes to a path that outlives the call. It used to be written into a
  `tempfile.TemporaryDirectory()`, which was then deleted while the browser was
  still getting round to loading it — so Edge loaded nothing, rendered its own
  "File not found" page, and printed *that* to the PDF. On 2026-08-04 a report
  was committed in that state: one page, 92 characters, exit code 0.
* The result is read back before this script claims success. Nothing cheaper
  works: that error page is a structurally valid PDF with a correct header, a
  correct trailer and a plausible size, because it still embeds the fonts the
  stylesheet asks for. The error page is recognised by its *shape* — one page
  that is almost entirely the message — and not by the message alone, so a
  report is free to quote the string (#282).

Whether Edge hands off depends on another Edge or Chromium process already
running, so the failure is intermittent — which is why the check is not
optional and there is no flag to skip it.
"""

import html
import subprocess
import sys
import time
from pathlib import Path

from md_to_docx import parse_markdown  # shared parser — one dialect, two emitters

EDGE_PATHS = [
    r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe",
    r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
]

CSS = """
body { font-family: Calibri, 'Segoe UI', sans-serif; font-size: 11pt;
       color: #22303C; margin: 2.2cm 2.1cm; }
h1.title { color: #2E6BA0; font-size: 24pt; margin: 0 0 4pt; }
h2 { color: #2E6BA0; font-size: 15pt; margin: 18pt 0 6pt; }
h3 { color: #22303C; font-size: 12.5pt; margin: 14pt 0 5pt; }
p  { margin: 0 0 7pt; line-height: 1.35; }
ul, ol { margin: 0 0 8pt 1.4em; padding: 0; }
li { margin: 0 0 4pt; line-height: 1.35; }
hr { border: none; border-bottom: 1.5pt solid #D9B13C; margin: 10pt 0 14pt; }
table { border-collapse: collapse; margin: 4pt 0 12pt; width: 100%; }
th { background: #2E6BA0; color: #fff; text-align: left; }
th, td { border: 1px solid #B9C6D2; padding: 4pt 7pt; font-size: 10pt; }
code { font-family: Consolas, monospace; font-size: 10pt; }
a.linkish { color: #2E6BA0; text-decoration: none; }
* { -webkit-print-color-adjust: exact; print-color-adjust: exact; }
"""


def runs_to_html(runs):
    out = []
    for r in runs:
        text = html.escape(r["text"])
        if r.get("code"):
            text = f"<code>{text}</code>"
        if r.get("bold"):
            text = f"<b>{text}</b>"
        if r.get("italic"):
            text = f"<i>{text}</i>"
        if r.get("link"):
            text = f'<span class="linkish">{text}</span>'
        out.append(text)
    return "".join(out)


def blocks_to_html(blocks):
    parts, list_open = [], None

    def close_list():
        nonlocal list_open
        if list_open:
            parts.append(f"</{list_open}>")
            list_open = None

    for block in blocks:
        kind = block[0]
        if kind in ("bullet", "num"):
            tag = "ul" if kind == "bullet" else "ol"
            if list_open != tag:
                close_list()
                parts.append(f"<{tag}>")
                list_open = tag
            parts.append(f"<li>{runs_to_html(block[1])}</li>")
            continue
        close_list()
        if kind == "title":
            parts.append(f'<h1 class="title">{runs_to_html(block[1])}</h1>')
        elif kind == "h1":
            parts.append(f"<h2>{runs_to_html(block[1])}</h2>")
        elif kind == "h2":
            parts.append(f"<h3>{runs_to_html(block[1])}</h3>")
        elif kind == "hr":
            parts.append("<hr>")
        elif kind == "p":
            parts.append(f"<p>{runs_to_html(block[1])}</p>")
        elif kind == "table":
            rows = block[1]
            parts.append("<table>")
            for i, row in enumerate(rows):
                tag = "th" if i == 0 else "td"
                cells = "".join(f"<{tag}>{runs_to_html(c)}</{tag}>" for c in row)
                parts.append(f"<tr>{cells}</tr>")
            parts.append("</table>")
    close_list()
    return ("<!doctype html><html><head><meta charset='utf-8'>"
            f"<style>{CSS}</style></head><body>{''.join(parts)}</body></html>")


def find_edge():
    for path in EDGE_PATHS:
        if Path(path).exists():
            return path
    sys.exit("Microsoft Edge not found in the standard locations.")


# Extracted text is not the source text: the embedded fonts use typographic
# ligatures, so 'backfill' reads back as 'back<fi>ll' and 'differ' as
# 'di<ff>er'. Folded before comparing, or four sound headings look missing.
LIGATURES = {"ﬀ": "ff", "ﬁ": "fi", "ﬂ": "fl", "ﬃ": "ffi", "ﬄ": "ffl",
             "ﬅ": "st", "ﬆ": "st"}

SETTLE_SECONDS = 90

# Edge's error page, and the shape that tells it apart from a report which
# merely writes *about* it (#282). The marker check used to search the whole
# document, so the 15 August edition — six pages that quoted the string once,
# in the section describing this very trap — was refused as an error page.
# What the error page actually is: a single page that is almost nothing but
# the message (92 characters on the day one was committed), so that is what is
# asked for. Everything longer is judged by the heading check below, which is
# the stronger signal anyway — a render missing headings from the source is a
# bad render whatever else it says. There is still no way to skip the check.
ERROR_MARKERS = ("ERR_FILE_NOT_FOUND", "File not found")
MIN_TEXT = 1000


def _is_error_page(pages, text):
    """Is this the error page itself, rather than a report that quotes it?"""
    return (pages == 1 and len(text) < MIN_TEXT
            and any(marker in text for marker in ERROR_MARKERS))


def _settle(out):
    """Wait for Edge to finish writing, since it exited long ago (#211)."""
    size = -1
    for _ in range(SETTLE_SECONDS):
        time.sleep(1)
        if out.exists():
            current = out.stat().st_size
            if current and current == size:
                return
            size = current
    sys.exit(f"error: {out.name} was never written — Edge rendered nothing.")


def _read_back(out):
    """The PDF's page count and its text, with ligatures folded."""
    try:
        from pypdf import PdfReader
    except ImportError:
        sys.exit("error: pypdf is required to check the rendered PDF — "
                 "pip install -r reports/scripts/requirements.txt")
    pages = PdfReader(str(out)).pages
    text = "".join((page.extract_text() or "") for page in pages)
    for char, plain in LIGATURES.items():
        text = text.replace(char, plain)
    return len(pages), text


def verify(src_text, out):
    """Read the PDF back. Exits non-zero rather than reporting a bad render."""
    pages, text = _read_back(out)
    if _is_error_page(pages, text):
        sys.exit(f"error: {out.name} is Edge's error page, not the report.")
    if len(text) < MIN_TEXT:
        sys.exit(f"error: {out.name} holds only {len(text)} characters of text "
                 f"— it did not render.")

    # Headings are a cheap whole-document check: they are spread through the
    # file, so a truncated or partly-rendered PDF loses some of them.
    flat = " ".join(text.split())
    missing = [line.lstrip("# ").strip()
               for line in src_text.splitlines() if line.startswith("#")
               if " ".join(line.lstrip("# ").replace("**", "").split()) not in flat]
    if missing:
        sys.exit(f"error: {out.name} is missing {len(missing)} heading(s): "
                 f"{missing[:3]}")
    return pages, len(text)


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = (Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".pdf")).resolve()
    src_text = src.read_text(encoding="utf-8")

    # Beside the output, not in a temp directory that gets deleted while the
    # browser is still opening it (#211). Kept if anything fails, so the HTML
    # that produced a bad PDF can be looked at.
    html_path = out.with_suffix(".render.html")
    html_path.write_text(blocks_to_html(parse_markdown(src_text)), encoding="utf-8")
    if out.exists():
        out.unlink()        # so a stale PDF cannot be mistaken for a new one

    subprocess.run(
        [find_edge(), "--headless", "--disable-gpu", "--no-pdf-header-footer",
         f"--print-to-pdf={out}", html_path.as_uri()],
        check=True, timeout=120,
    )
    _settle(out)
    pages, characters = verify(src_text, out)
    html_path.unlink()
    print(f"written: {out}")
    print(f"  verified: {pages} page(s), {characters} characters of text")


if __name__ == "__main__":
    main()
