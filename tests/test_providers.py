"""Tests for the provider registry.

The registry is the one place that decides which API key maps to which
provider, so a mistake here is invisible until a run fails for no apparent
reason.  Two classes of bug are covered:

* **Naming** -- a model id is parsed into the right provider, including the
  nested forms some providers use (``groq/openai/gpt-oss-120b``).
* **Existence** -- every model id we advertise is one LiteLLM actually knows.
  A provider that LiteLLM does not support fails at call time with a
  confusing "LLM Provider NOT provided" error, hours into a run.  The
  ``test_free_models_resolve_in_litellm`` test catches it in a second.

Run with:  ./venv/bin/python -m pytest tests/test_providers.py -v
"""

from __future__ import annotations

import pytest

from free_setup.providers import (
    FREE_MODELS,
    PROVIDERS,
    UnknownProviderError,
    discover_available,
    get_provider,
    list_providers,
    missing_providers,
    provider_of_model,
    split_model,
)


class TestProviderOfModel:
    """Parsing a model id down to the provider that serves it."""

    def test_a_plain_prefix_is_the_provider(self) -> None:
        assert provider_of_model("groq/qwen/qwen3.8-27b") == "groq"

    def test_a_nested_prefix_binds_to_the_known_provider(self) -> None:
        """Groq hosts OpenAI-branded models, so the id has two segments."""
        assert provider_of_model("groq/openai/gpt-oss-120b") == "groq"

    def test_a_provider_name_deeper_in_the_path_is_found(self) -> None:
        assert provider_of_model("huggingface/meta-llama/Llama-3.1-8B-Instruct") == (
            "huggingface"
        )

    def test_a_prefix_that_is_not_a_provider_is_rejected(self) -> None:
        with pytest.raises(UnknownProviderError):
            provider_of_model("totally-unknown/some-model")

    def test_a_model_without_a_prefix_is_rejected(self) -> None:
        with pytest.raises(UnknownProviderError):
            provider_of_model("gpt-4o-mini")


class TestSplitModel:
    """Separating the provider from the bare model id."""

    def test_the_prefix_is_stripped(self) -> None:
        assert split_model("groq/qwen/qwen3.8-27b") == ("groq", "qwen/qwen3.8-27b")

    def test_a_nested_model_keeps_its_inner_path(self) -> None:
        provider, bare = split_model("groq/openai/gpt-oss-120b")
        assert provider == "groq"
        assert bare == "openai/gpt-oss-120b"


class TestRegistry:
    """Properties the registry itself must hold."""

    def test_every_provider_is_discoverable_by_name(self) -> None:
        for name in PROVIDERS:
            assert get_provider(name).name == name

    def test_an_unknown_provider_names_the_known_ones(self) -> None:
        """The error should tell the user what they could have typed."""
        with pytest.raises(UnknownProviderError) as excinfo:
            get_provider("nope")
        assert "groq" in str(excinfo.value)

    def test_every_provider_declares_at_least_one_env_var(self) -> None:
        """A provider with no key variable could never be enabled."""
        for spec in PROVIDERS.values():
            assert spec.env_vars, f"{spec.name} has no key env var"

    def test_every_provider_has_a_free_model_list(self) -> None:
        """``--list-providers`` would show nothing useful otherwise."""
        for name in PROVIDERS:
            assert FREE_MODELS.get(name), f"{name} has no free models listed"

    def test_free_model_lists_only_name_known_providers(self) -> None:
        for name in FREE_MODELS:
            assert name in PROVIDERS, f"{name} has models but is not registered"

    def test_the_registry_order_puts_working_providers_first(self) -> None:
        """Providers with a rejected key must not be preferred.

        ``discover_available`` preserves registry order, so an unusable
        provider placed above a working one would be tried first on every
        call.
        """
        names = list(PROVIDERS)
        working = ("groq", "gemini", "mistral")
        broken = ("together_ai", "sambanova", "openrouter")
        for good in working:
            for bad in broken:
                assert names.index(good) < names.index(bad), (
                    f"{good} should be tried before {bad}"
                )


class TestFreeModelsResolve:
    """Every advertised model id must be one LiteLLM understands.

    This is the test that catches a provider LiteLLM does not support.  The
    failure mode it prevents is a mid-run ``BadRequestError: LLM Provider NOT
    provided`` from an API call, which looks like a network fault rather than
    a registry mistake.
    """

    @pytest.mark.parametrize("provider", sorted(FREE_MODELS))
    def test_free_models_resolve_in_litellm(self, provider: str) -> None:
        from litellm.litellm_core_utils.get_llm_provider_logic import (
            get_llm_provider,
        )

        for model in FREE_MODELS[provider]:
            qualified = f"{provider}/{model}"
            get_llm_provider(qualified)  # raises if LiteLLM cannot route it

    @pytest.mark.parametrize("provider", sorted(PROVIDERS))
    def test_the_default_model_resolves_in_litellm(self, provider: str) -> None:
        from litellm.litellm_core_utils.get_llm_provider_logic import (
            get_llm_provider,
        )

        spec = get_provider(provider)
        get_llm_provider(spec.default_model)


class TestDiscovery:
    """Finding the providers that are usable right now."""

    @pytest.fixture(autouse=True)
    def _clean_env(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """Start each test with no provider keys in the environment."""
        for spec in PROVIDERS.values():
            for var in spec.env_vars:
                monkeypatch.delenv(var, raising=False)

    def test_nothing_is_available_without_keys(self) -> None:
        assert discover_available() == []

    def test_a_configured_provider_is_discovered(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-something")
        available = [spec.name for spec in discover_available()]
        assert "groq" in available

    def test_an_alias_env_var_counts(self, monkeypatch) -> None:
        """``GOOGLE_API_KEY`` is an accepted spelling for Gemini."""
        monkeypatch.setenv("GOOGLE_API_KEY", "some-key")
        available = [spec.name for spec in discover_available()]
        assert "gemini" in available

    def test_a_blank_key_is_not_a_key(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "   ")
        assert "groq" not in [spec.name for spec in discover_available()]

    def test_missing_providers_complements_discovered(self) -> None:
        assert len(discover_available()) + len(missing_providers()) == len(PROVIDERS)

    def test_the_order_follows_the_registry(self, monkeypatch) -> None:
        monkeypatch.setenv("MISTRAL_API_KEY", "k")
        monkeypatch.setenv("GROQ_API_KEY", "k")
        available = [spec.name for spec in discover_available()]
        assert available == ["groq", "mistral"]

    def test_list_providers_returns_every_spec(self) -> None:
        assert len(list_providers()) == len(PROVIDERS)
