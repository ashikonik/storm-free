"""Keep the README in step with the command line.

A flag that is not documented is a flag nobody uses, and a flag that is
documented but does not exist is worse.  These tests check the README
against the parser itself, so a rename cannot silently rot the docs.

Run with:  ./venv/bin/python -m pytest tests/test_docs.py -v
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
README = PROJECT_ROOT / "README.md"

from run_storm_free import build_parser


def _flag_names() -> list[str]:
    """Return the option strings of every argument the parser defines."""
    names: list[str] = []
    for action in build_parser()._actions:
        for option in action.option_strings:
            if option.startswith("--"):
                names.append(option)
    return sorted(names)


def _readme_text() -> str:
    return README.read_text(encoding="utf-8")


@pytest.mark.parametrize("flag", _flag_names())
def test_every_flag_is_documented(flag: str) -> None:
    """Every option the CLI accepts must appear in the README."""
    assert flag in _readme_text(), f"{flag} is not documented in README.md"


def test_the_readme_documents_every_flag() -> None:
    """The README must not list a flag the CLI does not accept.

    This catches the other direction: a flag that was renamed or removed
    but left behind in the documentation.
    """
    # Flags mentioned in a fenced code block or a table row.
    mentioned = set(re.findall(r"--[a-z][a-z0-9-]+", _readme_text()))

    # Flags that belong to other tools, not to this CLI.
    foreign = {
        "--no-verify",
        "--depth",
        "--python",
        "--index-strategy",
        "--extra-index-url",
        "--all",
        "--error",
        "--limit",
        "--workflow",
        "--repo",
        "--check",  # ruff format --check
    }
    mentioned -= foreign

    unknown = sorted(mentioned - set(_flag_names()))
    assert not unknown, f"README mentions flags the CLI does not accept: {unknown}"


def test_the_readme_documents_the_providers() -> None:
    """Every provider advertised as free must appear in the README table.

    The check is on the environment variable, which the README table and
    the registry both name exactly, rather than on the provider's display
    name, which the README writes in prose.
    """
    from free_setup.providers import PROVIDERS

    text = _readme_text()
    missing = []
    for name in _ADVERTISED:
        spec = PROVIDERS.get(name)
        if spec is None:
            missing.append(f"{name} (not in the registry)")
            continue
        # The first entry is the canonical variable; the rest are aliases
        # the provider has also used, and the README need not list them.
        env_vars = spec.env_vars
        if not env_vars:
            missing.append(f"{name} (no environment variable)")
            continue
        if env_vars[0] not in text:
            missing.append(f"{name} ({env_vars[0]})")
    assert not missing, f"providers missing from the README: {missing}"


# The README advertises these as free and needing no credit card.
_ADVERTISED = {
    "groq",
    "gemini",
    "mistral",
    "nvidia_nim",
    "cohere",
    "ai21",
    "galadriel",
    "nebius",
    "deepinfra",
    "huggingface",
    "ovhcloud",
    "meta_llama",
    "fireworks_ai",
    "hyperbolic",
    "novita",
    "deepseek",
    "perplexity",
    "xai",
}
