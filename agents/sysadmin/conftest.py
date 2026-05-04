"""
Make ``agentlib`` and the ``sysadmin`` package importable when running
``pytest`` from this directory without an editable install.

Mirrors the layout in ``libs/agentlib/conftest.py``.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_AGENTLIB_SRC = _HERE.parent.parent / "libs" / "agentlib" / "src"
_SYSADMIN_SRC = _HERE / "src"

for p in (_AGENTLIB_SRC, _SYSADMIN_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
