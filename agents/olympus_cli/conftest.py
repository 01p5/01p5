"""Make every Olympus package importable from this dir."""
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent
_PATHS = [
    _ROOT / "libs" / "agentlib" / "src",
    _ROOT / "agents" / "sysadmin" / "src",
    _ROOT / "agents" / "programmer" / "src",
    _ROOT / "agents" / "terraform" / "src",
    _ROOT / "agents" / "ansible" / "src",
    _HERE / "src",
]
for p in _PATHS:
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
