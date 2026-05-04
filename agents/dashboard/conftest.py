"""Make agentlib + olympus_cli + dashboard importable for tests."""
import sys
from pathlib import Path

_HERE = Path(__file__).parent
_ROOT = _HERE.parent.parent
for p in (
    _ROOT / "libs" / "agentlib" / "src",
    _ROOT / "agents" / "olympus_cli" / "src",
    _ROOT / "agents" / "sysadmin" / "src",
    _ROOT / "agents" / "programmer" / "src",
    _ROOT / "agents" / "terraform" / "src",
    _ROOT / "agents" / "ansible" / "src",
    _HERE / "src",
):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))
