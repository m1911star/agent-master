"""Package-local conftest: adds repo root to sys.path so adapters can import
agent_master.* and tests can import their sibling observe.py via the package
path (e.g. `from packages.adapters.opencode.observe import ...`).
"""

from __future__ import annotations

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))
