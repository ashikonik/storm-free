"""Tests for the per-run log.

A run against free-tier providers takes minutes and often fails partway
through, so the log is the record of what actually happened: which providers
were tried, which ones failed and why.  These tests check that the log is
written where it is expected, that it captures the provider decisions, and
that a failed run still leaves a usable record.

Run with:  ./venv/bin/python -m pytest tests/test_run_log.py -v
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from free_setup.run_log import (
    RunLog,
    attach_log_capture,
    build_run_log,
    default_log_path,
    detach_log_capture,
    slugify,
)


class TestSlugify:
    """Turning a topic into a filename."""

    def test_spaces_become_dashes(self) -> None:
        assert slugify("history of the printing press") == (
            "history-of-the-printing-press"
        )

    def test_punctuation_is_removed(self) -> None:
        assert slugify("What's new? (2026)") == "what-s-new-2026"

    def test_an_empty_topic_still_yields_a_name(self) -> None:
        assert slugify("   ") == "run"

    def test_a_very_long_topic_is_truncated(self) -> None:
        assert len(slugify("word " * 100)) <= 80


class TestDefaultLogPath:
    """Where a log goes when no path is given."""

    def test_it_is_dated_and_named_after_the_topic(self, tmp_path: Path) -> None:
        path = default_log_path("history of the printing press", base_dir=tmp_path)
        assert path.parent == tmp_path
        assert "history-of-the-printing-press" in path.name

    def test_it_ends_in_a_log_file(self, tmp_path: Path) -> None:
        path = default_log_path("topic", base_dir=tmp_path)
        assert path.suffix == ".log"


class TestRunLog:
    """Writing and reading back the log."""

    def test_it_creates_its_directory(self, tmp_path: Path) -> None:
        target = tmp_path / "nested" / "dir" / "run.log"
        with RunLog(target, topic="t"):
            pass
        assert target.exists()

    def test_it_records_the_topic(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="printing press"):
            pass
        assert "printing press" in target.read_text()

    def test_it_records_the_start_and_the_end(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="t"):
            pass
        text = target.read_text()
        assert "run started" in text
        assert "run finished" in text

    def test_it_records_the_elapsed_time(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="t"):
            pass
        assert "s" in target.read_text().splitlines()[-1]

    def test_lines_are_timestamped(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="t") as log:
            log.write("something happened")
        # Every line opens with an ``[HH:MM:SS]`` stamp.  The shape is
        # checked rather than a literal hour, because the hour depends on
        # when the suite happens to run.
        lines = target.read_text().splitlines()
        assert lines
        for line in lines:
            assert re.fullmatch(r"\[\d{2}:\d{2}:\d{2}\] .*", line), line

    def test_a_block_is_indented(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="t") as log:
            log.write_block("chains", "a -> b")
        text = target.read_text()
        assert "--- chains ---" in text
        assert "  a -> b" in text

    def test_writing_after_close_is_harmless(self, tmp_path: Path) -> None:
        """A late write must not raise."""
        target = tmp_path / "run.log"
        log = RunLog(target, topic="t").open()
        log.close()
        log.write("too late")
        log.close()

    def test_it_appends_rather_than_truncating(self, tmp_path: Path) -> None:
        """Two runs to the same path must both be visible."""
        target = tmp_path / "run.log"
        with RunLog(target, topic="first"):
            pass
        with RunLog(target, topic="second"):
            pass
        text = target.read_text()
        assert "first" in text
        assert "second" in text


class TestLogCapture:
    """Capturing the fallback chain's decisions."""

    def test_it_captures_our_loggers(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="t") as log:
            handler = attach_log_capture(log)
            try:
                logging.getLogger("free_setup.fallback").warning(
                    "Provider X failed; trying the next provider."
                )
            finally:
                detach_log_capture(handler)
        assert "trying the next provider" in target.read_text()

    def test_it_ignores_third_party_loggers(self, tmp_path: Path) -> None:
        """LiteLLM is far too chatty to be useful in a run log."""
        target = tmp_path / "run.log"
        with RunLog(target, topic="t") as log:
            handler = attach_log_capture(log)
            try:
                logging.getLogger("litellm").warning("completion() model= gemini")
            finally:
                detach_log_capture(handler)
        assert "completion()" not in target.read_text()

    def test_detaching_stops_the_capture(self, tmp_path: Path) -> None:
        target = tmp_path / "run.log"
        with RunLog(target, topic="t") as log:
            handler = attach_log_capture(log)
            detach_log_capture(handler)
            logging.getLogger("free_setup.fallback").warning("after detach")
        assert "after detach" not in target.read_text()


class TestBuildRunLog:
    """Creating the log for a run."""

    def test_an_explicit_path_is_used(self, tmp_path: Path) -> None:
        target = tmp_path / "explicit.log"
        log = build_run_log("topic", path=target)
        assert log.path == target

    def test_the_base_directory_is_used_when_no_path_is_given(
        self, tmp_path: Path
    ) -> None:
        log = build_run_log("topic", base_dir=tmp_path)
        assert log.path.parent == tmp_path

    def test_the_topic_is_carried(self, tmp_path: Path) -> None:
        log = build_run_log("my topic", path=tmp_path / "x.log")
        assert log.topic == "my topic"


@pytest.mark.parametrize(
    "message,expected",
    [
        ("top_p must be 1 when using greedy sampling.", True),
        ("invalid_request_greedy_sampling", True),
        ("Invalid API key provided.", False),
        ("Rate limit reached for model", False),
        ("model not found", False),
    ],
)
class TestGreedySamplingDetection:
    """Mistral's intermittent refusal of the sampling parameters."""

    def test_it_recognises_the_error(self, message: str, expected: bool) -> None:
        from free_setup.fallback import _is_greedy_sampling_error

        assert _is_greedy_sampling_error(Exception(message)) is expected
