"""Assemble STORM runners from the free-tier configuration.

This module is the bridge between :mod:`free_setup.config` (what to run) and
the upstream ``knowledge_storm`` package (how to run it).  Nothing upstream is
modified: we build a ``STORMWikiLMConfigs`` whose five slots each hold a
:class:`~free_setup.fallback.FallbackLM` chain, then hand the whole thing to
``STORMWikiRunner``.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from typing import Any

from knowledge_storm import (
    STORMWikiLMConfigs,
    STORMWikiRunner,
    STORMWikiRunnerArguments,
)
from knowledge_storm.lm import LitellmModel

from .config import STAGE_ATTRS, STAGES, FreeConfig
from .fallback import FallbackLM
from .providers import (
    ProviderSpec,
    UnknownProviderError,
    get_provider,
    split_model,
)

logger = logging.getLogger(__name__)

# Per-stage max_tokens, mirroring the defaults in
# knowledge_storm.storm_wiki.engine.STORMWikiLMConfigs.init_openai_model.
# The polish stage needs a large budget because it rewrites the whole article.
STAGE_MAX_TOKENS: dict[str, int] = {
    "conv_simulator": 500,
    "question_asker": 500,
    "outline_gen": 400,
    "article_gen": 700,
    "article_polish": 4000,
}

# Reasoning models need a bigger budget than the nominal one.
#
# ``max_tokens`` bounds the *whole* answer, and a reasoning model spends part
# of it thinking before it writes anything.  Upstream's budgets are sized for
# ``gpt-4o``, which spends nothing on reasoning, so on a reasoning model the
# thinking eats the budget and the article is cut off mid-sentence: the
# article-gen stage asked for 700 tokens and Gemini 3 spent 4176 of them
# before it produced a single section.
#
# Multiplying the budget for the known reasoning models leaves the ordinary
# models on upstream's exact defaults and gives the reasoning ones room to
# finish.  The provider's own quota still applies, so this does not make a
# free tier more expensive -- it makes a run produce a complete article
# instead of a truncated one.
REASONING_MODEL_MARKERS: tuple[str, ...] = (
    "gemini-3",
    "gpt-oss",
    "o1",
    "o3",
    "o4",
    "r1",
    "deepseek-reasoner",
    "magistral-medium",
)

# How much to multiply a reasoning model's budget by.
REASONING_TOKEN_MULTIPLIER = 4

# The smallest budget any writing stage may carry.
#
# Upstream's ``article_gen`` budget of 700 tokens is sized for a short
# GPT-4 answer, but the stage asks the model for a *whole Wikipedia
# section*, and a section does not fit in 700 tokens: measured on
# ``groq/qwen/qwen3.8-27b``, a 700-token request came back cut off
# mid-sentence ("The press utilized a platen that") while the same
# request at 1400 tokens came back complete.  A truncated section is
# worse than a slightly larger request, because the article reads as
# broken.  Writing stages therefore get a floor of 1400 tokens.
MIN_WRITING_MAX_TOKENS = 1400

# The largest budget any single request may carry, whatever the stage.
# The article-polish stage rewrites the entire article in one call, so the
# ceiling has to leave room for a real article.
MAX_STAGE_MAX_TOKENS = 16_000

# The stages whose output is prose the reader sees, and which therefore
# must not be cut off part-way through.
_WRITING_STAGES = frozenset({"article_gen", "article_polish", "outline_gen"})


def budget_for_model(model_id: str, stage: str) -> int:
    """Return the token budget to use for ``model_id`` in ``stage``.

    Ordinary models get upstream's per-stage budget, raised to the writing
    floor where upstream's own value is too small to hold a section.
    Reasoning models get that budget multiplied, so the part they spend
    thinking does not eat into the part that has to hold the answer.
    """
    budget = STAGE_MAX_TOKENS.get(stage, 1000)
    if stage in _WRITING_STAGES:
        budget = max(budget, MIN_WRITING_MAX_TOKENS)
    lowered = model_id.lower()
    if any(marker in lowered for marker in REASONING_MODEL_MARKERS):
        budget *= REASONING_TOKEN_MULTIPLIER
    return min(budget, MAX_STAGE_MAX_TOKENS)


# Hard ceiling on how long a single provider may take to answer.
# LiteLLM's own default is 6000 seconds, which is fine for a paid API that is
# expected to answer but disastrous for a free tier: a provider that stalls
# holds the pipeline hostage for well over an hour before the fallback chain
# gets a chance to engage.  A free-tier provider that cannot answer within a
# minute is a provider that is not going to answer, so we give up and move on.
REQUEST_TIMEOUT_SECONDS = 60

# How many times LiteLLM should retry a failed request before giving up on the
# provider.  The default is 3, which multiplies the timeout above: a rate
# limited provider would cost four minutes before the fallback engaged.
# One retry is enough to ride out a momentary blip.
NUM_RETRIES = 1


class RunnerBuildError(RuntimeError):
    """Raised when a runner cannot be assembled from the current config."""


def build_lm(
    spec: ProviderSpec,
    model: str,
    *,
    max_tokens: int = 1000,
    temperature: float = 1.0,
    top_p: float = 0.9,
) -> Any:
    """Instantiate a single language model client for a provider.

    Every provider goes through LiteLLM, which handles the OpenAI-compatible
    APIs uniformly.  ``model`` must be the *prefixed* id
    (``gemini/gemini-2.5-flash``), because LiteLLM uses that prefix to decide
    which provider to route to.
    """
    api_key = spec.api_key()
    kwargs: dict[str, Any] = {
        "max_tokens": max_tokens,
        "temperature": temperature,
        "top_p": top_p,
        # These two reach litellm.completion: LitellmModel.__init__ keeps
        # **kwargs in self.kwargs, and __call__ merges them into the request.
        "timeout": REQUEST_TIMEOUT_SECONDS,
        "num_retries": NUM_RETRIES,
    }
    if api_key:
        kwargs["api_key"] = api_key
    if spec.base_url:
        kwargs["api_base"] = spec.base_url
    return LitellmModel(model=model, **kwargs)


def _resolve_model(model_id: str) -> tuple[ProviderSpec, str]:
    """Split a model id into its provider spec and the id to pass to the client.

    LiteLLM needs the provider prefix intact (``gemini/gemini-2.5-flash``) so it
    can route the request.
    """
    provider_name, bare = split_model(model_id)
    spec = get_provider(provider_name)
    return spec, f"{spec.prefix}/{bare}"


def _client_model_id(spec: ProviderSpec, client_model: str) -> str:
    """The model id the built client will report.

    ``FallbackLM`` keys its rate limiters on ``lm.model``, so this must match
    what ``build_lm`` sets.  ``client_model`` is the value already resolved
    by :func:`_resolve_model`: it carries the provider prefix for LiteLLM.
    """
    return client_model


def build_stage_chain(
    config: FreeConfig,
    stage: str,
    *,
    only_providers: Sequence[str] | None = None,
    use_cache: bool = False,
) -> FallbackLM:
    """Build the fallback chain for one STORM stage.

    Providers whose API key is missing are skipped, so a config that lists
    several providers still works with only one key configured.  When no
    provider in the chain is usable the build fails with an actionable
    message: every provider here is a hosted free-tier API, and there is no
    local model to fall back to.
    """
    lms: list[Any] = []
    skipped: list[str] = []
    rate_limits: dict[str, int] = {}
    daily_limits: dict[str, int] = {}

    for model_id in config.resolved_chain(stage):
        try:
            spec, client_model = _resolve_model(model_id)
        except UnknownProviderError as exc:
            logger.warning("Skipping model %s: %s", model_id, exc)
            skipped.append(model_id)
            continue
        if only_providers is not None and spec.name not in only_providers:
            continue
        if not spec.is_available():
            skipped.append(model_id)
            continue
        # The budget is per model, because a reasoning model in the same
        # chain needs more room than an ordinary one.
        lms.append(
            build_lm(
                spec,
                client_model,
                max_tokens=budget_for_model(model_id, stage),
            )
        )
        client_id = _client_model_id(spec, client_model)
        if spec.max_per_minute:
            rate_limits[client_id] = spec.max_per_minute
        if spec.max_per_day:
            daily_limits[client_id] = spec.max_per_day

    if not lms:
        detail = f"Skipped (no key): {', '.join(skipped) or 'none'}."
        if only_providers is not None:
            detail += f" Restricted to: {', '.join(only_providers)}."
        raise RunnerBuildError(
            f"No available provider for stage {stage!r}. {detail} "
            "Add at least one free API key to secrets.toml "
            "(Groq, Mistral, NVIDIA NIM and OpenRouter are all free and "
            "need no credit card)."
        )

    if skipped:
        logger.info(
            "Stage %s: skipped %d model(s) without a usable key: %s",
            stage,
            len(skipped),
            ", ".join(skipped),
        )
    return FallbackLM(
        lms,
        name=stage,
        use_cache=use_cache,
        rate_limits=rate_limits,
        daily_limits=daily_limits,
    )


def build_lm_configs(
    config: FreeConfig,
    *,
    only_providers: Sequence[str] | None = None,
    use_cache: bool = False,
) -> STORMWikiLMConfigs:
    """Build a ``STORMWikiLMConfigs`` with a fallback chain per stage."""
    lm_configs = STORMWikiLMConfigs()
    for stage in STAGES:
        chain = build_stage_chain(
            config,
            stage,
            only_providers=only_providers,
            use_cache=use_cache,
        )
        setattr(lm_configs, STAGE_ATTRS[stage], chain)
    return lm_configs


def build_retriever(
    name: str,
    k: int = 3,
    **kwargs: Any,
) -> Any:
    """Instantiate a retriever by name.

    ``duckduckgo`` is the default because it needs no API key.
    """
    from knowledge_storm import rm as rm_module

    name = (name or "duckduckgo").lower()
    if name == "duckduckgo":
        # Our own retriever, not upstream's: upstream pins the deprecated
        # ``backend="api"``, which today returns unrelated pages instead of
        # search results.  See free_setup/retrievers.py.
        from .retrievers import get_duckduckgo_retriever

        return get_duckduckgo_retriever(
            k=k,
            safe_search=kwargs.get("safe_search", "On"),
            region=kwargs.get("region", "us-en"),
        )
    if name == "bing":
        return rm_module.BingSearch(
            bing_search_api_key=os.environ.get("BING_SEARCH_API_KEY"), k=k
        )
    if name == "brave":
        return rm_module.BraveRM(
            brave_search_api_key=os.environ.get("BRAVE_API_KEY"), k=k
        )
    if name == "serper":
        return rm_module.SerperRM(
            serper_search_api_key=os.environ.get("SERPER_API_KEY"), k=k
        )
    if name == "tavily":
        return rm_module.TavilySearchRM(
            tavily_search_api_key=os.environ.get("TAVILY_API_KEY"),
            k=k,
            include_raw_content=True,
        )
    if name == "you":
        return rm_module.YouRM(ydc_api_key=os.environ.get("YDC_API_KEY"), k=k)
    if name == "searxng":
        return rm_module.SearXNG(searxng_api_key=os.environ.get("SEARXNG_API_KEY"), k=k)
    if name == "google":
        return rm_module.GoogleSearch(
            google_api_key=os.environ.get("GOOGLE_API_KEY"), k=k
        )
    raise RunnerBuildError(
        f"Unknown retriever {name!r}. "
        "Choose one of: duckduckgo, bing, brave, serper, tavily, you, searxng, google."
    )


def build_runner(
    config: FreeConfig,
    output_dir: str,
    *,
    retriever: str = "duckduckgo",
    only_providers: Sequence[str] | None = None,
    use_cache: bool = False,
    **overrides: Any,
) -> tuple[STORMWikiRunner, STORMWikiLMConfigs]:
    """Assemble a ready-to-run ``STORMWikiRunner``.

    ``overrides`` may contain any of the ``STORMWikiRunnerArguments`` field
    names (``max_conv_turn``, ``max_perspective``, ``search_top_k``,
    ``retrieve_top_k``, ``max_thread_num``, ``max_search_queries_per_turn``,
    ``disable_perspective``).
    """
    defaults = dict(config.defaults)
    defaults.update(overrides)

    engine_args = STORMWikiRunnerArguments(
        output_dir=output_dir,
        max_conv_turn=int(defaults.get("max_conv_turn", 2)),
        max_perspective=int(defaults.get("max_perspective", 2)),
        max_search_queries_per_turn=int(defaults.get("max_search_queries_per_turn", 2)),
        search_top_k=int(defaults.get("search_top_k", 3)),
        retrieve_top_k=int(defaults.get("retrieve_top_k", 5)),
        max_thread_num=int(defaults.get("max_thread_num", 2)),
        disable_perspective=bool(defaults.get("disable_perspective", False)),
    )

    lm_configs = build_lm_configs(
        config, only_providers=only_providers, use_cache=use_cache
    )
    rm = build_retriever(
        retriever or str(defaults.get("retriever", "duckduckgo")),
        k=engine_args.search_top_k,
    )
    runner = STORMWikiRunner(engine_args, lm_configs, rm)
    return runner, lm_configs


def describe_chains(lm_configs: STORMWikiLMConfigs) -> str:
    """Render the resolved provider chain for every stage."""
    lines: list[str] = []
    for stage in STAGES:
        chain = getattr(lm_configs, STAGE_ATTRS[stage])
        if isinstance(chain, FallbackLM):
            providers = " -> ".join(chain.provider_names())
            lines.append(f"  {stage:<18} {providers}")
        else:
            lines.append(f"  {stage:<18} {getattr(chain, 'model', chain)}")
    return "\n".join(lines)


__all__ = [
    "NUM_RETRIES",
    "REQUEST_TIMEOUT_SECONDS",
    "STAGE_MAX_TOKENS",
    "RunnerBuildError",
    "build_lm",
    "build_lm_configs",
    "build_retriever",
    "build_runner",
    "build_stage_chain",
    "describe_chains",
]
