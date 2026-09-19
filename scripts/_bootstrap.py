"""Put the repo root on `sys.path`, so a script here can import the app.

Every script in this directory reads the app's own modules -- `utils`,
`applog`, `settings_store`, `parsers` -- because the whole point of them is to
do a one-off job *through* the code the site runs, rather than through SQL
written twice. They lived beside those modules until #442 moved them here, and
that move is what makes this file necessary: `python scripts/seed_topics.py`
puts **`scripts/`** on `sys.path`, not the repo root, so `import utils` would
fail before the script printed a word.

Imported for its side effect and **before** anything from the app, which makes
it the one import in these files whose position is load-bearing:

    import _bootstrap  # noqa: F401  (must precede the app imports)
    import utils

One file rather than the same three lines six times, because #442 goes on to
propose a package and an application factory, and this is the seam that
argument has to move. `seed_words.py` does not import it: that module holds
data and imports nothing at all.
"""

import sys
from pathlib import Path

_ROOT = str(Path(__file__).resolve().parent.parent)
if _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)
