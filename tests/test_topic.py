"""Tests for the topic validation.

The topic is user input that upstream turns into a directory name, so it
has to be checked before it is used.  These tests cover the escape that
was possible before the check existed.

Run with:  ./venv/bin/python -m pytest tests/test_topic.py -v
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from free_setup.topic import InvalidTopicError, validate_topic

PROJECT_ROOT = Path(__file__).resolve().parent.parent


# -- ordinary topics ---------------------------------------------------------


@pytest.mark.parametrize(
    "topic",
    [
        "history of the printing press",
        "The Beatles",
        "quantum computing",
        "e=mc²",
        "a/b",
        "..foo",
        "foo..",
        ".hidden",
        "C:\\Windows",
        " . ",
    ],
)
def test_ordinary_topics_are_accepted(topic: str) -> None:
    """A topic that cannot escape is returned unchanged."""
    assert validate_topic(topic) == topic


def test_a_topic_may_be_about_a_dot() -> None:
    """``..`` alone is the problem, not dots in general."""
    assert validate_topic("The rise of ...") == "The rise of ..."


# -- escaping ----------------------------------------------------------------


@pytest.mark.parametrize("topic", ["..", ".", "...", "...."])
def test_a_topic_that_is_only_dots_is_rejected(topic: str) -> None:
    """A name made only of dots resolves to the output directory's parent.

    Upstream replaces ``/`` with ``_`` and joins the result onto the
    output directory, so ``..`` puts the article outside ``outputs/``.
    """
    with pytest.raises(InvalidTopicError):
        validate_topic(topic)


def test_an_empty_topic_is_rejected() -> None:
    with pytest.raises(InvalidTopicError):
        validate_topic("   ")


def test_the_escape_is_actually_an_escape(tmp_path: Path) -> None:
    """The check rejects what would really have escaped.

    This is the test that would have caught the bug: it builds the path
    the way upstream does and asks whether it lands inside the output
    directory.
    """
    output_dir = tmp_path / "outputs"
    output_dir.mkdir()

    def upstream_path(topic: str) -> Path:
        """Build the path the way upstream's engine does."""
        name = topic.replace(" ", "_").replace("/", "_")
        return output_dir / name

    assert not os.path.realpath(upstream_path("..")).startswith(
        str(os.path.realpath(output_dir))
    ), "the premise of this test is that '..' escapes"

    with pytest.raises(InvalidTopicError):
        validate_topic("..")


# -- the CLI -----------------------------------------------------------------


def test_the_cli_rejects_an_escaping_topic() -> None:
    """The CLI must refuse the topic, not write outside ``outputs/``."""
    import subprocess
    import sys

    result = subprocess.run(
        [sys.executable, str(PROJECT_ROOT / "run_storm_free.py"), "..", "--dry-run"],
        capture_output=True,
        text=True,
        timeout=120,
        cwd=str(PROJECT_ROOT),
    )

    assert result.returncode == 2
    combined = result.stdout + result.stderr
    assert "Traceback" not in combined
    assert ".." in combined
