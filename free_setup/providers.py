"""Provider registry for free-tier LLM APIs.

Each provider maps to a LiteLLM model prefix and the environment variable that
holds its API key.  The prefix/env-var pairs were extracted from the LiteLLM
wheel itself (``litellm/litellm_core_utils/get_llm_provider_logic.py``), not
guessed, so they stay accurate across provider renames.

Providers are ordered by how generous their free tier is, so that a config
which lists "all providers" degrades gracefully.
"""

from __future__ import annotations

import os
from dataclasses import dataclass

# Environment variables that hold an API key for each provider.  A provider is
# considered "available" when at least one of these is set to a non-empty value.
# Several providers accept more than one spelling; we list every spelling we
# know of so an existing key is picked up regardless of which name was used.
_KEY_ENV_VARS: dict[str, tuple[str, ...]] = {
    "groq": ("GROQ_API_KEY",),
    "gemini": ("GEMINI_API_KEY", "GOOGLE_API_KEY"),
    "mistral": ("MISTRAL_API_KEY",),
    "nvidia_nim": ("NVIDIA_NIM_API_KEY",),
    "cohere": ("COHERE_API_KEY",),
    "ai21": ("AI21_API_KEY",),
    "galadriel": ("GALADRIEL_API_KEY",),
    "nebius": ("NEBIUS_API_KEY",),
    "deepinfra": ("DEEPINFRA_API_KEY",),
    "huggingface": ("HUGGINGFACE_API_KEY",),
    "ovhcloud": ("OVH_API_KEY", "OVH_AI_API_KEY"),
    "meta_llama": ("LLAMA_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "fireworks_ai": (
        "FIREWORKS_AI_API_KEY",
        "FIREWORKSAI_API_KEY",
        "FIREWORKS_API_KEY",
    ),
    "hyperbolic": ("HYPERBOLIC_API_KEY",),
    "novita": ("NOVITA_API_KEY",),
    "cerebras": ("CEREBRAS_API_KEY",),
    "perplexity": ("PERPLEXITY_API_KEY", "PERPLEXITYAI_API_KEY"),
    "xai": ("XAI_API_KEY",),
    # Providers below this line have a configured key that the API rejects or
    # rate-limits.  They stay registered so the fix is a key swap rather than
    # a code change, but they are ordered last so the fallback prefers a
    # provider that actually answers.
    "together_ai": ("TOGETHER_API_KEY", "TOGETHER_AI_API_KEY", "TOGETHERAI_API_KEY"),
    "sambanova": ("SAMBANOVA_API_KEY",),
    "openrouter": ("OPENROUTER_API_KEY",),
}


@dataclass(frozen=True)
class ProviderSpec:
    """Static description of an LLM provider we can talk to."""

    name: str
    """Registry key, e.g. ``"groq"``."""

    prefix: str
    """LiteLLM model prefix, e.g. ``"groq"`` (model strings are ``prefix/model``)."""

    default_model: str
    """Default fully-qualified model id, e.g. ``"groq/openai/gpt-oss-120b"``."""

    signup_url: str = ""
    """Where to obtain a free key."""

    requires_key: bool = True
    """False for providers that work without credentials.

    Every provider in this registry is a hosted free-tier API, so this is
    always True in practice.  The flag remains because ``is_available`` and
    the config loader branch on it, and a keyless hosted provider would
    still be a valid thing to add later.
    """

    base_url: str | None = None
    """Override endpoint, when the provider is not the LiteLLM default."""

    free_tier: str = ""
    """Short human-readable note about the free allowance."""

    notes: str = ""

    max_per_minute: int | None = None
    """Requests-per-minute cap on the free tier, when one is known.

    ``None`` means the provider's limit is not documented or is generous
    enough not to need pacing.  STORM issues many requests in parallel, so
    the runner throttles to this rate to avoid a wall of 429s.
    """

    max_per_day: int | None = None
    """Requests-per-day cap on the free tier, when one is known.

    This is the binding constraint on the truly free tiers: a single
    provider cannot serve a full STORM run on its own, so the config spreads
    the work across several models and providers.
    """

    @property
    def env_vars(self) -> tuple[str, ...]:
        """Environment variables that may hold this provider's API key."""
        return _KEY_ENV_VARS.get(self.name, ())

    def api_key(self) -> str | None:
        """Return the first configured API key for this provider, if any."""
        for var in self.env_vars:
            value = os.environ.get(var)
            if value and value.strip():
                return value.strip()
        return None

    def has_key(self) -> bool:
        """Whether a usable API key is present in the environment."""
        return self.api_key() is not None

    def is_available(self) -> bool:
        """Whether this provider can be used right now."""
        if not self.requires_key:
            return True
        return self.has_key()


def _spec(
    name: str,
    prefix: str,
    default_model: str,
    *,
    signup_url: str = "",
    requires_key: bool = True,
    base_url: str | None = None,
    free_tier: str = "",
    notes: str = "",
    max_per_minute: int | None = None,
    max_per_day: int | None = None,
) -> ProviderSpec:
    return ProviderSpec(
        name=name,
        prefix=prefix,
        default_model=default_model,
        signup_url=signup_url,
        requires_key=requires_key,
        base_url=base_url,
        free_tier=free_tier,
        notes=notes,
        max_per_minute=max_per_minute,
        max_per_day=max_per_day,
    )


# Ordered most-generous-free-tier first.  ``discover_available`` preserves this
# order, so an "all providers" chain tries the roomiest quota first.
PROVIDERS: dict[str, ProviderSpec] = {
    "groq": _spec(
        "groq",
        "groq",
        "groq/qwen/qwen3.8-27b",
        signup_url="https://console.groq.com/keys",
        free_tier="Free plan, no card. ~30 RPM / 8K TPM on free models.",
        notes=(
            "Very fast inference. Tight token limit; use for the lighter "
            "stages. The gpt-oss models answer but return an empty body at "
            "low token budgets, so qwen is the default here."
        ),
        max_per_minute=30,
    ),
    "gemini": _spec(
        "gemini",
        "gemini",
        "gemini/gemini-3-flash-preview",
        signup_url="https://aistudio.google.com/apikey",
        free_tier="Free tier, no card (AI Studio). 5 RPM, 20 RPD per model.",
        notes=(
            "Large context; good for the article-writing stages. The daily "
            "cap is counted per model, so a second model adds another 20 "
            "requests. RPD resets at midnight Pacific time. Google retired "
            "gemini-2.5-flash for new accounts, so the default is "
            "gemini-3-flash-preview."
        ),
        max_per_minute=5,
        max_per_day=20,
    ),
    "mistral": _spec(
        "mistral",
        "mistral",
        "mistral/ministral-3b-latest",
        signup_url="https://console.mistral.ai/api-keys",
        free_tier="Free mode, no card.",
        notes="Smaller models; good for cheap fallback duty.",
        max_per_minute=5,
    ),
    "nvidia_nim": _spec(
        "nvidia_nim",
        "nvidia_nim",
        "nvidia_nim/meta-llama/llama-3.3-70b-instruct",
        signup_url="https://build.nvidia.com",
        free_tier="Free credits on signup; broad model catalog.",
        notes="Llama/Qwen/DeepSeek/GLM/Mistral available.",
    ),
    "cohere": _spec(
        "cohere",
        "cohere",
        "cohere/command-r-plus",
        signup_url="https://dashboard.cohere.com/api-keys",
        free_tier="Trial key, no card. 1,000 calls/month, 20 RPM.",
        notes=(
            "Strong at grounded, cited answers -- a good fit for the "
            "outline and article stages."
        ),
        max_per_minute=20,
        max_per_day=1000,
    ),
    "ai21": _spec(
        "ai21",
        "ai21",
        "ai21/chat-studio",
        signup_url="https://studio.ai21.com",
        free_tier="Trial key on signup, no card.",
        notes="Jamba and Studio models; generous context.",
    ),
    "galadriel": _spec(
        "galadriel",
        "galadriel",
        "galadriel/galadriel-2-131b",
        signup_url="https://galadriel.ai",
        free_tier="Free credits on signup, no card.",
        notes="Open models hosted on a free tier.",
    ),
    "nebius": _spec(
        "nebius",
        "nebius",
        "nebius/llama3-8b-instruct",
        signup_url="https://studio.nebius.ai",
        free_tier="Free credits on signup, no card.",
        notes="Llama models on Nebius AI Studio.",
    ),
    "deepinfra": _spec(
        "deepinfra",
        "deepinfra",
        "deepinfra/meta-llama/llama-3.1-8b-instruct",
        signup_url="https://deepinfra.com",
        free_tier="Free credits on signup, no card.",
        notes="Wide open-model catalog.",
    ),
    "huggingface": _spec(
        "huggingface",
        "huggingface",
        "huggingface/meta-llama/Llama-3.1-8B-Instruct",
        signup_url="https://huggingface.co/settings/tokens",
        free_tier="Free credits on signup, no card.",
        notes=(
            "Hub models served through the inference providers router. The "
            "key is a read token, so scope it accordingly."
        ),
    ),
    "ovhcloud": _spec(
        "ovhcloud",
        "ovhcloud",
        "ovhcloud/llama3-70b",
        signup_url="https://endpoints.ai.cloud.ovh.net",
        free_tier="Anonymous access, no signup required.",
        notes="Open endpoints; small per-model rate limit but no key needed.",
    ),
    "meta_llama": _spec(
        "meta_llama",
        "meta_llama",
        "meta_llama/Llama-4-Scout-17B-16E-Instruct",
        signup_url="https://llama.developer.meta.com",
        free_tier="Free tier, no card.",
        notes="Official Meta-hosted Llama models; long context.",
    ),
    "fireworks_ai": _spec(
        "fireworks_ai",
        "fireworks_ai",
        "fireworks_ai/gpt-oss-20b",
        signup_url="https://fireworks.ai/api-keys",
        free_tier="Free credits on signup.",
        notes="Wide open-model catalog.",
    ),
    "hyperbolic": _spec(
        "hyperbolic",
        "hyperbolic",
        "hyperbolic/Qwen/Qwen2.5-72B-Instruct",
        signup_url="https://app.hyperbolic.xyz/",
        free_tier="Free credits on signup.",
        notes="Open models at low cost.",
    ),
    "novita": _spec(
        "novita",
        "novita",
        "novita/qwen/qwen3-4b-fp8",
        signup_url="https://novita.ai/dashboard",
        free_tier="Free credits on signup.",
        notes="Lots of small models.",
    ),
    "deepseek": _spec(
        "deepseek",
        "deepseek",
        "deepseek/deepseek-chat",
        signup_url="https://platform.deepseek.com/api_keys",
        free_tier="Signup credits; then per-token billing.",
        notes="Cheap, strong at long-form writing.",
    ),
    "perplexity": _spec(
        "perplexity",
        "perplexity",
        "perplexity/sonar",
        signup_url="https://www.perplexity.ai/settings/api",
        free_tier="Some free API requests.",
        notes="Search-grounded model.",
    ),
    "xai": _spec(
        "xai",
        "xai",
        "xai/grok-4-20-beta-0309-reasoning",
        signup_url="https://console.x.ai/",
        free_tier="Free credits on signup.",
        notes="Large context.",
    ),
    "cerebras": _spec(
        "cerebras",
        "cerebras",
        "cerebras/llama-3.3-70b",
        signup_url="https://cloud.cerebras.ai",
        free_tier="Trial credits, but now REQUIRES a verified payment method.",
        notes="Not truly keyless-free any more; kept for completeness.",
    ),
    # -- Providers whose configured key the API currently rejects ---------
    # These are ordered last so a "use every provider" chain prefers one
    # that actually answers.  Each note records the observed failure.
    "together_ai": _spec(
        "together_ai",
        "together_ai",
        "together_ai/openai/gpt-oss-20b",
        signup_url="https://api.together.ai/settings/api-keys",
        free_tier="Free credits on signup.",
        notes=(
            "Key configured but the API rejects it as invalid. Needs a new "
            "key from the dashboard before it is usable again."
        ),
    ),
    "sambanova": _spec(
        "sambanova",
        "sambanova",
        "sambanova/Meta-Llama-3.3-70B-Instruct",
        signup_url="https://cloud.sambanova.ai/",
        free_tier="Free developer tier.",
        notes=(
            "Key configured but the free tier 403s on the published model "
            "ids and rate-limits the rest. Kept for when the catalog changes."
        ),
    ),
    "openrouter": _spec(
        "openrouter",
        "openrouter",
        "openrouter/openai/gpt-oss-120b",
        signup_url="https://openrouter.ai/keys",
        free_tier="Free models limited to ~50 requests/day.",
        notes=(
            "Key configured but carries a $0.00 total limit that does not "
            "reset, so every request is refused. Needs the limit raised in "
            "the OpenRouter settings, or a different key."
        ),
        max_per_minute=1,
    ),
}


# Curated free models per provider.  These are the ids we know are usable on
# a free allowance; the live catalog moves, so `doctor` re-verifies them and
# ``tests/test_providers.py`` checks that every id here is one LiteLLM
# actually recognises.
FREE_MODELS: dict[str, tuple[str, ...]] = {
    "groq": (
        "qwen/qwen3.8-27b",
        "openai/gpt-oss-120b",
        "openai/gpt-oss-20b",
    ),
    "gemini": (
        "gemini-3-flash-preview",
        "gemini-flash-lite-latest",
        "gemini-3.8-flash",
        "gemini-2.5-flash",
        "gemini-2.5-flash-lite",
        "gemini-flash-latest",
    ),
    "mistral": (
        "ministral-3b-latest",
        "ministral-3b-2512",
        "devstral-small-latest",
        "codestral-latest",
    ),
    "nvidia_nim": (
        "meta-llama/llama-3.3-70b-instruct",
        "meta-llama/llama-3.1-8b-instruct",
        "qwen/qwen3-235b-a22b",
        "deepseek-ai/deepseek-r1",
        "mistralai/mistral-large-2407",
    ),
    "cohere": (
        "command-r-plus",
        "command-r",
        "command-a-03-2025",
    ),
    "ai21": (
        "chat-studio",
        "jamba-mini",
        "jamba-large",
    ),
    "galadriel": ("galadriel-2-131b",),
    "nebius": (
        "llama3-8b-instruct",
        "llama3-70b-instruct",
    ),
    "deepinfra": (
        "meta-llama/llama-3.1-8b-instruct",
        "meta-llama/llama-3.3-70b-instruct",
    ),
    "huggingface": ("meta-llama/Llama-3.1-8B-Instruct",),
    "ovhcloud": (
        "llama3-70b",
        "llama3-8b",
    ),
    "meta_llama": ("Llama-4-Scout-17B-16E-Instruct",),
    "sambanova": (
        "Meta-Llama-3.3-70B-Instruct",
        "Llama-4-Maverick-17B-128E-Instruct",
        "gpt-oss-120b",
    ),
    "fireworks_ai": (
        "gpt-oss-20b",
        "accounts/fireworks/models/gpt-oss-20b",
        "accounts/fireworks/models/nemotron-lightning-3p5-30b-a3b",
    ),
    "hyperbolic": (
        "Qwen/Qwen2.5-72B-Instruct",
        "meta-llama/Llama-3.3-70B-Instruct",
    ),
    "novita": (
        "qwen/qwen3-4b-fp8",
        "meta-llama/llama-3.1-8b-instruct",
    ),
    "deepseek": (
        "deepseek-chat",
        "deepseek-reasoner",
    ),
    "together_ai": (
        "openai/gpt-oss-20b",
        "Qwen/Qwen2-1.5B-Instruct",
        "Prism-ML/Ternary-Bonsai-27B",
    ),
    "openrouter": (
        "openai/gpt-oss-120b",
        "nvidia/nemotron-3.5-super-vl",
        "xingchen-agi/xing4.0-29b-a4b",
    ),
    "perplexity": (
        "sonar",
        "sonar-pro",
    ),
    "xai": (
        "grok-4-20-beta-0309-reasoning",
        "grok-4",
    ),
    "cerebras": (
        "llama-3.3-70b",
        "llama3.1-8b",
    ),
}


class UnknownProviderError(ValueError):
    """Raised when a model string names a provider we do not know about."""


def get_provider(name: str) -> ProviderSpec:
    """Look up a provider by registry name."""
    try:
        return PROVIDERS[name]
    except KeyError:
        known = ", ".join(sorted(PROVIDERS))
        raise UnknownProviderError(
            f"Unknown provider {name!r}. Known providers: {known}"
        ) from None


def provider_of_model(model: str) -> str:
    """Extract the provider name from a fully-qualified model id.

    ``"groq/openai/gpt-oss-120b"`` -> ``"groq"``.
    ``"openai/gpt-oss-120b"``    -> ``"groq"`` (matched against known providers).
    """
    if "/" not in model:
        raise UnknownProviderError(
            f"Model {model!r} has no provider prefix. Use 'provider/model' form."
        )
    prefix = model.split("/", 1)[0]
    if prefix in PROVIDERS:
        return prefix
    # Some providers namespace further (e.g. "openai/gpt-oss-120b" on groq), so
    # look for a known provider in the *leading* segments only.  Scanning the
    # whole path would let "foo/xai/bar" bind to xai, which is a different
    # provider entirely.
    segments = model.split("/")
    for depth in range(1, len(segments)):
        candidate = "/".join(segments[:depth])
        if candidate in PROVIDERS:
            return candidate
    raise UnknownProviderError(
        f"Could not determine a provider for model {model!r}. "
        f"Known providers: {', '.join(sorted(PROVIDERS))}"
    )


def split_model(model: str) -> tuple[str, str]:
    """Split a fully-qualified model id into ``(provider_name, bare_model)``."""
    provider = provider_of_model(model)
    spec = PROVIDERS[provider]
    if model.startswith(f"{spec.prefix}/"):
        return provider, model[len(spec.prefix) + 1 :]
    return provider, model


def discover_available(
    names: list[str] | None = None,
) -> list[ProviderSpec]:
    """Return providers that are usable right now.

    A provider qualifies when its API key is present in the environment, or
    when it does not require a key.  Order follows the registry so the
    roomiest free tier is tried first.
    """
    candidates = (
        list(PROVIDERS.values()) if names is None else [get_provider(n) for n in names]
    )
    return [spec for spec in candidates if spec.is_available()]


def missing_providers(
    names: list[str] | None = None,
) -> list[ProviderSpec]:
    """Return providers that are known but not currently usable."""
    candidates = (
        list(PROVIDERS.values()) if names is None else [get_provider(n) for n in names]
    )
    return [spec for spec in candidates if not spec.is_available()]


def list_providers() -> list[ProviderSpec]:
    """All known providers, in registry order."""
    return list(PROVIDERS.values())


__all__ = [
    "FREE_MODELS",
    "PROVIDERS",
    "ProviderSpec",
    "UnknownProviderError",
    "discover_available",
    "get_provider",
    "list_providers",
    "missing_providers",
    "provider_of_model",
    "split_model",
]
