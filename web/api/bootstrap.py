"""Put the repository root and pipeline/ on sys.path.

The existing modules (`tracker`, `sponsor_check`, `shortlist`, `sponsor_review`)
are top-level scripts, not an installed package, and `pipeline/` modules import
each other by bare name. Importing this module first is what makes
`import tracker` work from inside `web.api`.

Deliberately the only import-time side effect anywhere under `web/api/`. No
module here may read a file, load the register, or touch the network at import
time — `tests/run_all.py` imports the whole tree, and a module-scope register
load would put a 10.9 MB download in the test suite.
"""
from __future__ import annotations

import sys
from pathlib import Path

ADZUNA_HOME = Path(__file__).resolve().parent.parent.parent

for _path in (ADZUNA_HOME, ADZUNA_HOME / "pipeline"):
    _s = str(_path)
    if _s not in sys.path:
        sys.path.insert(0, _s)
