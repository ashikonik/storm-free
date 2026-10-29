"""Configuration loading for the free-tier STORM setup.

The config file is TOML.  It declares which model serves each STORM pipeline
stage, the fallback chain behind it, and the retrievers / hyperparameters to
use.  A ``[providers.<name>]`` table can pin a specific model for a provider,
which overrides the model named in a stage entry.

Example::

    [defaults]
    retriever = "duckduckgo"
    max_conv_turn = 2

    [stages.article_gen]
    model = "gemini/gemini-2.5-flash"
    fallbacks = ["groq/openai/gpt-oss-120b"]

    [providers.groq]
    model = "openai/gpt-oss-20b"
"""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .providers import (
    UnknownProviderError,
    get_provider,
    provider_of_model,
)

# The five language-model slots exposed by STORMWikiLMConfigs.
STAGES: tuple[str, ...] = (
    "conv_simulator",
    "question_asker",
    "outline_gen",
    "article_gen",
    "article_polish",
)

# Map our short stage names onto the attribute names STORM uses.
STAGE_ATTRS: dict[str, str] = {
    "conv_simulator": "conv_simulator_lm",
    "question_asker": "question_asker_lm",
    "outline_gen": "outline_gen_lm",
    "article_gen": "article_gen_lm",
    "article_polish": "article_polish_lm",
}

# Retriever name -> (module class name, human description).
RETRIEVERS: dict[str, str] = {
    "duckduckgo": "DuckDuckGoSearchRM (no API key required)",
    "bing": "BingSearch (BING_SEARCH_API_KEY)",
    "brave": "BraveRM (BRAVE_API_KEY)",
    "serper": "SerperRM (SERPER_API_KEY)",
    "tavily": "TavilySearchRM (TAVILY_API_KEY)",
    "you": "YouRM (YDC_API_KEY)",
    "searxng": "SearXNG (SEARXNG_API_KEY)",
    "google": "GoogleSearch (GOOGLE_API_KEY)",
}

DEFAULT_CONFIG_NAME = "free_config.toml"


class ConfigError(ValueError):
    """Raised when the configuration file is missing required information."""


@dataclass(frozen=True)
class StageConfig:
    """Model chain for one STORM pipeline stage."""

    name: str
    model: str
    fallbacks: tuple[str, ...] = ()

    @property
    def chain(self) -> tuple[str, ...]:
        """Primary model followed by its fallbacks, de-duplicated."""
        seen: list[str] = []
        for item in (self.model, *self.fallbacks):
            if item and item not in seen:
                seen.append(item)
        return tuple(seen)


@dataclass(frozen=True)
class FreeConfig:
    """Fully resolved configuration."""

    stages: dict[str, StageConfig]
    defaults: dict[str, Any] = field(default_factory=dict)
    provider_models: dict[str, str] = field(default_factory=dict)
    source_path: Path | None = None

    def stage(self, name: str) -> StageConfig:
        """Return the config for a stage, raising a clear error if absent."""
        try:
            return self.stages[name]
        except KeyError:
            raise ConfigError(
                f"No configuration for stage {name!r}. "
                f"Known stages: {', '.join(STAGES)}"
            ) from None

    def get_default(self, key: str, fallback: Any = None) -> Any:
        """Read a value from the ``[defaults]`` table."""
        return self.defaults.get(key, fallback)

    def resolved_chain(self, name: str) -> tuple[str, ...]:
        """The effective model chain for a stage, after provider pinning."""
        stage = self.stage(name)
        pinned = self.provider_models
        chain: list[str] = []
        for model in stage.chain:
            provider = provider_of_model(model)
            replacement = pinned.get(provider)
            chain.append(replacement or model)
        return tuple(chain)


def _parse_stage(name: str, raw: Any) -> StageConfig:
    if isinstance(raw, str):
        return StageConfig(name=name, model=raw)
    if not isinstance(raw, dict):
        raise ConfigError(
            f"Stage {name!r} must be a table or a model string, got {type(raw).__name__}"
        )
    model = raw.get("model")
    if not model:
        raise ConfigError(f"Stage {name!r} is missing the required 'model' key")
    fallbacks = raw.get("fallbacks", []) or []
    if isinstance(fallbacks, str):
        fallbacks = [fallbacks]
    if not isinstance(fallbacks, list):
        raise ConfigError(f"Stage {name!r}: 'fallbacks' must be a list of model ids")
    return StageConfig(
        name=name, model=str(model), fallbacks=tuple(map(str, fallbacks))
    )


def load_config(
    path: str | os.PathLike[str] | None = None,
    *,
    base_dir: str | os.PathLike[str] | None = None,
) -> FreeConfig:
    """Load and validate a TOML configuration file."""
    if path is None:
        root = Path(base_dir) if base_dir else Path.cwd()
        path = root / DEFAULT_CONFIG_NAME
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"Configuration file not found: {config_path}\n"
            "Run from the project root or pass --config explicitly."
        )

    try:
        with config_path.open("rb") as handle:
            raw = tomllib.load(handle)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Could not parse {config_path}: {exc}") from exc

    raw_stages = raw.get("stages", {})
    if not isinstance(raw_stages, dict):
        raise ConfigError("The [stages] section must be a table")

    stages: dict[str, StageConfig] = {}
    for name, value in raw_stages.items():
        if name not in STAGES:
            raise ConfigError(
                f"Unknown stage {name!r}. Known stages: {', '.join(STAGES)}"
            )
        stages[name] = _parse_stage(name, value)

    missing = [name for name in STAGES if name not in stages]
    if missing:
        raise ConfigError(
            "Missing required stage(s): "
            + ", ".join(missing)
            + ". Every STORM pipeline stage needs a model."
        )

    provider_models: dict[str, str] = {}
    raw_providers = raw.get("providers", {})
    if not isinstance(raw_providers, dict):
        raise ConfigError("The [providers] section must be a table")
    for provider, value in raw_providers.items():
        model = value.get("model") if isinstance(value, dict) else value
        if not model:
            continue
        provider_models[provider] = str(model)

    defaults = raw.get("defaults", {})
    if not isinstance(defaults, dict):
        raise ConfigError("The [defaults] section must be a table")

    config = FreeConfig(
        stages=stages,
        defaults=dict(defaults),
        provider_models=provider_models,
        source_path=config_path,
    )
    _validate(config)
    return config


def _validate(config: FreeConfig) -> None:
    """Check every configured model maps to a provider we know."""
    for name in STAGES:
        for model in config.stage(name).chain:
            try:
                provider = provider_of_model(model)
            except UnknownProviderError as exc:
                raise ConfigError(
                    f"Stage {name!r} references model {model!r}: {exc}"
                ) from exc
            get_provider(provider)  # raises UnknownProviderError if unknown

    for provider, model in config.provider_models.items():
        try:
            get_provider(provider)
        except UnknownProviderError as exc:
            raise ConfigError(
                f"[providers.{provider}] pins model {model!r} for an unknown provider: {exc}"
            ) from exc


def describe(config: FreeConfig) -> str:
    """Render a human-readable summary of the resolved configuration."""
    lines: list[str] = []
    if config.source_path:
        lines.append(f"Config: {config.source_path}")
    lines.append("")
    lines.append("Stage model chains:")
    for name in STAGES:
        chain = config.resolved_chain(name)
        rendered = " -> ".join(chain)
        lines.append(f"  {name:<18} {rendered}")
    if config.provider_models:
        lines.append("")
        lines.append("Pinned provider models:")
        for provider, model in sorted(config.provider_models.items()):
            lines.append(f"  {provider:<18} {model}")
    if config.defaults:
        lines.append("")
        lines.append("Defaults:")
        for key, value in sorted(config.defaults.items()):
            lines.append(f"  {key:<18} {value}")
    return "\n".join(lines)


__all__ = [
    "DEFAULT_CONFIG_NAME",
    "RETRIEVERS",
    "STAGES",
    "STAGE_ATTRS",
    "ConfigError",
    "FreeConfig",
    "StageConfig",
    "describe",
    "load_config",
]
