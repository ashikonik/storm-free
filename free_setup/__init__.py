"""Free-tier setup for STORM (stanford-oval/storm).

Runs STORM's Wikipedia-style article generation against free-tier LLM APIs
with automatic failover between providers, using keyless web retrieval by
default.

Typical use is through ``run_storm_free.py`` at the project root.
"""

from __future__ import annotations

__version__ = "1.0.1"

import logging
import sys
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


class UpstreamMissingError(RuntimeError):
    """The upstream STORM clone is not present.

    The clone lives in ``storm/`` next to this package and is gitignored, so
    a fresh clone of this repository does not have it.  Without it there is
    nothing to run, and the failure surfaces as a bare
    ``ModuleNotFoundError: No module named 'knowledge_storm'`` pointing at an
    internal file -- which tells the reader nothing about what to do.
    """


def upstream_path() -> Path:
    """Return the path the upstream STORM clone is expected at."""
    return Path(__file__).resolve().parent.parent / "storm"


def _ensure_upstream_on_path() -> None:
    """Make the upstream STORM clone importable.

    The clone lives in ``storm/`` next to this package, but ``knowledge_storm``
    is not installed into the environment.  Adding it here means importing
    ``free_setup`` is enough -- callers (the CLI, the tests, the Streamlit
    demo) do not each have to arrange the path themselves.

    This never raises: importing ``free_setup`` has to keep working without
    the clone so that ``--list-providers`` and the test suite still run.  Use
    :func:`require_upstream` before doing anything that needs upstream.
    """
    clone = upstream_path()
    if clone.is_dir():
        clone_str = str(clone)
        if clone_str not in sys.path:
            sys.path.insert(0, clone_str)


def require_upstream() -> Path:
    """Ensure the upstream clone is present, or explain how to get it.

    Returns the path to the clone.  Raises :class:`UpstreamMissingError`
    with instructions when it is absent.
    """
    clone = upstream_path()
    if not clone.is_dir():
        raise UpstreamMissingError(
            "The upstream STORM clone is missing.\n"
            f"Expected it at: {clone}\n"
            "\n"
            "Run the setup script from the project root to clone it and "
            "create the virtualenv:\n"
            "\n"
            "    ./setup.sh\n"
        )
    _ensure_upstream_on_path()
    return clone


_ensure_upstream_on_path()

from .config import (
    RETRIEVERS,
    STAGES,
    ConfigError,
    FreeConfig,
    StageConfig,
    describe,
    load_config,
)
from .fallback import CostRecord, CostTracker, FallbackLM, classify_error
from .outline_patch import apply_outline_patch
from .progress import CLICallbackHandler
from .providers import (
    FREE_MODELS,
    PROVIDERS,
    ProviderSpec,
    UnknownProviderError,
    discover_available,
    get_provider,
    list_providers,
    missing_providers,
    provider_of_model,
    split_model,
)
from .secrets import load_secrets
from .topic import InvalidTopicError, validate_topic

# ``runner`` imports the upstream ``knowledge_storm`` package at module
# level, and the clone is gitignored.  Importing it lazily keeps
# ``import free_setup`` working without the clone, so the CLI can report
# the missing clone with instructions instead of dying with a bare
# ModuleNotFoundError.
_LAZY_RUNNER_EXPORTS = {
    "RunnerBuildError",
    "build_lm",
    "build_lm_configs",
    "build_retriever",
    "build_runner",
    "build_stage_chain",
    "describe_chains",
}


def __getattr__(name: str) -> Any:
    """Resolve the runner's exports on first use (PEP 562).

    The return type is ``Any`` rather than ``object`` because the resolved
    names are functions and classes; ``object`` would make every call site
    look uncallable to a type checker.
    """
    if name in _LAZY_RUNNER_EXPORTS:
        from . import runner

        return getattr(runner, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


# Upstream's outline parser raises a bare IndexError when a model repeats the
# article title as the first heading, which free-tier models do often enough
# to break runs.  The repair is installed here so every entry point -- the
# CLI, the tests and the Streamlit demo -- gets it simply by importing
# ``free_setup``.
#
# The patch needs the upstream clone.  Without it the import must still
# succeed, so that the CLI can report the missing clone with instructions.
try:
    apply_outline_patch()
except ImportError:  # pragma: no cover - only hit without the upstream clone
    logger.debug("Upstream clone absent; the outline patch was not installed.")

__all__ = [
    "FREE_MODELS",
    "PROVIDERS",
    "RETRIEVERS",
    "STAGES",
    "CLICallbackHandler",
    "ConfigError",
    "CostRecord",
    "CostTracker",
    "FallbackLM",
    "FreeConfig",
    "InvalidTopicError",
    "ProviderSpec",
    "RunnerBuildError",
    "StageConfig",
    "UnknownProviderError",
    "UpstreamMissingError",
    "apply_outline_patch",
    "build_lm",
    "build_lm_configs",
    "build_retriever",
    "build_runner",
    "build_stage_chain",
    "classify_error",
    "describe",
    "describe_chains",
    "discover_available",
    "get_provider",
    "list_providers",
    "load_config",
    "load_secrets",
    "missing_providers",
    "provider_of_model",
    "require_upstream",
    "split_model",
    "upstream_path",
    "validate_topic",
]
