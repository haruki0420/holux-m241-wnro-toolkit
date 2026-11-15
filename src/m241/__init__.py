"""Cross-platform toolkit for the Holux M-241 GPS logger.

Public submodules are imported lazily so that ``import m241`` stays cheap and
so that commands which need no serial hardware work on any host.
"""

from __future__ import annotations

__all__ = ["__version__", "errors"]

__version__ = "1.0.1"
