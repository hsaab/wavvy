"""Pytest bootstrap: make backend modules importable as top-level names.

The app runs with ``backend/`` as the working directory (``uvicorn main:app``),
so modules import each other as ``import cart_builder`` rather than
``backend.cart_builder``. Adding that directory to ``sys.path`` lets the
test suite resolve the same top-level imports.
"""

from __future__ import annotations

import sys
from pathlib import Path

_BACKEND_DIR = Path(__file__).resolve().parent.parent
_backend = str(_BACKEND_DIR)
if _backend not in sys.path:
    sys.path.insert(0, _backend)
