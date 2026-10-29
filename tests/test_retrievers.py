"""Tests for the retriever wrapper.

The retriever is the one part of the pipeline that needs no API key, so a
failure here shows up as an article with no sources rather than as an
error.  These tests cover the result normalisation, the source filter and
the failure handling, without making a network call.

Run with:  ./venv/bin/python -m pytest tests/test_retrievers.py -v
"""

from __future__ import annotations

from typing import Any

import pytest

from free_setup.retrievers import DEFAULT_BACKEND, get_duckduckgo_retriever


class StubDDGS:
    """A stand-in for the ``ddgs`` search client.

    ``ddgs`` is constructed by the retriever and used through its ``text``
    method, so that is what the stub implements.
    """

    def __init__(self, results: Any = None, error: BaseException | None = None) -> None:
        self._results = results if results is not None else []
        self._error = error
        self.queries: list[str] = []

    def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
        """Return the canned results for this query."""
        self.queries.append(query)
        if self._error is not None:
            raise self._error
        return self._results


def _retriever_with(
    results: Any = None,
    error: BaseException | None = None,
) -> tuple[Any, StubDDGS]:
    """Build a retriever whose search client is a stub."""
    retriever = get_duckduckgo_retriever()
    stub = StubDDGS(results, error)
    retriever.ddgs = stub
    return retriever, stub


# -- backend -----------------------------------------------------------------


def test_default_backend_is_auto() -> None:
    """The named backends return nothing useful today.

    Upstream hardcodes ``backend="api"``, which now proxies through Bing
    and returns unrelated pages instead of search results.
    """
    assert DEFAULT_BACKEND == "auto"


def test_retriever_can_be_built() -> None:
    """The retriever must construct without a network call."""
    retriever = get_duckduckgo_retriever()

    assert retriever is not None
    assert hasattr(retriever, "forward")
    assert retriever.duck_duck_go_backend == DEFAULT_BACKEND


# -- result normalisation ----------------------------------------------------


def test_results_are_normalised_to_upstream_shape() -> None:
    """Results must carry the keys upstream expects."""
    retriever, _ = _retriever_with(
        results=[
            {
                "href": "https://example.com/a",
                "title": "A page",
                "body": "Some body text.",
            }
        ]
    )

    results = retriever.forward("a query")

    assert len(results) == 1
    assert results[0]["url"] == "https://example.com/a"
    assert results[0]["title"] == "A page"
    assert results[0]["description"] == "Some body text."
    assert results[0]["snippets"] == ["Some body text."]


def test_a_url_key_is_accepted_too() -> None:
    """Some backends report the URL under ``url`` rather than ``href``."""
    retriever, _ = _retriever_with(
        results=[
            {
                "url": "https://example.com/b",
                "title": "B page",
                "description": "Body.",
            }
        ]
    )

    results = retriever.forward("a query")

    assert results[0]["url"] == "https://example.com/b"


def test_incomplete_results_are_skipped() -> None:
    """A result without a URL or a title is not usable."""
    retriever, _ = _retriever_with(
        results=[
            {"title": "No URL here"},
            {"href": "https://example.com/c"},
            {"href": "https://example.com/d", "title": "Fine"},
        ]
    )

    results = retriever.forward("a query")

    assert [r["url"] for r in results] == ["https://example.com/d"]


def test_excluded_urls_are_dropped() -> None:
    """A URL the caller has already seen must not be returned again."""
    retriever, _ = _retriever_with(
        results=[
            {"href": "https://example.com/e", "title": "Seen"},
            {"href": "https://example.com/f", "title": "New"},
        ]
    )

    results = retriever.forward("a query", exclude_urls=["https://example.com/e"])

    assert [r["url"] for r in results] == ["https://example.com/f"]


def test_the_source_filter_is_applied() -> None:
    """A URL the filter rejects must not be returned."""
    retriever, _ = _retriever_with(
        results=[
            {"href": "https://example.com/g", "title": "Rejected"},
            {"href": "https://example.com/h", "title": "Accepted"},
        ]
    )
    retriever.is_valid_source = lambda url: url.endswith("/h")

    results = retriever.forward("a query")

    assert [r["url"] for r in results] == ["https://example.com/h"]


# -- failure handling --------------------------------------------------------


def test_a_failed_search_does_not_raise(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A search failure must not take the whole run down.

    The pipeline treats an empty result set as "nothing found" and carries
    on; raising here would abort the article instead.
    """
    retriever, _ = _retriever_with(error=ConnectionError("network is down"))

    with caplog.at_level("WARNING"):
        results = retriever.forward("a query")

    assert results == []


def test_a_failed_search_is_logged_without_the_url(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The log must not carry the search URL, which can leak the query."""
    retriever, _ = _retriever_with(error=ConnectionError("https://internal/x"))

    with caplog.at_level("WARNING"):
        retriever.forward("a query")

    assert any("Search failed" in record.message for record in caplog.records)
    assert "internal/x" not in caplog.text


def test_a_failed_search_is_retried() -> None:
    """A transport failure is worth another attempt."""
    retriever, stub = _retriever_with(error=ConnectionError("flaky"))
    retriever.max_attempts = 3
    retriever.retry_delay = 0.0

    results = retriever.forward("a query")

    assert results == []
    assert len(stub.queries) == 3, "the search must be retried"


def test_one_failed_query_does_not_lose_the_others() -> None:
    """A failure on one query must not discard the results of another."""
    retriever = get_duckduckgo_retriever()

    calls = {"n": 0}

    class AlternatingStub:
        def text(self, query: str, **kwargs: Any) -> list[dict[str, Any]]:
            calls["n"] += 1
            if calls["n"] == 1:
                raise ConnectionError("flaky")
            return [{"href": "https://example.com/i", "title": "Recovered"}]

    retriever.ddgs = AlternatingStub()
    retriever.max_attempts = 1

    results = retriever.forward(["first query", "second query"])

    assert [r["url"] for r in results] == ["https://example.com/i"]


# -- usage accounting --------------------------------------------------------


def test_usage_counts_the_queries() -> None:
    """The runner reports how many searches were made."""
    retriever, _ = _retriever_with(results=[])

    retriever.forward(["one", "two", "three"])

    usage = retriever.get_usage_and_reset()
    assert usage["DuckDuckGoRM"] == 3


def test_usage_is_reset_after_reading() -> None:
    """Reading the usage must clear it, as upstream expects."""
    retriever, _ = _retriever_with(results=[])

    retriever.forward("one")
    retriever.get_usage_and_reset()

    assert retriever.get_usage_and_reset()["DuckDuckGoRM"] == 0
