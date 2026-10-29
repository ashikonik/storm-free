# Project Status

Last updated: 2026-10-29

This file is the durable record of what has been achieved and what is still
open. Per-run logs live under `logs/runs/` and are **not** committed — they
are the detail behind the summary here.

## Completed

- [x] **Provider registry** — 22 free-tier providers, every model id verified
      to resolve through LiteLLM (`tests/test_providers.py`).
- [x] **Fallback chain** — `FallbackLM` with sticky provider selection and
      narrow retry: 429/5xx/network advance the chain, 401/403 raise at once.
- [x] **Request timeout and retry cap** — `build_lm()` sets
      `timeout=60` and `num_retries=1`. LiteLLM's own default is 6000 s,
      which let one stalled provider hold a run for over an hour.
- [x] **CLI** — `--doctor`, `--list-providers`, `--dry-run`, per-stage flags,
      `--provider`, `--model`, `--use-cache`, `--verbose`.
- [x] **Packaging** — `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `LICENSE`,
      `setup.sh`, `secrets.toml.example`.
- [x] **Repaired outline parser** — upstream's `from_outline_str` raises a
      bare `IndexError` when an outline opens with the article title and
      later contains another top-level heading. Fixed at runtime by
      `free_setup/outline_patch.py`; upstream is not modified.
- [x] **Greedy-sampling recovery** — Mistral intermittently refuses a request
      whose sampling parameters its backend has disallowed. The request is
      resent with `top_p=1` instead of abandoning the provider.
- [x] **Per-run logs** — `--log-file` / `--no-log`; the log records the
      command, the resolved chains, every provider decision and the summary.
- [x] **Progress tracking** — this file, plus `logs/runs/` (gitignored).
- [x] **Test suite** — 223 tests, no network access required.
- [x] **Formatting and typing** — `ruff format` and `ruff check` are the
      project standard; `mypy.ini` configures the type checker.

## Verified end to end

- [x] **Full article run** — produced a five-section article with 73
      citations and 10 references (`outputs/history_of_the_printing_press/`).
- [x] **Fallback in production** — Gemini exhausted its daily quota
      mid-run and Groq took over; Mistral served the polish stage.
- [x] **Fallback on a broken primary** — a deliberately rate-limited
      primary advanced the chain three times, exactly as designed.
- [x] **Doctor** — 23 probes in ~23 s (was ~20 minutes before the timeout
      fix).
- [x] **Streamlit demo** — serves HTTP 200 and builds a `STORMWikiRunner`
      with all five fallback chains configured.

## Ongoing

- [ ] **CI/CD pipeline** — GitHub Actions workflows (lint, test, mypy,
      secret scan) and the nightly provider probe.
- [ ] **Publish the repository** — push to `github.com/ashikonik/storm-free`.
- [ ] **Nightly probe secrets** — the workflow needs repository secrets
      configured to be useful.

## Blocked

These need action outside the repository:

- [ ] **together_ai** — the API rejects the key as invalid.
- [ ] **openrouter** — the key carries a $0.00 total limit that does not
      reset, so every request is refused.
- [ ] **sambanova** — the published model ids return 403, and the rest are
      rate-limited with a "high demand" message.

## Provider availability

Probed 2026-10-29 with `--doctor`. "Quota" means the key is valid but the
free tier's daily allowance is spent; it resets on a daily cycle.

| Provider | Status | Detail |
|---|---|---|
| groq | OK | `qwen/qwen3.8-27b` answers in ~390 ms. The `gpt-oss-*` models answer with an **empty body** at low token budgets, so they are not in any stage chain. |
| mistral | OK | `ministral-3b-latest` answers in ~940 ms. |
| gemini | Quota | Both models work; the daily allowance (20 requests/day/model) is spent. A run needs ~60–80 calls, so a run spreads across providers. |
| together_ai | Broken | Key rejected as invalid. |
| openrouter | Broken | Key has a $0.00 total limit that does not reset. |
| sambanova | Broken | Published model ids return 403; others report high demand. |
| duckduckgo | OK | The retriever needs no API key. |

The remaining 16 providers are registered and their model ids are verified
against LiteLLM, but no key is configured for them yet.

## Models deliberately not in any chain

| Model | Why |
|---|---|
| `groq/openai/gpt-oss-120b`, `-20b` | Return an empty body at low token budgets — they would silently produce empty article sections. |
| `sambanova/*` | 403 on the published ids. |
| `together_ai/*` | The key is rejected. |
| `openrouter/*` | The key's limit is exhausted. |

They stay in `FREE_MODELS` so `--list-providers` shows them, and they will
work again if the underlying problem is fixed.

## Notes

- **The daily quota is the binding constraint**, not the rate limit. Gemini
  allows 20 requests/day/model and a full run needs 60–80 calls, which is
  why every stage has a three-provider chain and why the run log exists.
- **A run is cheaper in stages.** `--do-research --do-generate-outline`
  followed by the remaining stages reuses the cached research instead of
  paying for it twice.
