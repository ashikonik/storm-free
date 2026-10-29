"""Append-only per-run logs.

A run against free-tier providers takes minutes and may fail partway
through, so the useful record is not the article alone but what happened on
the way to it: which providers were tried, which ones failed and why, how
long each stage took, and where the article ended up.

This module captures that.  It writes a log under ``logs/runs/`` by default,
or to a path the caller names, and it keeps a running log of the provider
fallback decisions so the chain's behaviour is visible after the fact.

The log directory is gitignored: the durable record of what was achieved
lives in the committed ``STATUS.md``, while these files are the per-run
detail that would otherwise drown the repository.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from pathlib import Path
from typing import TextIO

logger = logging.getLogger(__name__)

# Where per-run logs are written when the caller does not name a path.
DEFAULT_LOG_DIR = Path("logs") / "runs"

# A topic slug safe to use as a filename.
_UNSAFE = re.compile(r"[^a-z0-9]+")


def slugify(text: str) -> str:
    """Turn a topic into a filesystem-safe slug."""
    slug = _UNSAFE.sub("-", text.strip().lower()).strip("-")
    return slug[:80] or "run"


def default_log_path(topic: str, *, base_dir: Path | None = None) -> Path:
    """Return the log path for a run about ``topic``.

    The name carries the date and the topic so that several runs on the same
    day stay distinguishable and sort chronologically.
    """
    directory = base_dir or DEFAULT_LOG_DIR
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return directory / f"{stamp}-{slugify(topic)}.log"


class RunLog:
    """A per-run log file.

    The log records the command that was run, the resolved provider chains,
    each fallback decision, and the outcome.  It is written as the run
    proceeds, so a run that is interrupted still leaves a usable record.
    """

    def __init__(self, path: Path, *, topic: str = "") -> None:
        self.path = Path(path)
        self.topic = topic
        self._handle: TextIO | None = None
        self._started = datetime.now()

    # -- lifecycle ---------------------------------------------------------

    def open(self) -> RunLog:
        """Create the log file and write its header."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._handle = self.path.open("a", encoding="utf-8")
        self._write(f"run started: {self._started.isoformat(timespec='seconds')}")
        if self.topic:
            self._write(f"topic: {self.topic}")
        return self

    def close(self) -> None:
        """Write the closing line and close the file."""
        if self._handle is None:
            return
        elapsed = (datetime.now() - self._started).total_seconds()
        self._write(f"run finished after {elapsed:.1f}s")
        self._handle.close()
        self._handle = None

    def __enter__(self) -> RunLog:
        return self.open()

    def __exit__(self, *exc_info: object) -> None:
        self.close()

    # -- writing -----------------------------------------------------------

    def _write(self, message: str) -> None:
        if self._handle is None:
            return
        stamp = datetime.now().strftime("%H:%M:%S")
        self._handle.write(f"[{stamp}] {message}\n")
        self._handle.flush()

    def write(self, message: str) -> None:
        """Write a line to the log."""
        self._write(message)

    def write_block(self, title: str, body: str) -> None:
        """Write a titled, multi-line block."""
        self._write(f"--- {title} ---")
        for line in body.splitlines():
            self._write(f"  {line}")

    def record_provider_event(self, message: str) -> None:
        """Record a provider fallback or failure decision.

        The fallback chain logs these to its own logger; capturing them here
        means a run's log shows exactly which provider ended up serving each
        request.
        """
        self._write(f"provider: {message}")

    def record_summary(self, summary: str) -> None:
        """Record the end-of-run token usage and timing summary."""
        self.write_block("summary", summary)


class _LogCaptureHandler(logging.Handler):
    """Forward log records into a :class:`RunLog`.

    Only records from the fallback and storm-free loggers are captured: the
    third-party libraries are far too chatty to be useful in a run log.
    """

    _INTERESTING = ("free_setup", "storm_free")

    def __init__(self, run_log: RunLog) -> None:
        super().__init__()
        self.run_log = run_log

    def emit(self, record: logging.LogRecord) -> None:
        """Write a record to the run log."""
        try:
            message = record.getMessage()
        except Exception:  # pragma: no cover - defensive
            return
        if any(record.name.startswith(prefix) for prefix in self._INTERESTING):
            self.run_log.record_provider_event(f"{record.levelname}: {message}")


def attach_log_capture(run_log: RunLog) -> _LogCaptureHandler:
    """Attach a handler that copies our loggers' output into ``run_log``.

    Returns:
        The handler, so the caller can detach it again.
    """
    handler = _LogCaptureHandler(run_log)
    handler.setLevel(logging.INFO)
    for name in ("free_setup", "free_setup.fallback", "storm_free"):
        logging.getLogger(name).addHandler(handler)
    return handler


def detach_log_capture(handler: logging.Handler) -> None:
    """Remove a handler previously attached by :func:`attach_log_capture`."""
    for name in ("free_setup", "free_setup.fallback", "storm_free"):
        logging.getLogger(name).removeHandler(handler)


def build_run_log(
    topic: str,
    *,
    path: str | Path | None = None,
    base_dir: Path | None = None,
) -> RunLog:
    """Create the run log for a topic.

    Args:
        topic: The topic being researched.
        path: An explicit path to write to.  When omitted, a dated path under
            ``logs/runs/`` is used.
        base_dir: The directory to write into when ``path`` is omitted.

    Returns:
        An unopened :class:`RunLog`.
    """
    target = Path(path) if path else default_log_path(topic, base_dir=base_dir)
    return RunLog(target, topic=topic)


__all__: list[str] = [
    "DEFAULT_LOG_DIR",
    "RunLog",
    "attach_log_capture",
    "build_run_log",
    "default_log_path",
    "detach_log_capture",
    "slugify",
]
