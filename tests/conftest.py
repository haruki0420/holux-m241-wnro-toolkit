"""Shared pytest fixtures and import-path setup for the m241 test suite.

The project uses a ``src/`` layout, so the source tree is prepended to
``sys.path`` here before any test module imports ``m241``.  Keep this file
additive: streams add their own fixtures, but do not remove the ``src``
bootstrap or the session-scoped :func:`repo_root` fixture.
"""

from __future__ import annotations

import sys
from pathlib import Path

#: Repository root (``tests/`` lives directly inside it).
REPO_ROOT = Path(__file__).resolve().parents[1]
SRC = REPO_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

import pytest  # noqa: E402  (must follow the sys.path bootstrap)


@pytest.fixture(scope="session")
def repo_root() -> Path:
    """Absolute path of the repository root."""
    return REPO_ROOT
