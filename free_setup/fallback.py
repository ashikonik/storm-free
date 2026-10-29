"""Thread-safe fallback wrapper around a chain of language models.

STORM drives its language models through a single choke point
(``knowledge_storm.lm.LitellmModel.__call__``), so a wrapper that implements the
same interface can be dropped into any of the five ``STORMWikiLMConfigs``
slots without touching upstream code.

Behaviour:

* **Sticky selection** - once a provider succeeds we keep using it, so a
  healthy provider carries the load instead of every call round-robining.
* **Narrow retry** - only rate limits and transient server/network errors move
  on to the next provider.  Authentication and bad-request errors raise
  immediately, because a wrong API key is a configuration bug that should be
  reported rather than papered over by burning every provider in the chain.
* **Thread safety** - STORM runs section generation and simulated
  conversations in ``ThreadPoolExecutor`` pools, so the active-provider index
  and the usage counters are guarded by a lock.

The cost-tracking style follows ECC's ``cost-aware-llm-pipeline`` skill: usage
is recorded in frozen dataclasses rather than mutated counters.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

# Longest delay we will honour from a provider's own retry hint.  A provider
# asking for an hour-long pause is more likely to be reporting a daily quota
# than a temporary one, and blocking the pipeline for that long is worse than
# failing over to the next provider.
_MAX_RETRY_AFTER = 120.0

# HTTP status codes that justify moving on to the next provider.
_RETRYABLE_STATUS = frozenset({408, 409, 425, 429, 500, 502, 503, 504, 522, 524})

# Exception class names we treat as retryable.  Matched by name rather than by
# isinstance so this module keeps working if litellm reorganises its
# exception hierarchy, and so importing litellm is not required at module
# import time.
_RETRYABLE_EXC_NAMES = frozenset(
    {
        "RateLimitError",
        "APIConnectionError",
        "Timeout",
        "APITimeoutError",
        "ServiceUnavailableError",
        "InternalServerError",
        "APIError",
        "ServiceUnavailable",
        "ConnectionError",
        "ConnectTimeout",
        "ReadTimeout",
        "RemoteProtocolError",
        # Provider-side conditions that are transient or that another
        # provider can serve perfectly well.
        "BudgetExceededError",
        "RejectedRequestError",
        "UnsupportedParamsError",
        "BlockedPiiEntityError",
        "OpenAIError",
    }
)

# Exception class names that must NOT trigger a fallback.
_FATAL_EXC_NAMES = frozenset(
    {
        "BadRequestError",
        "UnprocessableEntityError",
        "ContextWindowExceededError",
        "ContentPolicyViolationError",
        "InvalidRequestError",
    }
)

# Exception class names that condemn *this provider* rather than this
# request.  A rejected key, a revoked permission or a model that no longer
# exists will fail identically on every call, so the chain should move on to
# the next provider instead of aborting the whole stage.  This matters
# because the registry deliberately contains providers whose free tier may be
# exhausted or whose key may be wrong -- one of them must not sink a run that
# has healthy providers queued behind it.
_PROVIDER_SCOPED_EXC_NAMES = frozenset(
    {
        "AuthenticationError",
        "PermissionDeniedError",
        "NotFoundError",
    }
)


class GreedySamplingError(RuntimeError):
    """The provider refused a request because of its sampling mode.

    Raised instead of letting the provider's own ``BadRequestError`` escape.
    A ``BadRequestError`` is request-scoped and aborts the chain, but a
    sampling complaint is about the *provider's* mood, not about the request:
    the request is well formed and another attempt with ``top_p=1`` may well
    succeed.  Keeping it in its own type lets the chain treat it as
    retryable and move on to the next provider.
    """


@dataclass(frozen=True)
class CostRecord:
    """One completed LLM call and its token usage."""

    model: str
    provider: str
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def total_tokens(self) -> int:
        return self.prompt_tokens + self.completion_tokens


@dataclass(frozen=True)
class CostTracker:
    """Immutable accumulator of :class:`CostRecord` entries.

    ``add`` returns a new tracker and never mutates the receiver, which is
    the point of the immutable pattern: a tracker handed to one thread
    cannot change under another.  The cost is a copy per record, which is
    irrelevant at the scale of a single article run (a few hundred calls).
    """

    records: tuple[CostRecord, ...] = ()

    def add(self, record: CostRecord) -> CostTracker:
        """Return a new tracker including ``record`` (never mutates self)."""
        return CostTracker(records=(*self.records, record))

    @property
    def total_prompt_tokens(self) -> int:
        return sum(r.prompt_tokens for r in self.records)

    @property
    def total_completion_tokens(self) -> int:
        return sum(r.completion_tokens for r in self.records)

    @property
    def total_tokens(self) -> int:
        return self.total_prompt_tokens + self.total_completion_tokens

    def by_model(self) -> dict[str, dict[str, int]]:
        """Aggregate usage per model in the shape STORM expects."""
        out: dict[str, dict[str, int]] = {}
        for record in self.records:
            entry = out.setdefault(
                record.model, {"prompt_tokens": 0, "completion_tokens": 0}
            )
            entry["prompt_tokens"] += record.prompt_tokens
            entry["completion_tokens"] += record.completion_tokens
        return out


def classify_error(exc: BaseException) -> str:
    """Classify an exception for the fallback chain.

    Returns one of:

    ``"retry"``
        Transient.  Try the next provider.
    ``"provider_dead"``
        This provider is unusable (bad key, revoked permission, retired
        model), but the request itself is fine.  Try the next provider.
    ``"fatal"``
        The request itself is bad.  Retrying it elsewhere will not help, so
        the chain aborts.
    ``"unknown"``
        Unrecognised; treated as retryable by the chain.
    """
    # A sampling complaint is the provider's mood, not the request's
    # fault, so it is retryable regardless of how it was reported.
    if isinstance(exc, GreedySamplingError):
        return "retry"

    status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        if status in _RETRYABLE_STATUS:
            return "retry"
        if status in (401, 403):
            return "provider_dead"

    # Walk the cause chain: LiteLLM wraps provider errors in its own types.
    seen: set[int] = set()
    current: BaseException | None = exc
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        name = type(current).__name__
        if name in _FATAL_EXC_NAMES:
            return "fatal"
        if name in _PROVIDER_SCOPED_EXC_NAMES:
            return "provider_dead"
        if name in _RETRYABLE_EXC_NAMES:
            return "retry"
        current = current.__cause__ or current.__context__

    if isinstance(exc, (TimeoutError, ConnectionError)):
        return "retry"
    return "unknown"


def _is_greedy_sampling_error(exc: BaseException) -> bool:
    """Whether the provider refused the request because of its sampling mode.

    Mistral's API rejects a request that pairs ``temperature=0`` with a
    ``top_p`` below 1, but only on the occasions when its backend has chosen
    to sample greedily.  The request is well formed -- the provider has
    simply changed its mind about how to sample -- so the caller can send it
    again with ``top_p=1`` and get a real answer.

    This is deliberately narrow: a genuine bad request must still abort the
    chain, so the message has to match the provider's own wording.
    """
    message = str(exc).lower()
    return (
        "greedy sampling" in message
        or "greedy_sampling" in message
        or "top_p must be 1" in message
    )


def _normalise_greedy_sampling(exc: BaseException) -> BaseException:
    """Convert a provider's sampling complaint into our own exception type.

    Providers report this as a ``BadRequestError``, which is request-scoped
    and aborts the whole fallback chain.  But the complaint is about the
    provider's sampling mode, not about the request: the request is well
    formed and another provider can serve it.  Converting it here means the
    chain treats it as retryable wherever it surfaces.
    """
    if isinstance(exc, GreedySamplingError):
        return exc
    if _is_greedy_sampling_error(exc):
        return GreedySamplingError(str(exc))
    return exc


def _sleep_in_slices(seconds: float, slice_seconds: float = 1.0) -> None:
    """Sleep for ``seconds``, waking up regularly.

    STORM drives these calls from a bounded thread pool, so a worker parked
    for two minutes is a worker the run cannot use.  Waking up periodically
    keeps the thread responsive to interruption and lets other threads make
    progress on other providers.
    """
    remaining = max(0.0, seconds)
    while remaining > 0:
        chunk = min(slice_seconds, remaining)
        time.sleep(chunk)
        remaining -= chunk


def _is_daily_quota_error(exc: BaseException) -> bool:
    """Whether the error says a *daily* quota is exhausted.

    A per-minute limit resets in seconds and is worth waiting out.  A daily
    limit resets in hours, so the only useful response is to stop using that
    model for the rest of the run and let the chain move on.
    """
    message = str(exc).lower()
    if "per day" in message or "daily" in message or "per_day" in message:
        return True
    # A retry delay of hours is a daily quota even if the words are absent.
    hours = re.search(
        r"retry (?:in|after)\s+([0-9]+(?:\.[0-9]+)?)\s*h", message, re.IGNORECASE
    )
    if hours:
        try:
            return float(hours.group(1)) >= 1.0
        except ValueError:
            return False
    return False


def _is_rate_limit_error(exc: BaseException) -> bool:
    """Whether the error is a quota/rate limit of any window length.

    Some providers raise a plain ``ValueError`` carrying only the message,
    with no status code to inspect, so the text is the only signal.  This
    must stay narrower than a general "quota" match: an exhausted *budget*
    is a billing problem, not a rate limit.
    """
    message = str(exc).lower()
    if "rate limit" in message or "too many requests" in message:
        return True
    if "limit exceeded" in message and "quota" in message:
        return True
    if "requests per" in message and "limit" in message:
        return True
    status = getattr(exc, "status_code", None)
    return isinstance(status, int) and status == 429


def _response_to_dict(response: Any) -> dict[str, Any]:
    """Normalise a LiteLLM response into a plain dictionary.

    LiteLLM returns a pydantic ``ModelResponse`` whose ``.json()`` gives
    nested plain dictionaries, but some provider paths return a raw
    ``dict`` already.  Both are supported here.
    """
    if isinstance(response, dict):
        return response
    json_method = getattr(response, "json", None)
    if callable(json_method):
        try:
            parsed = json_method()
            if isinstance(parsed, dict):
                return parsed
        except Exception:
            logger.debug("Could not serialise the LiteLLM response to JSON.")
    return dict(response)


def _usage_from_inner_model(lm: Any) -> dict[str, Any]:
    """Read the token usage the inner model recorded for the last call.

    Upstream's ``LitellmModel`` accumulates ``prompt_tokens`` and
    ``completion_tokens`` itself, and resets them only when
    ``get_usage_and_reset()`` is called.  The wrapper is the object STORM
    holds, so it never sees that response -- but the counters are still on
    the inner model, and they are the only place the usage survives.

    The counters accumulate, so the value returned here is the *total* the
    inner model has seen, not the cost of the last call.  The caller's
    tracker bills a delta, so that is what it subtracts.
    """
    prompt = getattr(lm, "prompt_tokens", None)
    completion = getattr(lm, "completion_tokens", None)
    if not isinstance(prompt, int) or not isinstance(completion, int):
        return {}
    return {
        "prompt_tokens": prompt,
        "completion_tokens": completion,
        "_cumulative": True,
    }


def _choice_text(choice: Any) -> str:
    """Extract the assistant text from one completion choice.

    LiteLLM's ``ModelResponse`` is a pydantic model, but the choices inside it
    are plain dictionaries, so a choice can carry ``message`` (chat) or
    ``text`` (legacy completions) and neither key is guaranteed.  Reasoning
    models additionally leave ``content`` empty and put their output in
    ``reasoning_content``, so that is checked as a fallback.
    """
    if isinstance(choice, dict):
        message = choice.get("message")
        text = choice.get("text")
    else:
        message = getattr(choice, "message", None)
        text = getattr(choice, "text", None)

    content = None
    if message is not None:
        if isinstance(message, dict):
            content = message.get("content") or message.get("reasoning_content")
        else:
            content = getattr(message, "content", None) or getattr(
                message, "reasoning_content", None
            )

    if content:
        return content
    if text:
        return text
    return ""


# Regex for the retry delay a provider suggests in its rate-limit message.
# Gemini, for example, answers with "Please retry in 18.010432177s." for a
# per-minute limit and "Please retry in 13h28m28.003354075s." for a daily
# one; honouring that hint is much better than failing over immediately,
# because the quota resets on a timer rather than being permanently exhausted.
# Finds "retry in"/"retry after" and the duration that follows it.  The
# duration itself is parsed part by part, so "2 minutes 30 seconds" and
# "13h28m28s" are both read correctly.
_RETRY_AFTER_PATTERN = re.compile(
    r"retry (?:in|after)\s+(?P<duration>[0-9][^\n;,)]*)",
    re.IGNORECASE,
)

# Each "<number> <unit>" pair inside a duration, matched independently.
# The alternation puts the longer units first so that "minutes" is not
# read as "m" followed by junk, and the negative lookahead keeps "500 ms"
# from being read as 500 minutes.
_DURATION_PART_PATTERN = re.compile(
    r"([0-9]+(?:\.[0-9]+)?)\s*"
    # The lookahead, rather than a word boundary, allows the next unit to
    # follow immediately: "13h28m28s" has no space between the parts.
    r"(milliseconds?|hours?|minutes?|secs?|seconds?|hrs?|mins?|ms|hr|m(?!s)|s|h)"
    r"(?![a-z])",
    re.IGNORECASE,
)

# Multipliers converting the units a provider might use into seconds.
_UNIT_SECONDS = {
    "ms": 0.001,
    "millisecond": 0.001,
    "milliseconds": 0.001,
    "s": 1.0,
    "sec": 1.0,
    "secs": 1.0,
    "second": 1.0,
    "seconds": 1.0,
    "m": 60.0,
    "min": 60.0,
    "mins": 60.0,
    "minute": 60.0,
    "minutes": 60.0,
    "h": 3600.0,
    "hr": 3600.0,
    "hrs": 3600.0,
    "hour": 3600.0,
    "hours": 3600.0,
}


def retry_after_seconds(exc: BaseException, default: float = 0.0) -> float:
    """Extract the retry delay a provider asked us to wait for.

    Returns ``default`` when the error carries no usable hint.  A delay that
    exceeds :data:`_MAX_RETRY_AFTER` is reported as ``0.0``, meaning "do not
    wait", so the caller falls over to the next provider rather than stalling
    the run for hours.
    """
    message = str(exc)
    match = _RETRY_AFTER_PATTERN.search(message)
    if not match:
        return default

    seconds = 0.0
    found = False
    for part in _DURATION_PART_PATTERN.finditer(match.group("duration")):
        value_str, unit = part.group(1), part.group(2)
        try:
            value = float(value_str)
        except ValueError:
            continue
        seconds += value * _UNIT_SECONDS.get(unit.lower(), 1.0)
        found = True

    if not found:
        return default
    if seconds > _MAX_RETRY_AFTER:
        # The provider is reporting a daily (or longer) quota.  Waiting that
        # out is not an option, so tell the caller not to wait at all.
        return 0.0
    return max(0.0, seconds)


class RateLimiter:
    """Paces requests so a provider's per-minute quota is not exceeded.

    Free tiers are stingy: Gemini's free plan allows five generate-content
    requests per minute, and STORM issues many in parallel.  Without pacing,
    most of the run ends in 429s.  The limiter sleeps before a call when the
    previous calls were made too recently, and it also parks the provider
    entirely for the delay a 429 asks for.
    """

    def __init__(
        self,
        max_per_minute: int | None = None,
        *,
        window_seconds: float = 60.0,
        max_per_day: int | None = None,
    ) -> None:
        self.max_per_minute = max_per_minute
        self.window_seconds = window_seconds
        self.max_per_day = max_per_day
        self._lock = threading.Lock()
        self._timestamps: list[float] = []
        self._blocked_until = 0.0
        self._daily_count = 0
        self._daily_reset_at = time.monotonic() + 86400.0
        self._exhausted = False

    @property
    def exhausted(self) -> bool:
        """Whether the daily budget for this provider is spent.

        The counter rolls over on its own: once the day is over the budget
        is fresh again and the provider becomes usable.
        """
        with self._lock:
            self._roll_daily_if_due()
            return self._exhausted

    @property
    def daily_used(self) -> int:
        """How many calls have been made against today's budget."""
        with self._lock:
            self._roll_daily_if_due()
            return self._daily_count

    def _roll_daily_if_due(self) -> None:
        """Start a fresh daily budget once the current one has expired.

        Must be called with the lock held.
        """
        if not self.max_per_day:
            return
        now = time.monotonic()
        if now >= self._daily_reset_at:
            self._daily_count = 0
            self._daily_reset_at = now + 86400.0
            self._exhausted = False

    def acquire(self) -> None:
        """Block until another request may be made.

        The limiter keeps the timestamps of the calls made in the last
        window.  When that count has reached the cap, the next call waits
        until the oldest one falls out of the window.

        A call is counted against the daily budget only once it has been
        *admitted*: the decision to let a call through and the increment of
        the counter happen together under one lock acquisition, so
        concurrent callers cannot all observe "under the cap" and all
        proceed.  This keeps ``daily_used`` an accurate count of the calls
        actually made, never more than ``max_per_day``.
        """
        if not self.max_per_minute or self.max_per_minute <= 0:
            with self._lock:
                self._count_daily_call_locked()
            return
        while True:
            with self._lock:
                now = time.monotonic()
                wait = self._blocked_until - now
                if wait <= 0:
                    self._timestamps = [
                        t for t in self._timestamps if now - t < self.window_seconds
                    ]
                    if len(self._timestamps) < self.max_per_minute:
                        self._timestamps.append(now)
                        self._count_daily_call_locked()
                        return
                    # The oldest call in the window decides when a slot frees.
                    wait = self.window_seconds - (now - self._timestamps[0])
                    wait = max(0.01, wait)
                else:
                    wait = max(0.01, wait)
            time.sleep(min(wait, 5.0))

    def _count_daily_call_locked(self) -> None:
        """Count one call against today's budget and spot an exhausted pool.

        Must be called with the lock held.  The lock is not reentrant, so
        this helper deliberately does not take it itself; the caller decides.

        The counter is clamped at ``max_per_day``: it records calls that
        were admitted, so it can never drift past the cap no matter how
        many calls are attempted afterwards.
        """
        if not self.max_per_day:
            return
        self._roll_daily_if_due()
        self._daily_count = min(self._daily_count + 1, self.max_per_day)
        if self._daily_count >= self.max_per_day:
            self._exhausted = True

    def penalise(self, seconds: float) -> None:
        """Park the provider for ``seconds`` after a rate-limit response."""
        if seconds <= 0:
            return
        with self._lock:
            self._blocked_until = max(self._blocked_until, time.monotonic() + seconds)
            # Drop the recorded calls: the quota window restarts now.
            self._timestamps.clear()

    def mark_daily_exhausted(self) -> None:
        """Record that the provider reported its daily quota as spent."""
        with self._lock:
            self._exhausted = True
            self._daily_count = max(self._daily_count, self.max_per_day or 0)


class FallbackLM:
    """An ordered chain of language models with automatic failover.

    Implements the same surface as ``knowledge_storm.lm.LitellmModel``:
    ``__call__``, ``model``, ``kwargs``, ``history``, ``log_usage`` and
    ``get_usage_and_reset``.  That lets it be assigned directly to any of the
    five slots on ``STORMWikiLMConfigs``.
    """

    def __init__(
        self,
        lms: Sequence[Any],
        name: str = "fallback",
        *,
        use_cache: bool = False,
        cache_salt: str | None = None,
        rate_limits: dict[str, int] | None = None,
        daily_limits: dict[str, int] | None = None,
    ) -> None:
        """
        Args:
            lms: the providers, in priority order.
            name: a label used in logs and in the usage report.
            use_cache: whether to allow STORM's shared LRU cache to serve
                responses.  Off by default, because that cache is keyed on
                the request text alone and would serve one provider's answer
                for another's request.
            cache_salt: extra text mixed into the cache key.
            rate_limits: model id -> requests-per-minute cap.  Free tiers
                need this: STORM issues many requests in parallel and would
                otherwise spend the whole run collecting 429s.
            daily_limits: model id -> requests-per-day cap.  The daily
                quota is what actually limits a free tier: a single model
                cannot serve a full STORM run, so the chain needs to know
                when a model's budget for the day is spent and move on.
        """
        if not lms:
            raise ValueError("FallbackLM requires at least one language model")
        self.lms: list[Any] = list(lms)
        self.name = name
        self.use_cache = use_cache
        self.cache_salt = cache_salt or name
        self.model_type = getattr(self.lms[0], "model_type", "chat")
        # Copy the first model's kwargs but keep the credentials out of it:
        # this dict is the sort of thing that ends up in a debug log.
        self.kwargs: dict[str, Any] = {
            key: value
            for key, value in dict(getattr(self.lms[0], "kwargs", {})).items()
            if not key.startswith("api_")
        }
        self.history: list[Any] = []

        self._lock = threading.Lock()
        self._active_index = 0
        self._tracker = CostTracker()
        self._call_counts: dict[str, int] = {}
        self._failure_counts: dict[str, int] = {}
        # The last cumulative usage total seen for each model, so that the
        # cost of a call can be told apart from the running total.
        self._usage_seen: dict[str, tuple[int, int]] = {}
        # The running totals, kept apart from ``_tracker`` so that they
        # survive the reset upstream performs when it collects the usage.
        self._lifetime_usage: dict[str, dict[str, int]] = {}

        limits = rate_limits or {}
        daily = daily_limits or {}
        self._limiters: dict[str, RateLimiter] = {}
        for lm in self.lms:
            model_id = str(getattr(lm, "model", "?"))
            self._limiters[model_id] = RateLimiter(
                limits.get(model_id), max_per_day=daily.get(model_id)
            )

    # -- introspection -------------------------------------------------

    @property
    def active_index(self) -> int:
        """Index of the provider currently preferred."""
        with self._lock:
            return self._active_index

    def provider_names(self) -> list[str]:
        """Model ids of every provider in the chain, in order."""
        return [str(getattr(lm, "model", "?")) for lm in self.lms]

    def stats(self) -> dict[str, Any]:
        """Per-provider call and failure counts, plus token totals."""
        with self._lock:
            return {
                "calls": dict(self._call_counts),
                "failures": dict(self._failure_counts),
                "prompt_tokens": self._tracker.total_prompt_tokens,
                "completion_tokens": self._tracker.total_completion_tokens,
                "total_tokens": self._tracker.total_tokens,
                "active": self.provider_names()[self._active_index],
            }

    # -- usage accounting ----------------------------------------------

    def _record_usage(self, model: str, response: dict[str, Any]) -> None:
        """Record token usage against the model that served the request.

        This has to happen where the serving provider is known.  STORM calls
        ``log_usage`` on the wrapper *after* ``__call__`` returns, and by then
        another thread may have failed over, so attributing the tokens to
        whichever provider is currently active would bill the wrong one.

        Two shapes of usage arrive here.  The uncached LiteLLM path hands
        over the response for one call, and its figures are the cost of
        that call.  The plain path hands over the inner model's *cumulative*
        counters, which only ever grow, so the cost of this call is the
        difference since the last time that model was recorded.
        """
        usage = (response or {}).get("usage")
        if not isinstance(usage, dict):
            return
        prompt = int(usage.get("prompt_tokens", 0) or 0)
        completion = int(usage.get("completion_tokens", 0) or 0)

        with self._lock:
            if usage.get("_cumulative"):
                previous = self._usage_seen.get(model, (0, 0))
                # The counters can be reset by the inner model, in which
                # case the new total is smaller than the last one seen and
                # the whole total is this call's cost.
                prompt = max(prompt - previous[0], 0)
                completion = max(completion - previous[1], 0)
                self._usage_seen[model] = (
                    int(usage.get("prompt_tokens", 0) or 0),
                    int(usage.get("completion_tokens", 0) or 0),
                )
            record = CostRecord(
                model=model,
                provider=model.split("/", 1)[0],
                prompt_tokens=prompt,
                completion_tokens=completion,
            )
            self._tracker = self._tracker.add(record)
            # The tracker is resettable, and upstream resets it during
            # ``post_run``.  The run log is written after that, so the
            # totals it needs have to live somewhere the reset cannot
            # reach.
            total = self._lifetime_usage.setdefault(
                model, {"prompt_tokens": 0, "completion_tokens": 0}
            )
            total["prompt_tokens"] += prompt
            total["completion_tokens"] += completion

    def log_usage(self, response: dict[str, Any]) -> None:
        """Record token usage from a raw provider response.

        Kept for interface compatibility with ``LitellmModel``.  The wrapper
        records usage itself, inside ``__call__``, where it knows which
        provider actually served the request; this method only covers the
        case where something else hands us a response directly.
        """
        model = str(getattr(self.lms[self.active_index], "model", "unknown"))
        self._record_usage(model, response)

    def get_usage_and_reset(self) -> dict[str, dict[str, int]]:
        """Return accumulated usage per model and reset the counters.

        The shape matches what ``LMConfigs.collect_and_reset_lm_usage`` in
        ``knowledge_storm.interface`` expects.
        """
        with self._lock:
            usage = self._tracker.by_model()
            self._tracker = CostTracker()
            return usage

    def usage_summary(self) -> dict[str, dict[str, int]]:
        """Return token usage per model, without resetting the counters.

        Upstream's ``Engine.summary()`` only prints its report and returns
        ``None``, so the run log cannot use it.  This accessor exposes the
        same information, so a run log can record what each provider
        actually cost.

        The totals come from a record kept apart from the resettable
        tracker, because upstream collects and resets the usage during
        ``post_run`` -- and the run log is written after that.

        The returned mapping is keyed by model id and holds
        ``prompt_tokens``, ``completion_tokens``, ``total_tokens`` and
        ``calls``.
        """
        with self._lock:
            by_model = {
                model: dict(tokens) for model, tokens in self._lifetime_usage.items()
            }
            calls = dict(self._call_counts)
        summary: dict[str, dict[str, int]] = {}
        for model, tokens in by_model.items():
            prompt = tokens.get("prompt_tokens", 0)
            completion = tokens.get("completion_tokens", 0)
            summary[model] = {
                "prompt_tokens": prompt,
                "completion_tokens": completion,
                "total_tokens": prompt + completion,
                "calls": calls.get(model, 0),
            }
        for model, count in calls.items():
            # A provider that served requests but returned no usage should
            # still show up in the summary.
            summary.setdefault(
                model,
                {
                    "prompt_tokens": 0,
                    "completion_tokens": 0,
                    "total_tokens": 0,
                    "calls": count,
                },
            )
        return summary

    def provider_stats(self) -> dict[str, int]:
        """Return the number of requests served by each provider."""
        with self._lock:
            return dict(self._call_counts)

    # -- invocation ----------------------------------------------------

    def __call__(
        self,
        prompt: str | None = None,
        messages: list[dict[str, str]] | None = None,
        **kwargs: Any,
    ) -> list[str]:
        """Call the active provider, failing over on transient errors.

        A rate-limited provider is retried after the delay it asked for
        before the chain moves on, because free-tier quotas reset on a timer
        rather than being exhausted.  When the delay would be long, the chain
        falls through to the next provider instead of stalling the run.
        """
        last_exc: BaseException | None = None
        tried = 0
        skipped: list[str] = []

        # Snapshot the starting provider once, so that concurrent calls do
        # not chase each other around the chain.  Each call still visits
        # every provider at most once.
        start = self.active_index
        visited: set[int] = set()

        for offset in range(len(self.lms)):
            index = (start + offset) % len(self.lms)
            if index in visited:
                continue
            visited.add(index)

            lm = self.lms[index]
            model_id = str(getattr(lm, "model", "?"))
            tried += 1

            limiter = self._limiters.get(model_id)
            if limiter is not None and limiter.exhausted:
                # This model's daily budget is spent; there is nothing to
                # gain from asking it again today.
                skipped.append(model_id)
                logger.warning(
                    "Skipping %s: its daily request budget is spent.",
                    model_id,
                )
                continue

            if limiter is not None:
                limiter.acquire()

            try:
                outputs, response = self._invoke(lm, prompt, messages, kwargs)
            except Exception as exc:
                last_exc = exc
                verdict = classify_error(exc)
                self._record_failure(model_id, verdict)
                if verdict == "fatal":
                    logger.exception(
                        "Provider %s raised a non-retryable error; aborting chain.",
                        model_id,
                    )
                    raise

                if verdict == "provider_dead":
                    # This provider is unusable -- a rejected key, revoked
                    # permission or a retired model.  The request itself is
                    # fine, so move on to the next provider rather than
                    # sinking the whole stage.  The registry deliberately
                    # contains providers whose free tier may be exhausted.
                    logger.warning(
                        "Provider %s is unusable (%s); trying the next "
                        "provider in the chain.",
                        model_id,
                        type(exc).__name__,
                    )
                    continue

                if _is_daily_quota_error(exc) and limiter is not None:
                    # The provider says its *daily* quota is gone.  No amount
                    # of waiting inside this run helps, so park it for the day
                    # and move on to the next provider in the chain.
                    limiter.mark_daily_exhausted()
                    skipped.append(model_id)
                    logger.warning(
                        "Provider %s has exhausted its daily quota; "
                        "skipping it for the rest of the run.",
                        model_id,
                    )
                    last_exc = None
                    continue

                delay = retry_after_seconds(exc)
                if verdict == "retry" and delay > 0 and limiter is not None:
                    # Park this provider, then try it again from the top of
                    # the chain.  The quota is time-based, so waiting is the
                    # right move rather than burning the other providers.
                    limiter.penalise(delay)
                    logger.warning(
                        "Provider %s is rate limited; retrying it in %.1fs.",
                        model_id,
                        delay,
                    )
                    # Sleep in short slices rather than one long block: STORM
                    # runs these calls from a bounded thread pool, and a
                    # worker parked for two minutes would starve the run.
                    _sleep_in_slices(delay)
                    try:
                        # The retry consumes a quota slot of its own.
                        limiter.acquire()
                        outputs, response = self._invoke(lm, prompt, messages, kwargs)
                    except Exception as retry_exc:
                        last_exc = retry_exc
                        self._record_failure(model_id, classify_error(retry_exc))
                        logger.warning(
                            "Provider %s still failing after waiting: %s",
                            model_id,
                            retry_exc,
                        )
                        continue
                else:
                    logger.warning(
                        "Provider %s failed (%s: %s); trying the next provider in the chain.",
                        model_id,
                        verdict,
                        exc,
                    )
                    continue

            with self._lock:
                self._active_index = index
                self._call_counts[model_id] = self._call_counts.get(model_id, 0) + 1
            # Bill the tokens to the provider that served this request, not
            # to whichever provider happens to be active by now.
            self._record_usage(model_id, response)
            # Record the provider that actually served the request.  The run
            # log only ever saw failures before, so it was impossible to
            # tell afterwards which provider had produced the article.
            logger.info(
                "Provider %s served the request (%d call(s) so far).",
                model_id,
                self._call_counts.get(model_id, 0),
            )
            return outputs

        if last_exc is None and skipped:
            raise RuntimeError(
                "Every provider in the fallback chain has used up its daily "
                f"request budget: {', '.join(skipped)}."
            )
        assert last_exc is not None
        # When some providers were skipped for an exhausted budget, that is
        # the reason the run failed — not whatever error the last provider
        # happened to raise.  Lead with it so the message points at the fix.
        if skipped:
            raise RuntimeError(
                "Every provider in the fallback chain failed. "
                f"{len(skipped)} provider(s) had no daily request budget "
                f"left ({', '.join(skipped)}). Last error: {last_exc}"
            ) from last_exc
        raise RuntimeError(
            f"All {tried} provider(s) in the fallback chain failed. "
            f"Last error: {last_exc}"
        ) from last_exc

    def _invoke(
        self,
        lm: Any,
        prompt: str | None,
        messages: list[dict[str, str]] | None,
        kwargs: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        """Invoke one provider, honouring the cache-safety rules.

        Only LiteLLM-backed models need the uncached path.  STORM's
        ``cached_litellm_completion`` is an ``lru_cache`` keyed on the request
        string, and that string is identical across providers, so it would
        otherwise serve one provider's answer for another's request.

        Every other model implementation keeps its own ``__call__``, which
        matters for custom clients and for tests.

        Returns the outputs and the raw response, so the caller can bill the
        tokens to the provider that actually served the request.
        """
        if self.use_cache or not self._is_litellm_model(lm):
            try:
                outputs = lm(prompt=prompt, messages=messages, **kwargs)
            except Exception as exc:
                raise _normalise_greedy_sampling(exc) from None
            usage = _usage_from_inner_model(lm)
            self._record_history(lm, prompt, messages, outputs, {"usage": usage})
            return outputs, {"usage": usage}
        return self._invoke_uncached(lm, prompt, messages, kwargs)

    @staticmethod
    def _is_litellm_model(lm: Any) -> bool:
        """Whether this model is backed by LiteLLM's OpenAI-compatible path."""
        module = type(lm).__module__ or ""
        if module.startswith("knowledge_storm.lm"):
            # LitellmModel and the provider-specific wrappers in that module
            # all expose a LitellmModel-compatible ``model`` attribute.
            return hasattr(lm, "model") and not hasattr(lm, "base_url")
        return False

    def _invoke_uncached(
        self,
        lm: Any,
        prompt: str | None,
        messages: list[dict[str, str]] | None,
        kwargs: dict[str, Any],
    ) -> tuple[list[str], dict[str, Any]]:
        """Call a provider directly through LiteLLM, bypassing STORM's cache.

        Returns the outputs and the raw response so the caller can bill the
        tokens to this provider.
        """
        import litellm

        merged = {**getattr(lm, "kwargs", {}), **kwargs}
        api_key = merged.pop("api_key", None)
        api_base = merged.pop("api_base", None)
        payload: dict[str, Any] = {
            "model": getattr(lm, "model", None),
            "messages": messages or [{"role": "user", "content": prompt}],
            **merged,
        }
        if api_key is not None:
            payload["api_key"] = api_key
        if api_base is not None:
            payload["api_base"] = api_base

        try:
            response = litellm.completion(
                cache={"no-cache": True, "no-store": True}, **payload
            )
        except Exception as exc:
            if not _is_greedy_sampling_error(exc):
                raise
            # Mistral refuses a request that pairs a temperature of 0 with a
            # top_p below 1, but only when its backend has decided to sample
            # greedily -- which it does intermittently, for reasons the
            # caller cannot control.  The request is well formed, so the
            # right response is to send it again with top_p=1 rather than to
            # abandon the provider.
            logger.info(
                "Provider %s rejected the sampling parameters; retrying with top_p=1.",
                getattr(lm, "model", "?"),
            )
            payload["top_p"] = 1.0
            try:
                response = litellm.completion(
                    cache={"no-cache": True, "no-store": True}, **payload
                )
            except Exception as retry_exc:
                if not _is_greedy_sampling_error(retry_exc):
                    raise
                # The provider rejected the sampling parameters a second
                # time, even with top_p=1.  The error it raised is a
                # BadRequestError, which is request-scoped and would abort
                # the whole chain -- but the complaint is about the
                # provider's sampling mode, not about the request.  Raise a
                # dedicated type so the chain treats it as retryable and
                # moves on to the next provider instead of killing the run.
                logger.warning(
                    "Provider %s rejected the sampling parameters again; "
                    "giving up on it for this request.",
                    getattr(lm, "model", "?"),
                )
                raise GreedySamplingError(
                    f"{getattr(lm, 'model', '?')} refused the request twice "
                    "because of its sampling mode: "
                    f"{retry_exc}"
                ) from retry_exc
        response_dict = _response_to_dict(response)
        outputs = [
            _choice_text(choice) for choice in response_dict.get("choices") or []
        ]
        self._record_history(lm, prompt, payload["messages"], outputs, response_dict)
        return outputs, response_dict

    def _record_history(
        self,
        lm: Any,
        prompt: str | None,
        messages: list[dict[str, str]] | None,
        outputs: list[str],
        response: dict[str, Any],
    ) -> None:
        """Record one call in the history upstream collects.

        Upstream's ``collect_and_reset_lm_history`` reads the *wrapper's*
        ``history`` attribute -- the one on the object STORM holds a
        reference to -- and writes it to ``llm_call_history.jsonl``.
        Appending only to the inner model's history left that file empty in
        every run, so the entry has to go on the wrapper as well.
        """
        # Mirror LitellmModel's bookkeeping so history stays useful.  The
        # wrapper records the usage itself, against the provider that served
        # the request.
        entry: dict[str, Any] = {
            "prompt": prompt,
            "messages": messages or [{"role": "user", "content": prompt}],
            "outputs": outputs,
            "usage": dict((response or {}).get("usage") or {}),
        }
        with self._lock:
            self.history.append(entry)
            try:
                lm.history.append(entry)
            except Exception:
                logger.debug(
                    "Could not append to history for %s", getattr(lm, "model", "?")
                )

    def _record_failure(self, model_id: str, verdict: str) -> None:
        with self._lock:
            key = f"{model_id} ({verdict})"
            self._failure_counts[key] = self._failure_counts.get(key, 0) + 1

    def __repr__(self) -> str:
        return f"FallbackLM({self.name}, providers={self.provider_names()})"


def build_fallback(
    lms: Iterable[Any],
    name: str,
    *,
    use_cache: bool = False,
) -> FallbackLM:
    """Convenience constructor for :class:`FallbackLM`."""
    return FallbackLM(list(lms), name=name, use_cache=use_cache)


__all__ = [
    "CostRecord",
    "CostTracker",
    "FallbackLM",
    "build_fallback",
    "classify_error",
]
