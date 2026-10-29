"""Tests for loading API keys from ``secrets.toml``.

The behaviour that matters here is what does *not* happen: an empty
placeholder in the template must not erase a key that is already exported in
the shell.  Getting that wrong turns a working setup into a "no key" one
without any error message.

Run with:  ./venv/bin/python -m pytest tests/test_secrets.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from free_setup.secrets import load_secrets


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """Start each test with no provider keys in the environment."""
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(var, raising=False)


def test_a_missing_file_is_not_an_error(tmp_path: Path) -> None:
    assert load_secrets(tmp_path / "nope.toml") == 0


def test_a_key_is_loaded(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('GROQ_API_KEY = "gsk-real-key"\n')

    assert load_secrets(secrets) == 1
    assert os.environ["GROQ_API_KEY"] == "gsk-real-key"


def test_an_empty_placeholder_does_not_shadow_a_shell_key(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The bug this module exists to prevent.

    The shipped template has ``GROQ_API_KEY = ""`` for every provider.  If
    that empty value were written to the environment, a key already exported
    in the shell would be wiped and the provider would look unconfigured.
    """
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-the-shell")

    secrets = tmp_path / "secrets.toml"
    secrets.write_text('GROQ_API_KEY = ""\n')

    assert load_secrets(secrets) == 0
    assert os.environ["GROQ_API_KEY"] == "gsk-from-the-shell"


def test_an_existing_shell_key_is_not_overridden(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("GROQ_API_KEY", "gsk-from-the-shell")

    secrets = tmp_path / "secrets.toml"
    secrets.write_text('GROQ_API_KEY = "gsk-from-the-file"\n')

    assert load_secrets(secrets) == 0
    assert os.environ["GROQ_API_KEY"] == "gsk-from-the-shell"


def test_a_file_key_fills_a_variable_the_shell_did_not_set(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('MISTRAL_API_KEY = "sk-mistral"\n')

    assert load_secrets(secrets) == 1
    assert os.environ["MISTRAL_API_KEY"] == "sk-mistral"


def test_whitespace_only_values_are_treated_as_empty(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text('GROQ_API_KEY = "   "\n')

    assert load_secrets(secrets) == 0
    assert "GROQ_API_KEY" not in os.environ


def test_a_malformed_file_is_reported_not_raised(tmp_path: Path) -> None:
    secrets = tmp_path / "secrets.toml"
    secrets.write_text("this is not = = valid toml\n")

    assert load_secrets(secrets) == 0
