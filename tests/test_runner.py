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
    MAX_STAGE_MAX_TOKENS,
    MIN_WRITING_MAX_TOKENS,
    NUM_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    RunnerBuildError,
    budget_for_model,
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


class TestBudgetForModel:
    """How many tokens a model is given for a stage.

    The budgets matter more than they look: a request that is cut off
    mid-sentence produces an article that reads as broken, and STORM
    writes one section per call.
    """

    def test_an_ordinary_model_keeps_upstreams_budget(self) -> None:
        assert budget_for_model("groq/qwen/qwen3.8-27b", "conv_simulator") == 500

    def test_a_writing_stage_gets_enough_room_for_a_section(self) -> None:
        """Upstream's 700 tokens cannot hold a whole section.

        Measured on ``groq/qwen/qwen3.8-27b``, a 700-token request came
        back cut off mid-sentence; 1400 came back complete.
        """
        budget = budget_for_model("groq/qwen/qwen3.8-27b", "article_gen")
        assert budget >= MIN_WRITING_MAX_TOKENS

    def test_a_reasoning_model_gets_more_room(self) -> None:
        """A reasoning model spends part of the budget thinking first."""
        ordinary = budget_for_model("groq/qwen/qwen3.8-27b", "article_gen")
        reasoning = budget_for_model("gemini/gemini-3-flash-preview", "article_gen")
        assert reasoning > ordinary

    def test_every_reasoning_marker_is_recognised(self) -> None:
        for model in (
            "gemini/gemini-3-flash-preview",
            "groq/openai/gpt-oss-120b",
            "deepseek/deepseek-reasoner",
        ):
            assert budget_for_model(model, "article_gen") > budget_for_model(
                "mistral/ministral-3b-latest", "article_gen"
            )

    def test_the_budget_never_exceeds_the_ceiling(self) -> None:
        budget = budget_for_model("gemini/gemini-3-flash-preview", "article_polish")
        assert budget <= MAX_STAGE_MAX_TOKENS

    def test_the_polish_stage_can_hold_a_whole_article(self) -> None:
        """Polishing rewrites the entire article in one call."""
        budget = budget_for_model("groq/qwen/qwen3.8-27b", "article_polish")
        assert budget >= 4000
