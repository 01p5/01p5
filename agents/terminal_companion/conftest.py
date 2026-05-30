"""
Make ``agentlib``, the ``terminal_companion`` package, AND the
``dashboard.terminal`` module importable when running ``pytest`` from
this directory without an editable install. Mirrors the conftest layout
of the other agent packages.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_REPO_ROOT = _HERE.parent.parent
_AGENTLIB_SRC = _REPO_ROOT / "libs" / "agentlib" / "src"
_DASHBOARD_SRC = _REPO_ROOT / "agents" / "dashboard" / "src"
_TC_SRC = _HERE / "src"

for p in (_AGENTLIB_SRC, _DASHBOARD_SRC, _TC_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
