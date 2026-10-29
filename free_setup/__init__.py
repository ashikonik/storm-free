"""Free-tier setup for STORM (stanford-oval/storm).

Runs STORM's Wikipedia-style article generation against free-tier LLM APIs
with automatic failover between providers, using keyless web retrieval by
default.

Typical use is through ``run_storm_free.py`` at the project root.
"""

from __future__ import annotations

__version__ = "1.0.0"

import sys
from pathlib import Path


def _ensure_upstream_on_path() -> None:
    """Make the upstream STORM clone importable.

    The clone lives in ``storm/`` next to this package, but ``knowledge_storm``
    is not installed into the environment.  Adding it here means importing
    ``free_setup`` is enough -- callers (the CLI, the tests, the Streamlit
    demo) do not each have to arrange the path themselves.
    """
    clone = Path(__file__).resolve().parent.parent / "storm"
    if clone.is_dir():
        clone_str = str(clone)
        if clone_str not in sys.path:
            sys.path.insert(0, clone_str)


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
from .runner import (
    RunnerBuildError,
    build_lm,
    build_lm_configs,
    build_retriever,
    build_runner,
    build_stage_chain,
    describe_chains,
)
from .secrets import load_secrets

# Upstream's outline parser raises a bare IndexError when a model repeats the
# article title as the first heading, which free-tier models do often enough
# to break runs.  The repair is installed here so every entry point -- the
# CLI, the tests and the Streamlit demo -- gets it simply by importing
# ``free_setup``.
apply_outline_patch()

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
    "ProviderSpec",
    "RunnerBuildError",
    "StageConfig",
    "UnknownProviderError",
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
    "split_model",
]
