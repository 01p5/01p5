"""
Make ``agentlib`` importable when the package is not installed.

CI and local dev should be able to ``pytest`` from the package root
without an editable install. Mirrors the ``[tool.setuptools] package-dir``
in pyproject.toml.
"""
import sys
from pathlib import Path

_SRC = Path(__file__).parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))
