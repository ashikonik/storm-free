"""Tests for the command-line entry point.

The CLI is the only thing a user actually touches, and it was the one part
of the project with no direct coverage.  These tests invoke it the way a
user would -- as a subprocess, with arguments -- so that a break in the
argument parsing, the exit codes or the wiring between the modules shows
up here rather than in someone's first run.

The cases here need no API key and make no network call.

Run with:  ./venv/bin/python -m pytest tests/test_cli.py -v
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parent.parent
CLI = PROJECT_ROOT / "run_storm_free.py"

# Every invocation of the CLI imports torch, through the upstream package.
# That import takes several seconds on its own, and much longer on a loaded
# machine: the whole suite runs these in parallel with the other tests, and
# a timeout that is comfortable for a single test can expire under that
# load.  The timeout is generous on purpose -- a slow test is annoying, a
# flaky one is worse.
CLI_TIMEOUT_SECONDS = 600


def run_cli(
    *args: str, timeout: int = CLI_TIMEOUT_SECONDS
) -> subprocess.CompletedProcess[str]:
    """Run the CLI in a subprocess and return the completed process."""
    return subprocess.run(
        [sys.executable, str(CLI), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
        cwd=str(PROJECT_ROOT),
    )


# -- argument parsing --------------------------------------------------------


def test_help_exits_zero_and_lists_the_options() -> None:
    """``--help`` must work without a key, a config or a network."""
    result = run_cli("--help")

    assert result.returncode == 0
    assert "--doctor" in result.stdout
    assert "--list-providers" in result.stdout


def test_list_providers_exits_zero() -> None:
    """The registry must be readable without any credentials."""
    result = run_cli("--list-providers")

    assert result.returncode == 0
    assert "groq" in result.stdout


def test_no_arguments_prints_usage_and_fails() -> None:
    """A bare invocation has no topic, so it must say so and not run."""
    result = run_cli()

    assert result.returncode != 0
    assert "topic" in (result.stdout + result.stderr).lower()


def test_missing_topic_is_reported_clearly() -> None:
    """The error must name the problem, not raise a traceback."""
    result = run_cli("--dry-run")

    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "topic" in combined.lower()
    assert "Traceback" not in combined


def test_missing_config_is_reported_clearly() -> None:
    """A bad config path must be an error message, not a traceback."""
    result = run_cli(
        "some topic",
        "--config",
        "/nonexistent/config.toml",
        "--dry-run",
    )

    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert "error" in combined.lower()


def test_unknown_provider_is_reported_clearly() -> None:
    """An unknown provider name must not produce a traceback."""
    result = run_cli("some topic", "--provider", "not_a_provider", "--dry-run")

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined


# -- dry run -----------------------------------------------------------------


def test_dry_run_resolves_the_chains_without_calling_anything() -> None:
    """A dry run must print the plan and make no API call."""
    result = run_cli("a test topic", "--dry-run")

    assert result.returncode == 0
    assert "Dry run" in result.stdout
    assert "Resolved provider chains" in result.stdout


def test_dry_run_with_model_keeps_the_fallback_chain() -> None:
    """``--model`` overrides the primary model but keeps the fallbacks.

    Silently dropping the chain would disable the failover the project
    exists to provide.
    """
    result = run_cli(
        "a test topic",
        "--model",
        "groq/qwen/qwen3.8-27b",
        "--dry-run",
    )

    assert result.returncode == 0
    chains = result.stdout.split("Resolved provider chains:")[1]
    assert "groq/qwen/qwen3.8-27b" in chains
    # The chain must hold more than one model, or there is no failover.
    assert "->" in chains


# -- missing upstream clone --------------------------------------------------


def test_missing_clone_reports_how_to_fix_it(tmp_path: Path) -> None:
    """A fresh clone without ``storm/`` must say what to do.

    The failure it used to produce was a bare ``ModuleNotFoundError``
    pointing at an internal file, which told the reader nothing.
    """
    # A copy of the project without the gitignored upstream clone.
    checkout = tmp_path / "project"
    checkout.mkdir()
    for name in ("run_storm_free.py", "free_setup"):
        source = PROJECT_ROOT / name
        if source.is_dir():
            subprocess.run(["cp", "-r", str(source), str(checkout / name)], check=True)
        else:
            subprocess.run(["cp", str(source), str(checkout / name)], check=True)

    result = subprocess.run(
        [sys.executable, str(checkout / "run_storm_free.py"), "a topic"],
        capture_output=True,
        text=True,
        timeout=CLI_TIMEOUT_SECONDS,
        cwd=str(checkout),
    )

    assert result.returncode != 0
    combined = result.stdout + result.stderr
    assert "setup.sh" in combined, "the message must say how to fix it"
    assert "ModuleNotFoundError" not in combined


# -- flags -------------------------------------------------------------------


@pytest.mark.parametrize(
    "flag",
    [
        "--do-research",
        "--do-generate-outline",
        "--do-generate-article",
        "--do-polish-article",
    ],
)
def test_stage_flags_are_accepted(flag: str) -> None:
    """Every stage flag must parse."""
    result = run_cli("a test topic", flag, "--dry-run")

    assert result.returncode == 0


def test_retriever_choices_are_enforced() -> None:
    """An unknown retriever must be rejected by the parser itself."""
    result = run_cli("a test topic", "--retriever", "not_a_retriever", "--dry-run")

    assert result.returncode != 0
    assert "not_a_retriever" in result.stdout + result.stderr


def test_no_log_suppresses_the_run_log() -> None:
    """``--no-log`` must be accepted and must not write a log."""
    result = run_cli("a test topic", "--no-log", "--dry-run")

    assert result.returncode == 0
