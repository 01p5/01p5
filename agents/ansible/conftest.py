"""Make agentlib + the ansible_agent package importable from this dir."""
import sys
from pathlib import Path

_HERE = Path(__file__).parent
for p in (_HERE.parent.parent / "libs" / "agentlib" / "src", _HERE / "src"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
