"""Loading API keys from a TOML file.

The keys live in ``secrets.toml`` at the project root, which is gitignored.
This module exists because upstream's ``knowledge_storm.utils.load_api_key``
assigns every entry to ``os.environ`` unconditionally, which means an empty
placeholder in the template (``GROQ_API_KEY = ""``) would *erase* a key that
is already exported in the shell.  That silently turns a working setup into a
"no key" one, so the loading here is deliberately conservative:

- an empty value never overwrites anything;
- a value already present in the environment is left alone;
- only keys that are genuinely new are applied.
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)


def load_secrets(path: str | Path) -> int:
    """Load API keys from a TOML file into the environment.

    Returns the number of variables that were actually set.  A missing file
    is not an error: the pipeline can run with keys exported in the shell
    alone.
    """
    path = Path(path)
    if not path.exists():
        return 0

    try:
        import toml
    except ImportError:  # pragma: no cover - toml ships with the upstream deps
        logger.warning("toml is not installed; cannot read %s", path)
        return 0

    try:
        data = toml.load(path)
    except (OSError, toml.TomlDecodeError) as exc:
        logger.warning("Could not read %s: %s", path, exc)
        return 0

    loaded = 0
    for key, value in data.items():
        value = str(value).strip()
        if not value:
            # An empty placeholder must not shadow a real key from the shell.
            continue
        if os.environ.get(key, "").strip():
            # The shell already has this one; the file does not override it.
            continue
        os.environ[key] = value
        loaded += 1

    if loaded:
        logger.info("Loaded %d API key(s) from %s", loaded, path)
    return loaded


__all__ = ["load_secrets"]
