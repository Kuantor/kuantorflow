# Build-time tooling

Converters that are run by hand when preparing something for the repo, rather
than by the app at runtime. Two jobs live here:

- **Reports** — turn a Markdown report from `reports/` into styled PDF and DOCX
  renders (KuantorFlow palette: blue headings, yellow rules, branded table
  headers). Weekly reports ship as Markdown and PDF only since 15 August 2026;
  DOCX remains for one-off documents that want it.
- **Images** — turn source artwork into the WebP files the site's tiles and
  banners expect (`to_webp.py`, added for the game icons in #234).

## Usage

```bash
pip install -r reports/scripts/requirements.txt   # python-docx, pypdf, Pillow

python reports/scripts/md_to_docx.py reports/2026-07-13-weekly-report.md
python reports/scripts/md_to_pdf.py  reports/2026-07-13-weekly-report.md
```

Output lands next to the input (same name, `.docx` / `.pdf`) unless a second
argument names the output file.

Image conversion takes a file or a whole directory:

```bash
python reports/scripts/to_webp.py --tile    src/ static/img/games
python reports/scripts/to_webp.py --width 1600 src/wide.png static/img/games/wide.webp
```

## How it works

- `md_to_docx.py` — parses the report Markdown (headings, bullets, numbered
  lists, tables, `**bold**` / `*italic*` / `` `code` `` / links) and emits the
  document with python-docx. The parser is importable (`parse_markdown`), and
  **`md_to_pdf.py` imports it** — so this file stays even though weekly reports
  no longer render a DOCX. Deleting it takes the PDF converter with it.
- `md_to_pdf.py` — reuses that same parser, emits styled HTML, and prints it
  to PDF with **headless Microsoft Edge** (present on every Windows 10/11
  machine — no LibreOffice or Word required). It then **reads the PDF back**
  and fails if the text is not the report (issue #211): Edge exits before it
  has rendered anything, and given half a chance will print its own "File not
  found" page — a structurally valid PDF that passes every cheaper check. It
  prints the page and character count on success, so a silent bad render is
  not a thing that can happen.

One parser, two emitters: if the Markdown dialect grows, extend
`parse_markdown` once and both formats pick it up.

- `to_webp.py` — resizes to `--tile` (400×400), `--banner` (1600×400) or
  `--width N`, and writes WebP at quality 82 / method 6. Those numbers are
  measured, not chosen: the eighteen topic icons from #223 are 400×400 RGB at
  20.9–36.1 KB, and that setting reproduces the band. `--width` derives the
  height from the source so **nothing is cropped** — the other two centre-crop
  to their ratio first, in preference to stretching.

## Notes

- Link text is kept and tinted blue, but not clickable (reports are meant for
  reading/printing; the Markdown original carries the live links).
- Table column widths are proportional to content length (DOCX); PDF tables
  use full-width HTML layout.
