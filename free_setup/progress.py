"""Terminal progress output for the STORM pipeline.

The upstream ``BaseCallbackHandler`` exposes hooks at each interesting point
in the pipeline.  This subclass turns them into timestamped lines on stderr
so the user can see what the run is doing, which matters most when a run is
slow because it is rate-limited and retrying.
"""

from __future__ import annotations

import sys
import time
from typing import Any, TextIO

from knowledge_storm.storm_wiki.modules.callback import BaseCallbackHandler


class CLICallbackHandler(BaseCallbackHandler):
    """Print pipeline progress to a stream (stderr by default)."""

    def __init__(self, stream: TextIO | None = None, *, verbose: bool = False) -> None:
        self.stream = stream if stream is not None else sys.stderr
        self.verbose = verbose
        self._start = time.monotonic()
        self._turn = 0

    def _emit(self, message: str) -> None:
        """Write a timestamped progress line."""
        elapsed = time.monotonic() - self._start
        print(f"[{elapsed:7.1f}s] {message}", file=self.stream, flush=True)

    def _emit_verbose(self, message: str) -> None:
        """Write a line only when verbose mode is on."""
        if self.verbose:
            self._emit(message)

    # -- pipeline hooks ------------------------------------------------

    def on_identify_perspective_start(self, **kwargs: Any) -> None:
        self._emit("Identifying perspectives for the topic...")

    def on_identify_perspective_end(
        self, perspectives: list[str], **kwargs: Any
    ) -> None:
        count = len(perspectives or [])
        self._emit(f"Found {count} perspective(s): {', '.join(perspectives or [])}")

    def on_information_gathering_start(self, **kwargs: Any) -> None:
        self._emit("Gathering information (simulated expert conversation)...")

    def on_dialogue_turn_end(self, dlg_turn: Any = None, **kwargs: Any) -> None:
        self._turn += 1
        question = ""
        if dlg_turn is not None:
            question = str(getattr(dlg_turn, "question", "") or "")
        preview = question.replace("\n", " ")[:90]
        self._emit(f"  dialogue turn {self._turn}: {preview}")

    def on_information_gathering_end(self, **kwargs: Any) -> None:
        self._emit("Information gathering complete.")

    def on_information_organization_start(self, **kwargs: Any) -> None:
        self._emit("Organising the collected information into an outline...")

    def on_direct_outline_generation_end(
        self, outline: str = "", **kwargs: Any
    ) -> None:
        self._emit_verbose(f"  direct outline: {len(outline or '')} characters")

    def on_outline_refinement_end(self, outline: str = "", **kwargs: Any) -> None:
        sections = (outline or "").count("#")
        self._emit(f"Outline refined ({sections} heading marker(s)).")


__all__ = ["CLICallbackHandler"]
