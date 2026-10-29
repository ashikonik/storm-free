"""Retriever wrappers that work with the current free search backends.

Upstream STORM's ``DuckDuckGoSearchRM`` hardcodes ``backend="api"``.  The
``duckduckgo_search`` package it imports has since been renamed to ``ddgs``,
and its ``"api"`` backend is deprecated: it now proxies through Bing and
returns unrelated pages (Google home pages, ad landing pages) instead of
search results.  With ``backend="auto"`` the current ``ddgs`` package
returns real results.

Rather than patching the upstream clone (so ``git pull`` stays clean), we
subclass the upstream retriever and override just the two things that broke:
the search client and the backend.  Everything else -- the result
normalisation, the ``WebPageHelper`` snippet fetching and the usage
accounting -- is inherited unchanged.
"""

from __future__ import annotations

import logging
import threading
import time
from typing import Any

logger = logging.getLogger(__name__)

# Errors worth retrying: a transport failure or the backend rate-limiting
# itself.  Anything else is a bug and must surface rather than be retried
# into the ground.
_RETRYABLE_SEARCH_ERRORS = (ConnectionError, TimeoutError, OSError, ValueError)

# The backend that actually returns usable results today.  The named
# backends are rate-limited to the point of returning nothing, so ``"auto"``
# (which lets ``ddgs`` pick a working engine) is the only reliable choice.
DEFAULT_BACKEND = "auto"


def _load_ddgs() -> Any:
    """Import the ``ddgs`` search client, preferring it over the old name.

    ``ddgs`` is the current name for the project that used to be called
    ``duckduckgo_search``.  The old package still installs and still works
    as an import shim, but it warns on import and its bundled backend
    selection is stale, so we try ``ddgs`` first and fall back.
    """
    try:
        from ddgs import DDGS

        return DDGS
    except ImportError:
        # The old package exports a different class object under the same
        # name, so the import is deliberately re-bound.
        from duckduckgo_search import DDGS as LegacyDDGS

        return LegacyDDGS


def _make_duckduckgo_class() -> Any:
    """Build the retriever class.

    ``dspy.Retrieve`` provides the ``__call__`` -> ``forward`` wiring and the
    ``dump_state``/``load_state`` hooks that STORM's runner expects, so the
    class has to inherit from it.  The import is deferred because ``dspy``
    lives inside the upstream ``storm/`` clone and is only importable once
    that directory is on ``sys.path``.
    """
    import dspy

    class DuckDuckGoSearchRM(dspy.Retrieve):
        """Search DuckDuckGo for pages relevant to a query.

        Drop-in replacement for upstream's ``DuckDuckGoSearchRM`` that uses a
        search backend which still returns real results.
        """

        def __init__(
            self,
            k: int = 3,
            is_valid_source: Any = None,
            min_char_count: int = 150,
            snippet_chunk_size: int = 1000,
            webpage_helper_max_threads: int = 10,
            safe_search: str = "On",
            region: str = "us-en",
            backend: str = DEFAULT_BACKEND,
            max_attempts: int = 3,
            retry_delay: float = 2.0,
        ) -> None:
            from knowledge_storm import rm as rm_module

            super().__init__(k=k)
            self.k = k
            self.max_attempts = max_attempts
            self.retry_delay = retry_delay
            self.webpage_helper = rm_module.WebPageHelper(
                min_char_count=min_char_count,
                snippet_chunk_size=snippet_chunk_size,
                max_thread_num=webpage_helper_max_threads,
            )
            self.usage = 0
            self._usage_lock = threading.Lock()
            self.duck_duck_go_backend = backend
            self.duck_duck_go_safe_search = safe_search
            self.duck_duck_go_region = region
            self.is_valid_source = is_valid_source or (lambda _url: True)

            ddgs_cls = _load_ddgs()
            self.ddgs = ddgs_cls()

        def get_usage_and_reset(self) -> dict[str, int]:
            with self._usage_lock:
                usage = self.usage
                self.usage = 0
            return {"DuckDuckGoRM": usage}

        def request(self, query: str) -> list[dict[str, Any]]:
            """Run one search and return the raw result dicts.

            The search backend intermittently returns an empty list instead of
            raising (it rate-limits itself), so an empty result is retried
            with a short backoff rather than being treated as "nothing found".
            """
            last_error: Exception | None = None
            for attempt in range(self.max_attempts):
                try:
                    results = self.ddgs.text(
                        query,
                        max_results=self.k,
                        backend=self.duck_duck_go_backend,
                    )
                except _RETRYABLE_SEARCH_ERRORS as exc:
                    # A transport or rate-limit failure: worth another go.
                    last_error = exc
                    results = []
                except Exception:
                    # Anything else is a bug, not a transient failure, so it
                    # must not be hidden behind a retry.
                    raise

                if results:
                    return results

                logger.debug(
                    "Search for %r returned nothing (attempt %d/%d).",
                    query,
                    attempt + 1,
                    self.max_attempts,
                )
                if attempt < self.max_attempts - 1:
                    time.sleep(self.retry_delay * (attempt + 1))

            if last_error is not None:
                raise last_error
            return []

        def forward(
            self,
            query_or_queries: str | list[str],
            exclude_urls: list[str] | None = None,
            **kwargs: Any,
        ) -> list[dict[str, Any]]:
            """Search for each query and return normalised result dicts.

            Mirrors upstream's output shape: ``url``, ``title``,
            ``description`` and ``snippets``.
            """
            exclude_urls = exclude_urls or []
            queries = (
                [query_or_queries]
                if isinstance(query_or_queries, str)
                else list(query_or_queries)
            )
            with self._usage_lock:
                self.usage += len(queries)

            collected: list[dict[str, Any]] = []
            for query in queries:
                try:
                    results = self.request(query)
                except Exception as exc:
                    # Log the exception type but not its text: a search
                    # client's message can carry the request URL.
                    logger.warning(
                        "Search failed for %r: %s", query, type(exc).__name__
                    )
                    continue

                for d in results:
                    if not isinstance(d, dict):
                        logger.debug("Ignoring non-dict search result: %r", d)
                        continue
                    url = d.get("href") or d.get("url")
                    title = d.get("title")
                    description = d.get("description") or d.get("body") or title
                    body = d.get("body") or description
                    if not url or not title:
                        logger.debug("Skipping incomplete search result: %r", d)
                        continue
                    if self.is_valid_source(url) and url not in exclude_urls:
                        collected.append(
                            {
                                "url": url,
                                "title": title,
                                "description": description,
                                "snippets": [body],
                            }
                        )
            return collected

    return DuckDuckGoSearchRM


def get_duckduckgo_retriever(**kwargs: Any) -> Any:
    """Instantiate the patched DuckDuckGo retriever."""
    return _make_duckduckgo_class()(**kwargs)


__all__ = ["DEFAULT_BACKEND", "get_duckduckgo_retriever"]
