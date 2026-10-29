"""Shared pytest configuration.

The upstream STORM clone lives in ``storm/`` at the project root, and that is
where the importable ``knowledge_storm`` package is.  Adding it to
``sys.path`` here means the tests can import ``free_setup`` directly without
the caller having to set anything up.
"""

from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
STORM_CLONE = PROJECT_ROOT / "storm"

if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))
if STORM_CLONE.is_dir() and str(STORM_CLONE) not in sys.path:
    sys.path.insert(0, str(STORM_CLONE))
