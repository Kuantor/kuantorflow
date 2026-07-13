# Report tooling

Reusable converters that turn a Markdown report from `reports/` into styled
DOCX and PDF renders (KuantorFlow palette: blue headings, yellow rules,
branded table headers).

## Usage

```bash
pip install python-docx        # the only Python dependency

python reports/scripts/md_to_docx.py reports/2026-07-13-weekly-report.md
python reports/scripts/md_to_pdf.py  reports/2026-07-13-weekly-report.md
```

Output lands next to the input (same name, `.docx` / `.pdf`) unless a second
argument names the output file.

## How it works

- `md_to_docx.py` — parses the report Markdown (headings, bullets, numbered
  lists, tables, `**bold**` / `*italic*` / `` `code` `` / links) and emits the
  document with python-docx. The parser is importable (`parse_markdown`).
- `md_to_pdf.py` — reuses that same parser, emits styled HTML, and prints it
  to PDF with **headless Microsoft Edge** (present on every Windows 10/11
  machine — no LibreOffice or Word required).

One parser, two emitters: if the Markdown dialect grows, extend
`parse_markdown` once and both formats pick it up.

## Notes

- Link text is kept and tinted blue, but not clickable (reports are meant for
  reading/printing; the Markdown original carries the live links).
- Table column widths are proportional to content length (DOCX); PDF tables
  use full-width HTML layout.
