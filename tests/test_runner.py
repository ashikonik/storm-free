"""Tests for assembling a STORM runner from the free-tier configuration.

Two things are worth guarding here.  The first is the request timeout: LiteLLM
defaults to 6000 seconds, and a free-tier provider that stalls would hold the
pipeline hostage for over an hour before the fallback chain could engage.  The
second is that a provider without a key must be skipped rather than tried, so
one missing key does not break a config that names a dozen providers.

Run with:  ./venv/bin/python -m pytest tests/test_runner.py -v
"""

from __future__ import annotations

import pytest

from free_setup.config import FreeConfig, StageConfig
from free_setup.fallback import FallbackLM
from free_setup.providers import PROVIDERS, get_provider
from free_setup.runner import (
    NUM_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RunnerBuildError,
    build_lm,
    build_lm_configs,
    build_retriever,
    build_stage_chain,
    describe_chains,
)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no provider keys in the environment."""
    for spec in PROVIDERS.values():
        for var in spec.env_vars:
            monkeypatch.delenv(var, raising=False)


def _config(*chains: tuple[str, ...]) -> FreeConfig:
    """Build a config whose stages all use the same chain."""
    stages = {
        name: StageConfig(name=name, model=chain[0], fallbacks=chain[1:])
        for name in (
            "conv_simulator",
            "question_asker",
            "outline_gen",
            "article_gen",
            "article_polish",
        )
        for chain in [chains[0]]
    }
    return FreeConfig(stages=stages)


class TestBuildLm:
    """Turning a provider spec into a language-model client."""

    def test_the_timeout_is_capped(self, monkeypatch) -> None:
        """LiteLLM's own default is 6000 seconds.

        A free-tier provider that cannot answer within a minute is a provider
        that is not going to answer, so the timeout must be set explicitly
        rather than inherited.
        """
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        lm = build_lm(get_provider("groq"), "groq/qwen/qwen3.8-27b")

        assert lm.kwargs["timeout"] == REQUEST_TIMEOUT_SECONDS
        assert lm.kwargs["timeout"] < 6000

    def test_the_retry_count_is_capped(self, monkeypatch) -> None:
        """Three retries against a 60s timeout is four wasted minutes."""
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        lm = build_lm(get_provider("groq"), "groq/qwen/qwen3.8-27b")

        assert lm.kwargs["num_retries"] == NUM_RETRIES
        assert lm.kwargs["num_retries"] <= 1

    def test_the_api_key_is_passed_through(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-secret")
        lm = build_lm(get_provider("groq"), "groq/qwen/qwen3.8-27b")

        assert lm.kwargs["api_key"] == "gsk-secret"

    def test_the_model_keeps_its_provider_prefix(self, monkeypatch) -> None:
        """LiteLLM routes on the prefix, so it must survive."""
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        lm = build_lm(get_provider("groq"), "groq/qwen/qwen3.8-27b")

        assert lm.model == "groq/qwen/qwen3.8-27b"


class TestBuildStageChain:
    """Building the fallback chain for one stage."""

    def test_providers_without_keys_are_skipped(self, monkeypatch) -> None:
        """One missing key must not break a chain that names a dozen."""
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        config = _config(
            ("groq/qwen/qwen3.8-27b", "mistral/ministral-3b-latest"),
        )

        chain = build_stage_chain(config, "conv_simulator")

        assert chain.provider_names() == ["groq/qwen/qwen3.8-27b"]

    def test_the_order_is_preserved(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        monkeypatch.setenv("MISTRAL_API_KEY", "k")
        config = _config(
            ("groq/qwen/qwen3.8-27b", "mistral/ministral-3b-latest"),
        )

        chain = build_stage_chain(config, "conv_simulator")

        assert chain.provider_names() == [
            "groq/qwen/qwen3.8-27b",
            "mistral/ministral-3b-latest",
        ]

    def test_an_empty_chain_raises_with_an_actionable_message(self) -> None:
        """The error should say what to do, not just that it failed."""
        config = _config(("groq/qwen/qwen3.8-27b",))

        with pytest.raises(RunnerBuildError) as excinfo:
            build_stage_chain(config, "conv_simulator")

        message = str(excinfo.value)
        assert "secrets.toml" in message
        assert "Groq" in message

    def test_restricting_to_one_provider_drops_the_others(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        monkeypatch.setenv("MISTRAL_API_KEY", "k")
        config = _config(
            ("groq/qwen/qwen3.8-27b", "mistral/ministral-3b-latest"),
        )

        chain = build_stage_chain(config, "conv_simulator", only_providers=["groq"])

        assert chain.provider_names() == ["groq/qwen/qwen3.8-27b"]

    def test_restricting_to_a_provider_without_a_key_raises(self) -> None:
        config = _config(("groq/qwen/qwen3.8-27b",))

        with pytest.raises(RunnerBuildError):
            build_stage_chain(config, "conv_simulator", only_providers=["mistral"])


class TestBuildLmConfigs:
    """Building the five STORM language-model slots."""

    def test_every_slot_gets_a_fallback_chain(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        config = _config(("groq/qwen/qwen3.8-27b",))

        configs = build_lm_configs(config)

        for attr in (
            "conv_simulator_lm",
            "question_asker_lm",
            "outline_gen_lm",
            "article_gen_lm",
            "article_polish_lm",
        ):
            assert isinstance(getattr(configs, attr), FallbackLM)


class TestBuildRetriever:
    """Turning a retriever name into a working retriever."""

    def test_duckduckgo_needs_no_key(self) -> None:
        retriever = build_retriever("duckduckgo")
        assert retriever is not None

    def test_an_unknown_name_lists_the_valid_ones(self) -> None:
        with pytest.raises(RunnerBuildError) as excinfo:
            build_retriever("nosuchsearch")
        assert "duckduckgo" in str(excinfo.value)


class TestDescribeChains:
    """The summary the CLI prints before a run."""

    def test_it_names_every_stage(self, monkeypatch) -> None:
        monkeypatch.setenv("GROQ_API_KEY", "gsk-test")
        config = _config(("groq/qwen/qwen3.8-27b",))

        summary = describe_chains(build_lm_configs(config))

        for stage in ("conv_simulator", "question_asker", "article_polish"):
            assert stage in summary
