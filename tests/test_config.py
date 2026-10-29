"""Tests for loading and validating the TOML configuration.

The configuration decides which model serves each stage of the pipeline, so a
typo in it should fail loudly at load time -- not halfway through a run that
has already spent a provider's daily quota.  These tests cover the validation
that turns a bad config into an immediate, readable error.

Run with:  ./venv/bin/python -m pytest tests/test_config.py -v
"""

from __future__ import annotations

from pathlib import Path

import pytest

from free_setup.config import (
    RETRIEVERS,
    STAGES,
    ConfigError,
    StageConfig,
    describe,
    load_config,
)

# A complete, valid configuration used as the base for the negative cases.
VALID_CONFIG = """
[defaults]
retriever = "duckduckgo"
max_conv_turn = 2

[stages.conv_simulator]
model = "gemini/gemini-2.5-flash-lite"

[stages.question_asker]
model = "gemini/gemini-2.5-flash"

[stages.outline_gen]
model = "gemini/gemini-2.5-flash-lite"

[stages.article_gen]
model = "gemini/gemini-2.5-flash"

[stages.article_polish]
model = "gemini/gemini-2.5-flash-lite"
"""


@pytest.fixture
def write_config(tmp_path: Path):
    """Write a TOML string to a temporary config file and return its path."""

    def _write(text: str, name: str = "free_config.toml") -> Path:
        path = tmp_path / name
        path.write_text(text)
        return path

    return _write


class TestLoadConfig:
    """Reading a configuration file."""

    def test_a_valid_config_loads(self, write_config) -> None:
        config = load_config(write_config(VALID_CONFIG))
        assert set(config.stages) == set(STAGES)

    def test_a_missing_file_is_reported(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_config(tmp_path / "nope.toml")
        assert "not found" in str(excinfo.value).lower()

    def test_malformed_toml_is_reported(self, write_config) -> None:
        with pytest.raises(ConfigError) as excinfo:
            load_config(write_config("this is = = not toml\n"))
        assert "parse" in str(excinfo.value).lower()

    def test_defaults_are_read(self, write_config) -> None:
        config = load_config(write_config(VALID_CONFIG))
        assert config.get_default("max_conv_turn") == 2
        assert config.get_default("retriever") == "duckduckgo"

    def test_an_absent_default_returns_the_fallback(self, write_config) -> None:
        config = load_config(write_config(VALID_CONFIG))
        assert config.get_default("nope", "fallback") == "fallback"

    def test_the_source_path_is_recorded(self, write_config) -> None:
        path = write_config(VALID_CONFIG)
        config = load_config(path)
        assert config.source_path == path


class TestValidation:
    """Configurations that must be rejected."""

    def test_an_unknown_stage_is_rejected(self, write_config) -> None:
        text = VALID_CONFIG + '\n[stages.not_a_stage]\nmodel = "groq/x"\n'
        with pytest.raises(ConfigError) as excinfo:
            load_config(write_config(text))
        assert "not_a_stage" in str(excinfo.value)

    def test_a_missing_stage_is_rejected(self, write_config) -> None:
        text = VALID_CONFIG.replace(
            '[stages.article_polish]\nmodel = "gemini/gemini-2.5-flash-lite"\n', ""
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(write_config(text))
        assert "article_polish" in str(excinfo.value)

    def test_a_stage_without_a_model_is_rejected(self, write_config) -> None:
        text = VALID_CONFIG.replace(
            '[stages.article_polish]\nmodel = "gemini/gemini-2.5-flash-lite"\n',
            "[stages.article_polish]\n",
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(write_config(text))
        assert "model" in str(excinfo.value)

    def test_an_unknown_provider_is_rejected(self, write_config) -> None:
        """A model naming a provider we do not know cannot be routed."""
        text = VALID_CONFIG.replace(
            'model = "gemini/gemini-2.5-flash-lite"\n\n[stages.question_asker]',
            'model = "nosuchprovider/some-model"\n\n[stages.question_asker]',
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(write_config(text))
        assert "nosuchprovider" in str(excinfo.value)

    def test_an_unknown_fallback_provider_is_rejected(self, write_config) -> None:
        text = VALID_CONFIG.replace(
            'model = "gemini/gemini-2.5-flash-lite"\n\n[stages.question_asker]',
            'model = "gemini/gemini-2.5-flash-lite"\n'
            'fallbacks = ["nosuchprovider/some-model"]\n\n[stages.question_asker]',
        )
        with pytest.raises(ConfigError):
            load_config(write_config(text))

    def test_a_non_list_fallbacks_is_rejected(self, write_config) -> None:
        text = VALID_CONFIG.replace(
            'model = "gemini/gemini-2.5-flash-lite"\n\n[stages.question_asker]',
            'model = "gemini/gemini-2.5-flash-lite"\n'
            "fallbacks = 5\n\n[stages.question_asker]",
        )
        with pytest.raises(ConfigError) as excinfo:
            load_config(write_config(text))
        assert "list" in str(excinfo.value)

    def test_a_string_fallback_is_accepted(self, write_config) -> None:
        """A single fallback written as a string is a natural mistake."""
        text = VALID_CONFIG.replace(
            'model = "gemini/gemini-2.5-flash-lite"\n\n[stages.question_asker]',
            'model = "gemini/gemini-2.5-flash-lite"\n'
            'fallbacks = "groq/qwen/qwen3.8-27b"\n\n[stages.question_asker]',
        )
        config = load_config(write_config(text))
        assert "groq/qwen/qwen3.8-27b" in config.stage("conv_simulator").fallbacks


class TestStageConfig:
    """The chain of models for one stage."""

    def test_the_chain_starts_with_the_primary(self) -> None:
        stage = StageConfig(name="x", model="a/1", fallbacks=("b/2",))
        assert stage.chain == ("a/1", "b/2")

    def test_the_chain_deduplicates(self) -> None:
        """The same model twice would waste a slot in the chain."""
        stage = StageConfig(name="x", model="a/1", fallbacks=("a/1", "b/2"))
        assert stage.chain == ("a/1", "b/2")

    def test_an_empty_fallback_is_dropped(self) -> None:
        stage = StageConfig(name="x", model="a/1", fallbacks=("", "b/2"))
        assert stage.chain == ("a/1", "b/2")


class TestProviderPinning:
    """The ``[providers.<name>]`` table overrides models across stages."""

    def test_a_pinned_model_replaces_the_provider_everywhere(
        self, write_config
    ) -> None:
        text = (
            VALID_CONFIG + '\n[providers.gemini]\nmodel = "gemini/gemini-2.5-flash"\n'
        )
        config = load_config(write_config(text))

        # ``chain`` is the raw list from the file; ``resolved_chain`` is the
        # one with provider pins applied, and that is what the runner uses.
        assert config.stage("conv_simulator").chain == ("gemini/gemini-2.5-flash-lite",)
        assert config.resolved_chain("conv_simulator") == ("gemini/gemini-2.5-flash",)
        assert config.resolved_chain("article_gen") == ("gemini/gemini-2.5-flash",)

    def test_the_chain_keeps_the_fallback_order(self, write_config) -> None:
        text = VALID_CONFIG.replace(
            '[stages.conv_simulator]\nmodel = "gemini/gemini-2.5-flash-lite"',
            "[stages.conv_simulator]\n"
            'model = "gemini/gemini-2.5-flash-lite"\n'
            'fallbacks = ["groq/qwen/qwen3.8-27b", "mistral/ministral-3b-latest"]',
        )
        config = load_config(write_config(text))
        assert config.resolved_chain("conv_simulator") == (
            "gemini/gemini-2.5-flash-lite",
            "groq/qwen/qwen3.8-27b",
            "mistral/ministral-3b-latest",
        )

    def test_a_pin_for_an_unknown_provider_is_rejected(self, write_config) -> None:
        text = VALID_CONFIG + '\n[providers.nosuchprovider]\nmodel = "x/1"\n'
        with pytest.raises(ConfigError):
            load_config(write_config(text))


class TestDescribe:
    """The human-readable summary shown by the CLI."""

    def test_it_lists_every_stage(self, write_config) -> None:
        config = load_config(write_config(VALID_CONFIG))
        summary = describe(config)
        for stage in STAGES:
            assert stage in summary

    def test_it_lists_the_defaults(self, write_config) -> None:
        config = load_config(write_config(VALID_CONFIG))
        assert "max_conv_turn" in describe(config)


class TestRetrieverRegistry:
    """The retrievers the CLI offers."""

    def test_duckduckgo_is_offered_and_needs_no_key(self) -> None:
        assert "duckduckgo" in RETRIEVERS
        assert "no API key" in RETRIEVERS["duckduckgo"]
