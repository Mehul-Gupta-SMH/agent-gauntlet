"""Put the repository root on `sys.path` for the test run.

`tests/test_probe_record.py` imports `tools.probe_next`, the rotation
chooser the probe workflow runs. `tools/` is deliberately not part of the
wheel -- the distribution ships `src/agent_gauntlet` alone -- so it is
importable only if the repository root is on the path.

Nothing was putting it there. It worked anyway in every environment this
was developed in, including a clean venv built the same way CI builds its
own, because some pip/setuptools editable-install strategies drop the
project root on `sys.path` via a `.pth` file and others install an import
finder scoped to `src/`. Which one you get depends on the pip and
setuptools versions, so the import was resolving by luck.

On the runner the luck ran out, and the offline suite went red on seven
tests with `ModuleNotFoundError: No module named 'tools'` for three
consecutive commits. It took making CI echo its failures as check-run
annotations to find out which seven, because the job log lives on a host
that environment cannot reach.

A root `conftest.py` is the explicit version of what was happening by
accident. pytest imports it before collection and the path is then the
same everywhere.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
