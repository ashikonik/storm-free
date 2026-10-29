"""Tests for the diagnostics in :mod:`free_setup.doctor`.

The probe is what a user runs when something is not working, so it has to be
honest: a provider whose quota is spent must not be reported as healthy.

Run with:  ./venv/bin/python -m pytest tests/test_doctor.py -v
"""

from __future__ import annotations

from typing import Any

import pytest

from free_setup.doctor import (
    ProbeResult,
    Status,
    _status_from_exception,
    render_results,
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


# -- status mapping ---------------------------------------------------------


def test_auth_error_maps_to_auth_error() -> None:
    status, _ = _status_from_exception(AuthenticationError("bad key", 401))
    assert status is Status.AUTH_ERROR


def test_per_minute_limit_maps_to_rate_limited() -> None:
    exc = ValueError("Generate content requests per minute limit exceeded.")
    status, _ = _status_from_exception(exc)
    assert status is Status.RATE_LIMITED


def test_daily_limit_maps_to_quota_exhausted() -> None:
    """A daily quota is a distinct condition from a per-minute one.

    It will not clear by waiting, so the two must not share a status.
    """
    exc = ValueError(
        "Quota exceeded for metric: generate_content_free_tier_requests, "
        "description: Generate content requests per day limit exceeded."
    )
    status, detail = _status_from_exception(exc)
    assert status is Status.QUOTA_EXHAUSTED
    assert "daily" in detail.lower()


def test_network_failure_maps_to_unreachable() -> None:
    status, _ = _status_from_exception(ConnectionError("no route"))
    assert status is Status.UNREACHABLE


# -- probe behaviour --------------------------------------------------------


def test_probe_bypasses_the_disk_cache(monkeypatch: pytest.MonkeyPatch) -> None:
    """The probe must hit the provider, not the cache.

    Upstream's ``LitellmModel`` defaults to ``cache=True`` and reads a disk
    cache under ``~/.storm_local_cache``.  A probe that hits the cache
    reports "ok" even when the provider's quota is spent, which is exactly
    the situation the user needs to be told about.
    """
    from free_setup import doctor

    captured: dict[str, Any] = {}

    class FakeLM:
        cache = True
        model = "gemini/gemini-2.5-flash"

        def __call__(self, prompt=None, messages=None, **kwargs):
            captured["cache"] = self.cache
            return ["ok"]

    class FakeSpec:
        name = "gemini"
        default_model = "gemini/gemini-2.5-flash"
        requires_key = True
        env_vars = ("GEMINI_API_KEY",)

        def has_key(self) -> bool:
            return True

    monkeypatch.setattr(
        "free_setup.runner.build_lm", lambda *a, **k: FakeLM(), raising=True
    )
    monkeypatch.setitem(
        __import__("os").environ, "GEMINI_API_KEY", "dummy-key-for-the-test"
    )

    result = doctor.probe_provider(FakeSpec())

    assert result.status is Status.OK
    assert captured["cache"] is False, "the probe must not read a cached reply"


def test_probe_leaves_room_for_a_reasoning_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The probe budget must exceed what a reasoning model spends thinking.

    Gemini 3 splits the token budget between reasoning and the answer.  At
    16 tokens it spends the whole budget thinking and returns no content,
    which the probe would report as an empty -- and therefore broken --
    provider even though the key is perfectly good.
    """
    from free_setup import doctor

    captured: dict[str, Any] = {}

    class FakeLM:
        cache = False
        model = "gemini/gemini-3-flash-preview"

        def __call__(self, prompt=None, messages=None, **kwargs):
            captured.update(kwargs)
            return ["ok"]

    class FakeSpec:
        name = "gemini"
        default_model = "gemini/gemini-3-flash-preview"
        requires_key = True
        env_vars = ("GEMINI_API_KEY",)

        def has_key(self) -> bool:
            return True

    monkeypatch.setattr(
        "free_setup.runner.build_lm", lambda *a, **k: FakeLM(), raising=True
    )
    monkeypatch.setitem(
        __import__("os").environ, "GEMINI_API_KEY", "dummy-key-for-the-test"
    )

    result = doctor.probe_provider(FakeSpec())

    assert result.status is Status.OK
    assert captured["max_tokens"] >= 64


def test_probe_reports_a_missing_key_without_calling_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A provider with no key must be reported, not probed."""
    from free_setup import doctor

    class FakeSpec:
        name = "groq"
        default_model = "groq/openai/gpt-oss-120b"
        requires_key = True
        env_vars = ("GROQ_API_KEY",)

        def has_key(self) -> bool:
            return False

    monkeypatch.delenv("GROQ_API_KEY", raising=False)

    result = doctor.probe_provider(FakeSpec())

    assert result.status is Status.NO_KEY
    assert "GROQ_API_KEY" in result.detail


# -- rendering --------------------------------------------------------------


def test_render_includes_every_status() -> None:
    results = [
        ProbeResult("gemini", "gemini/gemini-2.5-flash", Status.OK, "ok", 12),
        ProbeResult(
            "groq",
            "groq/openai/gpt-oss-120b",
            Status.QUOTA_EXHAUSTED,
            "daily quota exhausted",
            900,
        ),
        ProbeResult(
            "mistral", "mistral/ministral-3b-latest", Status.NO_KEY, "set key", None
        ),
    ]

    rendered = render_results(results)

    assert "gemini" in rendered
    assert "groq" in rendered
    assert "mistral" in rendered
    assert "daily quota exhausted" in rendered


def test_render_handles_an_empty_result_list() -> None:
    assert render_results([]) == "No providers to probe."


def test_only_ok_statuses_count_as_usable() -> None:
    ok = ProbeResult("a", "m", Status.OK)
    quota = ProbeResult("b", "m", Status.QUOTA_EXHAUSTED)
    rate = ProbeResult("c", "m", Status.RATE_LIMITED)

    assert ok.usable is True
    assert quota.usable is False
    assert rate.usable is False
