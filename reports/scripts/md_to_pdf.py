"""
Convert a KuantorFlow report written in Markdown into a styled .pdf,
using headless Microsoft Edge (ships with Windows 10/11) as the renderer.

Usage:
    python md_to_pdf.py <report.md> [output.pdf]

Requires: md_to_docx.py next to this script (shares its Markdown parser);
Microsoft Edge installed (checked in the standard locations).
"""

import html
import subprocess
import sys
import tempfile
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


def main():
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    src = Path(sys.argv[1])
    out = (Path(sys.argv[2]) if len(sys.argv) > 2 else src.with_suffix(".pdf")).resolve()
    page = blocks_to_html(parse_markdown(src.read_text(encoding="utf-8")))

    with tempfile.TemporaryDirectory() as tmp:
        html_path = Path(tmp) / "report.html"
        html_path.write_text(page, encoding="utf-8")
        subprocess.run(
            [find_edge(), "--headless", "--disable-gpu",
             "--no-pdf-header-footer",
             f"--print-to-pdf={out}", html_path.as_uri()],
            check=True, timeout=120,
        )
    print(f"written: {out}")


if __name__ == "__main__":
    main()
