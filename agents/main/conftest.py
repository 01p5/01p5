"""Make ``agentlib`` and the ``main_agent`` package importable under
pytest without an editable install. Same shape as the other agents'
conftest.
"""
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_AGENTLIB_SRC = _HERE.parent.parent / "libs" / "agentlib" / "src"
_MAIN_SRC = _HERE / "src"

for p in (_AGENTLIB_SRC, _MAIN_SRC):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
