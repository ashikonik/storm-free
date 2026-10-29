"""Tests for the terminal progress output.

Progress lines are the only feedback a user gets during a run that may take
many minutes against rate-limited providers, so the handler has to actually
say something at each stage boundary.  These tests check that every hook
emits a line, that the elapsed-time prefix is present, and that verbose-only
output stays quiet unless asked for.

Run with:  ./venv/bin/python -m pytest tests/test_progress.py -v
"""

from __future__ import annotations

import io

import pytest

from free_setup.progress import CLICallbackHandler


@pytest.fixture
def stream() -> io.StringIO:
    """A buffer to capture the handler's output."""
    return io.StringIO()


@pytest.fixture
def handler(stream: io.StringIO) -> CLICallbackHandler:
    """A handler writing into the capture buffer."""
    return CLICallbackHandler(stream=stream)


class TestPipelineHooks:
    """Each stage of the pipeline announces itself."""

    def test_perspective_identification_is_announced(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_identify_perspective_start()
        assert "perspective" in stream.getvalue().lower()

    def test_the_perspectives_found_are_listed(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_identify_perspective_end(perspectives=["A", "B"])
        output = stream.getvalue()
        assert "2" in output
        assert "A" in output

    def test_information_gathering_is_announced(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_information_gathering_start()
        assert "information" in stream.getvalue().lower()

    def test_each_dialogue_turn_is_counted(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_dialogue_turn_end(dlg_turn=None)
        handler.on_dialogue_turn_end(dlg_turn=None)
        assert "turn 2" in stream.getvalue()

    def test_a_dialogue_question_is_previewed(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        class _Turn:
            question = "What was the printing press?"

        handler.on_dialogue_turn_end(dlg_turn=_Turn())
        assert "printing press" in stream.getvalue()

    def test_organisation_is_announced(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_information_organization_start()
        assert "outline" in stream.getvalue().lower()

    def test_the_refined_outline_is_announced(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_outline_refinement_end(outline="# Title\n## Section\n")
        assert "outline" in stream.getvalue().lower()

    def test_information_gathering_end_is_announced(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_information_gathering_end()
        assert "complete" in stream.getvalue().lower()


class TestFormatting:
    """The shape of the emitted lines."""

    def test_lines_carry_an_elapsed_time_prefix(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_identify_perspective_start()
        assert stream.getvalue().startswith("[")

    def test_lines_end_with_a_newline(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_identify_perspective_start()
        assert stream.getvalue().endswith("\n")


class TestVerbose:
    """Verbose-only output."""

    def test_verbose_output_is_suppressed_by_default(
        self, handler: CLICallbackHandler, stream: io.StringIO
    ) -> None:
        handler.on_direct_outline_generation_end(outline="# Title")
        assert stream.getvalue() == ""

    def test_verbose_output_appears_when_enabled(self, stream: io.StringIO) -> None:
        handler = CLICallbackHandler(stream=stream, verbose=True)
        handler.on_direct_outline_generation_end(outline="# Title")
        assert "characters" in stream.getvalue()
