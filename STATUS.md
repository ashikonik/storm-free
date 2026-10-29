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
- [x] **Token budgets that leave room for a whole section** — upstream's
      `article_gen` budget of 700 tokens cannot hold a Wikipedia section;
      the request comes back cut off mid-sentence. Writing stages now get
      at least 1400 tokens, and reasoning models get four times the
      nominal budget because they spend part of it thinking first.
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
- [x] **CI/CD pipeline** — GitHub Actions on every push and pull request:
      `lint` (ruff check + format), `test` (pytest against a fresh clone of
      upstream STORM), `mypy`, and `secret-scan`.
- [x] **Nightly provider probe** — a scheduled workflow runs `--doctor`
      against the configured providers and publishes the result to the job
      summary. It skips cleanly when no provider keys are set.
- [x] **Published** — `github.com/ashikonik/storm-free`, public.
- [x] **CI green** — run `40852466979`: all four jobs passed (lint, test,
      mypy, secret-scan).

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

- [ ] **More provider keys** — 19 of the 22 registered providers still have
      no key, so only groq, gemini and mistral are exercised in practice.
      The three that are configured but broken are listed under *Blocked*.
- [ ] **Nightly probe result** — the first probe with keys is still running;
      check the job summary for the current state of each provider.
- [ ] **Re-verify the full run** — the article pipeline was last verified end
      to end with the old Gemini model ids. The chains have been updated to
      the ids that answer today, but a fresh full run is still owed.

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
| groq | OK | `qwen/qwen3.8-27b` answers in ~300–1000 ms. The `gpt-oss-*` models answer with an **empty body** at low token budgets, so they are not in any stage chain. |
| mistral | OK | `ministral-3b-latest` answers in ~700–1100 ms. |
| gemini | OK | `gemini-3-flash-preview` and `gemini-flash-lite-latest` answer in ~1.2–1.6 s. The daily allowance is 20 requests/day/model. |
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
| `gemini/gemini-2.5-flash`, `-lite` | Google retired these for new accounts; the API answers `404 "no longer available to new users"`. |
| `sambanova/*` | 403 on the published ids. |
| `together_ai/*` | The key is rejected. |
| `openrouter/*` | The key's limit is exhausted. |

They stay in `FREE_MODELS` so `--list-providers` shows them, and they will
work again if the underlying problem is fixed.

## Free-tier rot

Free tiers retire models without warning.  The nightly probe is what
catches it, and it has already paid for itself twice:

| Date | Model | What happened |
|---|---|---|
| 2026-10-29 | `groq/openai/gpt-oss-120b` | Answers with an empty body at low token budgets. |
| 2026-10-29 | `gemini/gemini-2.5-flash` | `404`: *"no longer available to new users"*. Google points new accounts at `gemini-3.8-flash`. |
| 2026-10-29 | `gemini/gemini-3-flash-preview` | Works, but only with a token budget above ~16: it spends part of the budget reasoning and answers with nothing below that. The probe budget was raised from 16 to 64 tokens. |
| 2026-10-29 | every model | Upstream's `article_gen` budget of 700 tokens is too small for a whole section — the article came back cut off mid-sentence ("Johannes Gensfleisch zur Laden zum Gutenberg (c."). Writing stages now get at least 1400 tokens, and reasoning models four times that. |

## Notes

- **The daily quota is the binding constraint**, not the rate limit. Gemini
  allows 20 requests/day/model and a full run needs 60–80 calls, which is
  why every stage has a three-provider chain and why the run log exists.
- **A run is cheaper in stages.** `--do-research --do-generate-outline`
  followed by the remaining stages reuses the cached research instead of
  paying for it twice.
