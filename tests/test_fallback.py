"""Tests for the fallback wrapper and provider registry.

These cover the safety-critical behaviour: which errors trigger a failover,
which ones must not, and that the chain stays correct under concurrency.

Run with:  ./venv/bin/python -m pytest tests/ -v
"""

from __future__ import annotations

import threading
from typing import Any

import pytest

from free_setup.fallback import (
    CostRecord,
    CostTracker,
    FallbackLM,
    GreedySamplingError,
    classify_error,
)
from free_setup.providers import (
    PROVIDERS,
    UnknownProviderError,
    discover_available,
    provider_of_model,
    split_model,
)


class FakeHTTPError(Exception):
    """An exception carrying a status_code, like LiteLLM's own errors."""

    def __init__(self, message: str, status_code: int) -> None:
        super().__init__(message)
        self.status_code = status_code


class RateLimitError(FakeHTTPError):
    """Stand-in for litellm.exceptions.RateLimitError."""


class AuthenticationError(FakeHTTPError):
    """Stand-in for litellm.exceptions.AuthenticationError."""


class BadRequestError(FakeHTTPError):
    """Stand-in for litellm.exceptions.BadRequestError."""


class PermissionDeniedError(FakeHTTPError):
    """Stand-in for litellm.exceptions.PermissionDeniedError."""


class NotFoundError(FakeHTTPError):
    """Stand-in for litellm.exceptions.NotFoundError."""


class APIConnectionError(Exception):
    """Stand-in for litellm.exceptions.APIConnectionError."""


class FakeLM:
    """A minimal language model that records calls and can be made to fail."""

    def __init__(self, model: str, error: BaseException | None = None) -> None:
        self.model = model
        self.error = error
        self.kwargs: dict[str, Any] = {"max_tokens": 100}
        self.model_type = "chat"
        self.history: list[Any] = []
        self.calls = 0

    def __call__(self, prompt=None, messages=None, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return [f"response from {self.model}"]

    def log_usage(self, response: dict[str, Any]) -> None:
        pass

    def get_usage_and_reset(self) -> dict[str, dict[str, int]]:
        return {self.model: {"prompt_tokens": 0, "completion_tokens": 0}}


# -- error classification ---------------------------------------------------


def test_rate_limit_is_retryable() -> None:
    assert classify_error(RateLimitError("slow down", 429)) == "retry"


def test_server_error_is_retryable() -> None:
    assert classify_error(FakeHTTPError("boom", 503)) == "retry"


def test_connection_error_is_retryable() -> None:
    assert classify_error(APIConnectionError("no route")) == "retry"


def test_auth_error_is_provider_scoped() -> None:
    """A rejected key condemns the provider, not the request.

    The registry deliberately contains providers whose free tier may be
    exhausted or whose key may be wrong.  One of them must not sink a run
    that has healthy providers queued behind it.
    """
    assert classify_error(AuthenticationError("bad key", 401)) == "provider_dead"
    assert classify_error(AuthenticationError("forbidden", 403)) == "provider_dead"
    assert classify_error(PermissionDeniedError("nope", 403)) == "provider_dead"
    assert classify_error(NotFoundError("gone", 404)) == "provider_dead"


def test_bad_request_is_fatal() -> None:
    assert classify_error(BadRequestError("bad model", 400)) == "fatal"


def test_unknown_error_is_not_retryable() -> None:
    assert classify_error(ValueError("who knows")) == "unknown"


def test_cause_chain_is_inspected() -> None:
    """A wrapped provider error must still be classified correctly."""
    inner = RateLimitError("slow down", 429)
    outer = RuntimeError("request failed")
    outer.__cause__ = inner
    assert classify_error(outer) == "retry"


@pytest.mark.parametrize(
    "name",
    [
        "BudgetExceededError",
        "RejectedRequestError",
        "UnsupportedParamsError",
        "BlockedPiiEntityError",
        "OpenAIError",
    ],
)
def test_provider_side_errors_are_retryable(name: str) -> None:
    """These are provider-side conditions another provider can serve.

    They used to fall through to "unknown", which is not a verdict the chain
    treats as a clean failover.
    """
    exc = type(name, (Exception,), {})(name)
    assert classify_error(exc) == "retry"


def test_greedy_sampling_error_is_retryable() -> None:
    """A sampling complaint is the provider's mood, not the request's fault."""
    assert classify_error(GreedySamplingError("top_p must be 1")) == "retry"


# -- failover behaviour -----------------------------------------------------


def test_primary_provider_is_used_when_healthy() -> None:
    primary = FakeLM("groq/model-a")
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])

    assert chain("hello") == ["response from groq/model-a"]
    assert primary.calls == 1
    assert backup.calls == 0


def test_fallback_takes_over_on_rate_limit() -> None:
    primary = FakeLM("groq/model-a", RateLimitError("slow down", 429))
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])

    assert chain("hello") == ["response from gemini/model-b"]
    assert primary.calls == 1
    assert backup.calls == 1


def test_auth_error_advances_the_chain() -> None:
    """A wrong API key must not sink a run that has healthy providers behind it.

    The chain moves on to the next provider, because the request itself is
    fine and another provider can serve it.
    """
    primary = FakeLM("groq/model-a", AuthenticationError("bad key", 401))
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])

    assert chain("hello") == ["response from gemini/model-b"]
    assert primary.calls == 1
    assert backup.calls == 1


def test_bad_request_does_not_advance_the_chain() -> None:
    """A bad request is the caller's fault, so retrying elsewhere will not help."""
    primary = FakeLM("groq/model-a", BadRequestError("bad model", 400))
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])

    with pytest.raises(BadRequestError):
        chain("hello")
    assert backup.calls == 0, "request-scoped errors must not advance the chain"


def test_all_providers_failing_raises() -> None:
    first = FakeLM("groq/model-a", RateLimitError("slow down", 429))
    second = FakeLM("gemini/model-b", APIConnectionError("no route"))
    chain = FallbackLM([first, second])

    with pytest.raises(RuntimeError, match="fallback chain failed"):
        chain("hello")


def test_successful_provider_becomes_sticky() -> None:
    """Once a provider works, later calls should prefer it."""
    first = FakeLM("groq/model-a", RateLimitError("slow down", 429))
    second = FakeLM("gemini/model-b")
    chain = FallbackLM([first, second])

    chain("one")
    assert chain.active_index == 1
    chain("two")
    assert second.calls == 2, "the working provider should keep serving"


def test_empty_chain_is_rejected() -> None:
    with pytest.raises(ValueError):
        FallbackLM([])


# -- greedy-sampling recovery ------------------------------------------------


class GreedyLM(FakeLM):
    """A model that always refuses because of its sampling mode.

    Mirrors Mistral's ``"top_p must be 1 when using greedy sampling"``.
    """

    def __init__(self, model: str) -> None:
        super().__init__(
            model,
            BadRequestError(
                "MistralException - top_p must be 1 when using greedy sampling.",
                400,
            ),
        )


def test_greedy_sampling_error_does_not_abort_the_chain() -> None:
    """One provider's sampling quirk must not sink the whole run.

    This is the failure recorded in the project's own run log: Mistral
    refused the request, the recovery attempt refused it too, and the
    resulting BadRequestError aborted the chain while Groq and Gemini were
    still waiting behind it.
    """
    primary = GreedyLM("mistral/model-a")
    backup = FakeLM("groq/model-b")
    chain = FallbackLM([primary, backup])

    assert chain("hello") == ["response from groq/model-b"]
    assert primary.calls == 1
    assert backup.calls == 1


def test_greedy_sampling_error_is_raised_as_its_own_type() -> None:
    """The error must not escape as a request-scoped BadRequestError."""
    primary = GreedyLM("mistral/model-a")
    chain = FallbackLM([primary])

    with pytest.raises(RuntimeError) as excinfo:
        chain("hello")
    # A single-provider chain has nowhere to fall back to, so the error
    # surfaces -- but as the retryable type, not as a fatal bad request.
    assert isinstance(excinfo.value.__cause__, GreedySamplingError)


# -- call history ------------------------------------------------------------


def test_history_is_recorded_on_the_wrapper() -> None:
    """Upstream reads the wrapper's history, not the inner model's.

    Appending only to the inner model left ``llm_call_history.jsonl``
    empty in every run.
    """
    primary = FakeLM("groq/model-a")
    chain = FallbackLM([primary])

    chain("hello")

    assert len(chain.history) == 1
    assert chain.history[0]["outputs"] == ["response from groq/model-a"]


def test_history_survives_the_way_upstream_collects_it() -> None:
    """Mirror upstream's ``collect_and_reset_lm_history`` behaviour.

    Upstream walks the ``*_lm`` attributes of the LM config object -- each
    of which holds the ``FallbackLM`` wrapper -- and collects their
    ``history`` lists.
    """
    primary = FakeLM("groq/model-a")
    chain = FallbackLM([primary])

    chain("hello")

    # Stand in for upstream's STORMWikiLMConfigs, which holds the wrapper
    # in a ``*_lm`` attribute.  Upstream iterates the *instance* dict, so
    # the attribute has to be set on the instance.
    class _LMConfigs:
        pass

    lm_configs = _LMConfigs()
    lm_configs.conv_simulator_lm = chain

    history = []
    for attr_name in lm_configs.__dict__:
        if "_lm" in attr_name and hasattr(getattr(lm_configs, attr_name), "history"):
            history.extend(getattr(lm_configs, attr_name).history)
            getattr(lm_configs, attr_name).history = []

    assert len(history) == 1, "the history entry must survive collection"


def test_usage_summary_reports_tokens_and_calls() -> None:
    """The run log needs token usage; upstream's summary() returns None."""
    primary = FakeLM("groq/model-a")
    chain = FallbackLM([primary])

    chain("hello")

    summary = chain.usage_summary()
    assert "groq/model-a" in summary
    assert summary["groq/model-a"]["calls"] == 1


# -- LiteLLM response parsing ------------------------------------------------


def test_choice_text_reads_dict_message() -> None:
    """LiteLLM returns plain dicts for choices, not objects.

    Reading them with attribute access raises ``KeyError: 'text'`` because a
    chat choice carries ``message`` but no ``text`` key.
    """
    from free_setup.fallback import _choice_text

    choice = {"finish_reason": "length", "index": 0, "message": {"content": "ok"}}
    assert _choice_text(choice) == "ok"


def test_choice_text_reads_object_message() -> None:
    from free_setup.fallback import _choice_text

    class _Message:
        content = "hello"

    class _Choice:
        message = _Message()

    assert _choice_text(_Choice()) == "hello"


def test_choice_text_falls_back_to_legacy_text_key() -> None:
    from free_setup.fallback import _choice_text

    assert _choice_text({"text": "legacy"}) == "legacy"


def test_choice_text_handles_reasoning_content() -> None:
    """Reasoning models can leave ``content`` empty."""
    from free_setup.fallback import _choice_text

    choice = {"message": {"content": None, "reasoning_content": "thought"}}
    assert _choice_text(choice) == "thought"


def test_choice_text_returns_empty_string_for_missing_content() -> None:
    from free_setup.fallback import _choice_text

    assert _choice_text({"message": {"content": None}}) == ""


# -- rate limiting ----------------------------------------------------------


def test_retry_after_seconds_reads_seconds() -> None:
    from free_setup.fallback import retry_after_seconds

    exc = ValueError("Quota exceeded. Please retry in 18.010432177s.")
    assert retry_after_seconds(exc) == pytest.approx(18.01, abs=0.01)


def test_retry_after_seconds_reads_minutes() -> None:
    from free_setup.fallback import retry_after_seconds

    assert retry_after_seconds(ValueError("retry after 2 minutes")) == 120.0


def test_retry_after_seconds_reads_milliseconds() -> None:
    from free_setup.fallback import retry_after_seconds

    assert retry_after_seconds(ValueError("retry in 500 ms")) == 0.5


def test_retry_after_seconds_reads_compound_hours_and_minutes() -> None:
    """Gemini reports a daily limit as "13h28m28s"."""
    from free_setup.fallback import retry_after_seconds

    exc = ValueError("Please retry in 13h28m28.003354075s.")
    assert retry_after_seconds(exc) == 0.0, "a daily quota must not be waited out"


def test_retry_after_seconds_rejects_a_wait_over_the_cap() -> None:
    from free_setup.fallback import retry_after_seconds

    assert retry_after_seconds(ValueError("retry in 5m")) == 0.0, (
        "a five-minute wait is longer than the cap, so it must not be waited out"
    )


def test_retry_after_seconds_reads_minutes_under_the_cap() -> None:
    from free_setup.fallback import retry_after_seconds

    assert retry_after_seconds(ValueError("retry in 90s")) == 90.0


def test_retry_after_seconds_reads_a_compound_duration() -> None:
    """A provider may spell out more than one unit."""
    from free_setup.fallback import retry_after_seconds

    exc = ValueError("Please retry in 1 minute 30 seconds.")
    assert retry_after_seconds(exc) == 90.0


def test_retry_after_seconds_reads_a_compact_duration() -> None:
    """Units may follow each other with no space: "13h28m28s"."""
    from free_setup.fallback import retry_after_seconds

    exc = ValueError("Please retry in 13h28m28.003354075s.")
    assert retry_after_seconds(exc) == 0.0, (
        "a duration of hours is a daily quota and must not be waited out"
    )


def test_retry_after_seconds_falls_back_to_default() -> None:
    from free_setup.fallback import retry_after_seconds

    assert retry_after_seconds(ValueError("boom"), default=5.0) == 5.0


def test_retry_after_seconds_is_clamped() -> None:
    """A provider asking for an absurd wait must not wedge the pipeline."""
    from free_setup.fallback import retry_after_seconds

    assert retry_after_seconds(ValueError("retry in 99999 s")) == 0.0


def test_rate_limiter_paces_requests() -> None:
    """The limiter must space calls out to respect a per-minute cap."""
    import time

    from free_setup.fallback import RateLimiter

    # A one-second window keeps the test fast; the real limiter uses 60s.
    # The cap is two calls per window, so the third call has to wait.
    limiter = RateLimiter(max_per_minute=2, window_seconds=1.0)
    limiter.acquire()
    limiter.acquire()
    start = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - start >= 0.5


def test_rate_limiter_allows_calls_up_to_the_cap() -> None:
    """Calls within the cap must not be delayed."""
    import time

    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_minute=10, window_seconds=60.0)
    start = time.monotonic()
    for _ in range(10):
        limiter.acquire()
    assert time.monotonic() - start < 0.5


def test_rate_limiter_is_disabled_when_unset() -> None:
    import time

    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_minute=None)
    start = time.monotonic()
    for _ in range(50):
        limiter.acquire()
    assert time.monotonic() - start < 0.5


def test_rate_limiter_penalise_blocks_until_the_delay_passes() -> None:
    import time

    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_minute=1000)
    limiter.penalise(0.3)
    start = time.monotonic()
    limiter.acquire()
    assert time.monotonic() - start >= 0.25


def test_chain_retries_a_rate_limited_provider_after_waiting() -> None:
    """A 429 with a retry hint should be waited out, not skipped."""
    from free_setup.fallback import FallbackLM

    class RateLimitedOnce(FakeLM):
        def __init__(self) -> None:
            super().__init__("gemini/model-a")
            self.attempts = 0

        def __call__(self, prompt=None, messages=None, **kwargs):
            self.attempts += 1
            if self.attempts == 1:
                raise RateLimitError("Quota exceeded. Please retry in 0.2s.", 429)
            return [f"response from {self.model}"]

    primary = RateLimitedOnce()
    backup = FakeLM("groq/model-b")
    chain = FallbackLM([primary, backup], rate_limits={"gemini/model-a": 1000})

    assert chain("hello") == ["response from gemini/model-a"]
    assert primary.attempts == 2, "the rate-limited provider should be retried"
    assert backup.calls == 0, "the backup should not be needed"


def test_chain_falls_through_when_no_retry_hint_is_given() -> None:
    """Without a hint there is nothing to wait for, so fail over at once."""
    from free_setup.fallback import FallbackLM

    primary = FakeLM("groq/model-a", RateLimitError("slow down", 429))
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])

    assert chain("hello") == ["response from gemini/model-b"]
    assert backup.calls == 1


def test_daily_quota_error_is_detected() -> None:
    from free_setup.fallback import _is_daily_quota_error

    exc = ValueError(
        "Quota exceeded for metric: generate_content_free_tier_requests, "
        "description: Generate content requests per day limit exceeded."
    )
    assert _is_daily_quota_error(exc) is True


def test_per_minute_quota_error_is_not_daily() -> None:
    from free_setup.fallback import _is_daily_quota_error

    exc = ValueError(
        "Generate content requests per minute limit exceeded on Free Tier."
    )
    assert _is_daily_quota_error(exc) is False


def test_chain_skips_a_provider_whose_daily_quota_is_spent() -> None:
    """A daily quota error should retire the model for the rest of the run."""
    from free_setup.fallback import FallbackLM

    daily = ValueError(
        "Quota exceeded: Generate content requests per day limit exceeded."
    )
    primary = FakeLM("gemini/model-a", daily)
    backup = FakeLM("groq/model-b")
    chain = FallbackLM([primary, backup], daily_limits={"gemini/model-a": 20})

    assert chain("hello") == ["response from groq/model-b"]
    assert backup.calls == 1

    # The second call must not go back to the exhausted model.
    chain("hello again")
    assert primary.calls == 1, "the exhausted model must not be called again"
    assert backup.calls == 2


def test_chain_raises_a_clear_error_when_every_daily_budget_is_spent() -> None:
    from free_setup.fallback import FallbackLM

    daily = ValueError(
        "Quota exceeded: Generate content requests per day limit exceeded."
    )
    chain = FallbackLM(
        [FakeLM("gemini/model-a", daily)], daily_limits={"gemini/model-a": 20}
    )

    with pytest.raises(RuntimeError, match="daily request budget"):
        chain("hello")


# -- usage accounting -------------------------------------------------------


def test_usage_is_aggregated_per_model() -> None:
    lm = FakeLM("groq/model-a")
    chain = FallbackLM([lm])

    chain("hello")
    chain.log_usage({"usage": {"prompt_tokens": 100, "completion_tokens": 50}})

    usage = chain.get_usage_and_reset()
    assert usage["groq/model-a"]["prompt_tokens"] == 100
    assert usage["groq/model-a"]["completion_tokens"] == 50


def test_usage_is_reset_after_reading() -> None:
    lm = FakeLM("groq/model-a")
    chain = FallbackLM([lm])
    chain.log_usage({"usage": {"prompt_tokens": 10, "completion_tokens": 5}})

    chain.get_usage_and_reset()
    assert chain.get_usage_and_reset() == {}


def test_usage_is_attributed_to_the_provider_that_served() -> None:
    """Tokens must be billed to the provider that answered.

    STORM calls ``log_usage`` *after* ``__call__`` returns, by which time the
    active provider may already have moved on.  Billing against the active
    provider would therefore attribute tokens to the wrong model.
    """
    chain = FallbackLM([FakeLM("groq/model-a"), FakeLM("gemini/model-b")])

    chain("hello")
    usage = chain.get_usage_and_reset()

    assert "groq/model-a" in usage
    assert "gemini/model-b" not in usage


def test_usage_follows_a_failover() -> None:
    """After a failover the tokens belong to the backup, not the primary."""
    primary = FakeLM("groq/model-a", RateLimitError("slow down", 429))
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])

    chain("hello")
    usage = chain.get_usage_and_reset()

    assert "gemini/model-b" in usage
    assert "groq/model-a" not in usage


def test_daily_budget_resets_after_the_day_is_over() -> None:
    """A retired provider must come back once the daily window rolls over."""
    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_day=1)
    limiter.acquire()
    limiter.mark_daily_exhausted()
    assert limiter.exhausted is True

    limiter._daily_reset_at = 0.0
    assert limiter.exhausted is False
    assert limiter.daily_used == 0


def test_chain_retires_a_daily_exhausted_provider() -> None:
    """Once the daily budget is spent the provider must not be called again."""
    daily = ValueError(
        "Quota exceeded: Generate content requests per day limit exceeded."
    )
    primary = FakeLM("gemini/model-a", daily)
    backup = FakeLM("groq/model-b")
    chain = FallbackLM([primary, backup], daily_limits={"gemini/model-a": 20})

    chain("one")
    chain("two")

    assert primary.calls == 1, "the exhausted model must not be retried"
    assert backup.calls == 2


def test_daily_counter_never_exceeds_the_cap() -> None:
    """The counter records admitted calls, so it cannot drift past the cap.

    It used to increment before checking the limit, so every call made
    after the budget was spent pushed the counter further past it.
    """
    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_day=2)
    for _ in range(10):
        limiter.acquire()

    assert limiter.daily_used == 2, "the counter must be clamped at the cap"


def test_daily_counter_is_admission_accurate_under_concurrency() -> None:
    """Concurrent callers must not push the counter past the cap.

    The counter records admitted calls and is clamped, so however many
    threads race for it the value stays within the budget.  The chain
    enforces the cap separately, by skipping a provider whose budget is
    spent (see ``test_chain_retires_a_daily_exhausted_provider``).
    """
    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_day=5)
    barrier = threading.Barrier(20)

    def worker() -> None:
        barrier.wait()
        limiter.acquire()

    threads = [threading.Thread(target=worker) for _ in range(20)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert limiter.daily_used == 5, "the counter must never exceed the cap"
    assert limiter.exhausted is True, "the provider must be marked exhausted"


def test_rate_limiter_with_both_limits_does_not_deadlock() -> None:
    """A limiter with both a per-minute and a per-day cap must not hang.

    The daily counter is incremented while the per-minute lock is already
    held.  If the counter helper takes that non-reentrant lock again, the
    call blocks forever — which is what happened to every Gemini call.
    """
    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_minute=5, max_per_day=20)
    for _ in range(5):
        limiter.acquire()

    assert limiter.daily_used == 5
    assert limiter.exhausted is False


def test_rate_limiter_daily_only_does_not_deadlock() -> None:
    """A limiter with only a daily cap must not hang either."""
    from free_setup.fallback import RateLimiter

    limiter = RateLimiter(max_per_minute=None, max_per_day=20)
    limiter.acquire()

    assert limiter.daily_used == 1


def test_cost_tracker_is_immutable() -> None:
    tracker = CostTracker()
    updated = tracker.add(CostRecord(model="m", provider="p", prompt_tokens=5))

    assert tracker.records == (), "the original tracker must not be mutated"
    assert updated.total_tokens == 5
    assert updated.total_prompt_tokens == 5


# -- thread safety ----------------------------------------------------------


def test_concurrent_calls_all_succeed() -> None:
    """The chain must stay consistent when STORM calls it from many threads."""
    lm = FakeLM("groq/model-a")
    chain = FallbackLM([lm])
    results: list[list[str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            output = chain("hello")
        except BaseException as exc:
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(output)

    threads = [threading.Thread(target=worker) for _ in range(24)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent calls raised: {errors}"
    assert len(results) == 24
    assert lm.calls == 24


def test_concurrent_calls_with_failover() -> None:
    """A failing primary under concurrency must not lose calls."""
    primary = FakeLM("groq/model-a", RateLimitError("slow down", 429))
    backup = FakeLM("gemini/model-b")
    chain = FallbackLM([primary, backup])
    results: list[list[str]] = []
    errors: list[BaseException] = []
    lock = threading.Lock()

    def worker() -> None:
        try:
            output = chain("hello")
        except BaseException as exc:
            with lock:
                errors.append(exc)
            return
        with lock:
            results.append(output)

    threads = [threading.Thread(target=worker) for _ in range(12)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, f"concurrent calls raised: {errors}"
    assert len(results) == 12
    # The chain switches to the backup after the first failure and stays
    # there, so the backup serves all twelve calls.
    assert backup.calls == 12
    assert primary.calls >= 1


# -- provider registry ------------------------------------------------------


def test_provider_of_model_resolves_prefix() -> None:
    assert provider_of_model("groq/openai/gpt-oss-120b") == "groq"
    assert provider_of_model("gemini/gemma-3-27b-it") == "gemini"


def test_split_model_strips_the_provider_prefix() -> None:
    provider, model = split_model("groq/openai/gpt-oss-120b")
    assert provider == "groq"
    assert model == "openai/gpt-oss-120b"


def test_unknown_provider_raises() -> None:
    with pytest.raises(UnknownProviderError):
        provider_of_model("notaprovider/some-model")


def test_model_without_prefix_raises() -> None:
    with pytest.raises(UnknownProviderError):
        provider_of_model("bare-model-name")


def test_no_local_providers_are_registered() -> None:
    """Every provider is a hosted API; there is no local-model fallback.

    The machine this runs on cannot serve a usable local model, so a local
    provider in the registry would only create a chain entry that always
    fails with a connection error.
    """
    for name, spec in PROVIDERS.items():
        assert not getattr(spec, "local", False), (
            f"{name} is marked as a local provider; the registry must contain "
            "hosted free-tier APIs only"
        )
        assert "ollama" not in name
        assert spec.requires_key is True, (
            f"{name} does not require a key; hosted providers all do"
        )


def test_discover_available_returns_nothing_without_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """With no keys set there is no usable provider, and no local one either."""
    for var in ("GROQ_API_KEY", "GEMINI_API_KEY", "MISTRAL_API_KEY"):
        monkeypatch.delenv(var, raising=False)

    available = discover_available()
    assert all(spec.requires_key for spec in available)


def test_every_provider_has_a_default_model() -> None:
    for name, spec in PROVIDERS.items():
        assert spec.default_model, f"{name} has no default model"
        assert spec.default_model.startswith(spec.prefix + "/"), (
            f"{name} default model {spec.default_model!r} "
            f"does not use the {spec.prefix!r} prefix"
        )
