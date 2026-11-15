"""`python -m m241` support: run the command line interface.

Equivalent to the installed ``m241`` console script::

    python -m m241 selftest
"""

from __future__ import annotations

import sys

from m241.cli import main

if __name__ == "__main__":
    sys.exit(main())
