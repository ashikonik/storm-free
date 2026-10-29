"""Diagnostics for the free-tier setup.

``doctor`` answers three questions:

1. Which providers have a usable API key?
2. Do those providers actually answer a request right now?
3. Does web retrieval work?

The retriever check matters as much as the LLM check: STORM's whole value is
grounding its output in retrieved sources, so a working model with a broken
retriever still produces an unusable article.
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from dataclasses import dataclass
from enum import Enum
from typing import Any

logger = logging.getLogger(__name__)


class Status(str, Enum):  # noqa: UP042 - StrEnum needs 3.11+; keep 3.10 compat
    """Outcome of a single probe."""

    OK = "ok"
    NO_KEY = "no_key"
    AUTH_ERROR = "auth_error"
    RATE_LIMITED = "rate_limited"
    QUOTA_EXHAUSTED = "quota_exhausted"
    UNREACHABLE = "unreachable"
    ERROR = "error"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class ProbeResult:
    """The outcome of probing one provider."""

    name: str
    model: str
    status: Status
    detail: str = ""
    latency_ms: int | None = None

    @property
    def usable(self) -> bool:
        """Whether this provider can serve requests."""
        return self.status is Status.OK

    def symbol(self) -> str:
        """A short glyph for terminal output."""
        return {
            Status.OK: "[ok]",
            Status.NO_KEY: "[--]",
            Status.AUTH_ERROR: "[!!]",
            Status.RATE_LIMITED: "[~~]",
            Status.QUOTA_EXHAUSTED: "[~~]",
            Status.UNREACHABLE: "[!!]",
            Status.ERROR: "[!!]",
            Status.SKIPPED: "[--]",
        }[self.status]


def _status_from_exception(exc: BaseException) -> tuple[Status, str]:
    """Map an exception onto a probe status.

    The mapping is finer-grained here than in the fallback chain: the doctor
    runs precisely when something is not working, so it needs to say *which*
    thing is wrong rather than lumping everything transient into one bucket.
    """
    from .fallback import (
        _is_daily_quota_error,
        _is_rate_limit_error,
        classify_error,
    )

    verdict = classify_error(exc)
    name = type(exc).__name__

    if verdict == "fatal":
        return Status.AUTH_ERROR, f"{name}: {exc}"

    # The daily-quota check runs before the retry/unknown split: a provider
    # may report the exhausted budget through an exception type that carries
    # no status code, and the message is the only reliable signal.
    if _is_daily_quota_error(exc):
        return Status.QUOTA_EXHAUSTED, f"{name}: daily quota exhausted"

    if verdict == "retry":
        if isinstance(exc, (ConnectionError, TimeoutError, OSError)):
            return Status.UNREACHABLE, f"{name}: {exc}"
        return Status.RATE_LIMITED, f"{name}: {exc}"

    # A bare exception with no status code can still be a rate limit.
    if _is_rate_limit_error(exc):
        return Status.RATE_LIMITED, f"{name}: {exc}"

    status_code = getattr(exc, "status_code", None)
    if status_code in (401, 403):
        return Status.AUTH_ERROR, f"HTTP {status_code}: {exc}"
    return Status.UNREACHABLE, f"{name}: {exc}"


def probe_provider(spec: Any, model: str | None = None) -> ProbeResult:
    """Send a minimal request to a provider and report what happened.

    The probe asks for a one-word reply with a small token budget, so it
    costs essentially nothing while still confirming that the key is valid
    and the model id is correct.
    """
    import time

    model_id = model or spec.default_model
    if spec.requires_key and not spec.has_key():
        return ProbeResult(
            name=spec.name,
            model=model_id,
            status=Status.NO_KEY,
            detail=f"set {spec.env_vars[0]} to enable",
        )

    from .runner import build_lm

    # A token or two is enough to prove the key works, but too few and the
    # model has no room to emit even a single word, which would look like an
    # empty response rather than a success.  Reasoning models need more:
    # Gemini 3 spends part of the budget thinking and answers with nothing
    # at 16 tokens, so the floor is set above that.
    probe_tokens = 64

    try:
        lm = build_lm(spec, model_id, max_tokens=probe_tokens)
    except Exception as exc:
        return ProbeResult(
            name=spec.name,
            model=model_id,
            status=Status.ERROR,
            detail=f"could not build client: {exc}",
        )

    # Upstream's ``LitellmModel`` defaults to ``cache=True``, which reads a
    # disk cache under ``~/.storm_local_cache``.  A probe that hits the cache
    # reports "ok" even when the provider's quota is exhausted, which is
    # exactly the situation the user needs to be told about.  Bypass it.
    if hasattr(lm, "cache"):
        lm.cache = False

    start = time.monotonic()
    try:
        outputs = lm("Reply with the single word: ok", max_tokens=probe_tokens)
    except Exception as exc:
        status, detail = _status_from_exception(exc)
        return ProbeResult(
            name=spec.name,
            model=model_id,
            status=status,
            detail=detail,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    latency = int((time.monotonic() - start) * 1000)
    preview = ""
    if outputs:
        first = outputs[0]
        preview = (first or "").strip().replace("\n", " ")[:40]
    return ProbeResult(
        name=spec.name,
        model=model_id,
        status=Status.OK,
        detail=preview or "(empty response)",
        latency_ms=latency,
    )


def probe_retriever(name: str = "duckduckgo") -> ProbeResult:
    """Run one real retrieval to confirm web search works end to end."""
    import time

    from .runner import build_retriever

    start = time.monotonic()
    try:
        rm = build_retriever(name, k=1)
        results = rm("who invented the printing press")
    except Exception as exc:
        status, detail = _status_from_exception(exc)
        return ProbeResult(
            name=f"retriever:{name}",
            model="-",
            status=status,
            detail=detail,
            latency_ms=int((time.monotonic() - start) * 1000),
        )

    latency = int((time.monotonic() - start) * 1000)
    count = len(results) if results is not None else 0
    if count == 0:
        return ProbeResult(
            name=f"retriever:{name}",
            model="-",
            status=Status.ERROR,
            detail="query returned no results",
            latency_ms=latency,
        )
    first_url = ""
    if isinstance(results[0], dict):
        first_url = str(results[0].get("url", ""))
    return ProbeResult(
        name=f"retriever:{name}",
        model="-",
        status=Status.OK,
        detail=f"{count} result(s): {first_url[:60]}",
        latency_ms=latency,
    )


def run_doctor(
    specs: Sequence[Any],
    retriever: str = "duckduckgo",
    *,
    probe_retrieval: bool = True,
) -> list[ProbeResult]:
    """Probe every provider plus the retriever."""
    results: list[ProbeResult] = []
    for spec in specs:
        results.append(probe_provider(spec))
    if probe_retrieval:
        results.append(probe_retriever(retriever))
    return results


def render_results(results: Sequence[ProbeResult]) -> str:
    """Render probe results as a table for the terminal."""
    if not results:
        return "No providers to probe."

    name_width = max(len(r.name) for r in results)
    model_width = min(38, max(len(r.model) for r in results))

    lines = []
    for result in results:
        model = result.model
        if len(model) > model_width:
            model = model[: model_width - 1] + "…"
        latency = (
            f"{result.latency_ms:>6}ms" if result.latency_ms is not None else "     -"
        )
        lines.append(
            f"  {result.symbol()} {result.name:<{name_width}}  "
            f"{model:<{model_width}}  {latency}  {result.detail}"
        )
    return "\n".join(lines)


__all__ = [
    "ProbeResult",
    "Status",
    "probe_provider",
    "probe_retriever",
    "render_results",
    "run_doctor",
]
