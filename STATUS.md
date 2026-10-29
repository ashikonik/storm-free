# Project Status

Last updated: 2026-10-29

This file is the durable record of what has been achieved and what is still
open. Per-run logs live under `logs/runs/` and are **not** committed — they
are the detail behind the summary here.

## Completed

- [x] **Provider registry** — 22 free-tier providers, every model id verified
      to resolve through LiteLLM (`tests/test_providers.py`).
- [x] **Fallback chain** — `FallbackLM` with sticky provider selection and
      three-way error classification: transient errors advance the chain,
      provider-scoped errors (401/403, 404) skip to the next provider, and
      request-scoped errors (400, context overflow, content policy) raise at
      once.
- [x] **Request timeout and retry cap** — `build_lm()` sets
      `timeout=60` and `num_retries=1`. LiteLLM's own default is 6000 s,
      which let one stalled provider hold a run for over an hour.
- [x] **Token budgets that leave room for a whole section** — upstream's
      `article_gen` budget of 700 tokens cannot hold a Wikipedia section;
      the request comes back cut off mid-sentence. Writing stages now get
      at least 1400 tokens, and reasoning models get four times the
      nominal budget because they spend part of it thinking first.
- [x] **CLI** — `--doctor`, `--list-providers`, `--dry-run`, per-stage flags,
      `--provider`, `--model`, `--use-cache`, `--verbose`, and the rest. All
      24 flags are documented in `README.md`, and `tests/test_docs.py` fails
      the build if the two drift apart.
- [x] **Packaging** — `README.md`, `CLAUDE.md`, `CONTRIBUTING.md`, `LICENSE`,
      `CHANGELOG.md`, `SECURITY.md`, `CODE_OF_CONDUCT.md`, `setup.sh`,
      `secrets.toml.example`, and the GitHub issue and pull-request
      templates.
- [x] **Repaired outline parser** — upstream's `from_outline_str` raises a
      bare `IndexError` when an outline opens with the article title and
      later contains another top-level heading. Fixed at runtime by
      `free_setup/outline_patch.py`; upstream is not modified.
- [x] **Greedy-sampling recovery** — Mistral intermittently refuses a request
      whose sampling parameters its backend has disallowed. The request is
      resent with `top_p=1` instead of abandoning the provider, and a
      second refusal is converted into a `GreedySamplingError` so the chain
      moves on instead of aborting the run.
- [x] **Per-run logs** — `--log-file` / `--no-log`; the log records the
      command, the resolved chains, every provider decision, which provider
      served each request, the token usage and the per-stage timings.
- [x] **LLM call history** — written to the wrapper's history, which is the
      list upstream collects, so `llm_call_history.jsonl` is no longer empty.
- [x] **Progress tracking** — this file, plus `logs/runs/` (gitignored).
- [x] **Test suite** — 321 tests, no network access required.
- [x] **Formatting and typing** — `ruff format` and `ruff check` are the
      project standard; `mypy.ini` configures the type checker.
- [x] **CI/CD pipeline** — GitHub Actions on every push and pull request:
      `lint` (ruff check + format), `test` (pytest against a fresh clone of
      upstream STORM), `cli` (the entry point, as a subprocess), `mypy`,
      and `secret-scan`.
- [x] **Nightly provider probe** — a scheduled workflow runs `--doctor`
      against the configured providers and publishes the result to the job
      summary. It skips cleanly when no provider keys are set. Secrets are
      passed through the environment rather than interpolated into shell
      commands, and the published summary is filtered and redacted.
- [x] **Pinned upstream** — `setup.sh` and both CI workflows clone
      `stanford-oval/storm` at the same commit, so the tested tree is the
      shipped tree.
- [x] **Published** — `github.com/ashikonik/storm-free`, public.
- [x] **CI green** — run `40856789855`: all four jobs passed (lint, test,
      mypy, secret-scan).

## Fixed in 1.0.1

The release notes are in `CHANGELOG.md`. The five findings that made the
project's headline guarantee untrue are worth restating here, because the
guarantee is what the project exists for:

1. **A provider's greedy-sampling quirk could abort the whole chain.** The
   recovery retry was not itself guarded, so a second refusal escaped as a
   fatal `BadRequestError` and every remaining provider was skipped. The
   committed production run log shows it: a Mistral error ended a run with
   Groq and Gemini still queued.
2. **A provider-level fatal error killed the stage even with healthy
   providers queued behind it.** A 401 on the primary meant providers 2
   and 3 were never tried.
3. **The daily quota counter counted attempts rather than admitted calls,
   and was never clamped.** With `max_per_day=10` and fifty threads the
   counter reached fifty.
4. **The run log carried no token counts and no timings.** It was built
   from `runner.summary()`, but upstream's `Engine.summary()` prints and
   returns `None`, so the section was never written. The token counts
   needed a second fix of their own: the tracker was reset by upstream
   when it collected the usage, which happens before the log is written,
   so the summary read zero. The running totals now live in a record the
   reset cannot reach.
5. **`llm_call_history.jsonl` was always empty.** The history went to the
   inner model; upstream reads the wrapper's list.

Two further bugs were found while writing the release: `--model` silently
discarded the whole fallback chain, and a topic of `..` wrote the article
outside the output directory.

A third was found by CI itself, on a machine with no API keys configured:
`--dry-run` dropped every provider that had no key, so it failed with
"No available provider" before it could print anything. That is exactly
what a dry run is for — showing someone who has not set a key up yet
what *would* happen — so the key requirement is now lifted for a dry
run only. A real run still fails with the message telling the user which
key to add.

## Verified end to end

- [x] **Full article run** — produced a complete 32,226-byte article: 7
      top-level sections, 22 subsections, 11 further subsections, 67 inline
      citation markers and 17 distinct cited sources
      (`outputs/history_of_the_printing_press/`). The run is spread across
      three providers and the article ends on a full sentence.
- [x] **Fallback in production** — Gemini exhausted its daily quota
      mid-run; Groq took over the article stage and Mistral served both the
      article body and the polish stage. The token summary shows all three
      models contributing to one article.
- [x] **Fallback on a broken primary** — a deliberately rate-limited
      primary advanced the chain three times, exactly as designed.
- [x] **Doctor** — 23 probes in ~23 s (was ~20 minutes before the timeout
      fix).
- [x] **The call history is written** — after the 1.0.1 fix, a live
      research run produced a 70 KB `llm_call_history.jsonl` with 18
      recorded calls. Before the fix the file was always 0 bytes, because
      the history went to the inner model while upstream reads the
      wrapper's.
- [x] **The run log reports the tokens a run cost** — a live research run
      reported 14,743 tokens across three providers (Gemini 6,670, Mistral
      6,602, Groq 1,471) over 24 calls, together with the per-stage
      timings. Before the fix every model reported 0 prompt + 0
      completion: the wrapper's tracker was reset by upstream when it
      collected the usage, before the log was written.
- [x] **Streamlit demo** — serves HTTP 200 and builds a `STORMWikiRunner`
      with all five fallback chains configured.

## Ongoing

- [ ] **More provider keys** — 16 of the 22 registered providers have no
      key, so only groq, gemini and mistral are exercised in practice, and
      one of those three is currently out of quota. The three that are
      configured but broken are listed under *Blocked*.
- [ ] **Gemini's daily quota** — 20 requests/day/model. A full run needs
      60–80 calls, so Gemini alone can never finish a run; that is what
      the fallback chain is for.

## Blocked

These need action outside the repository:

- [ ] **together_ai** — the API rejects the key as invalid.
- [ ] **openrouter** — the key carries a $0.00 total limit that does not
      reset, so every request is refused. The error body names the key id
      and its limit; the nightly workflow now redacts it before the
      summary is published.
- [ ] **sambanova** — the published model ids return 403, and the rest are
      rate-limited with a "high demand" message.

## Provider availability

Probed 2026-10-29 with `--doctor`. "Quota" means the key is valid but the
free tier's daily allowance is spent; it resets on a daily cycle.

| Provider | Status | Detail |
|---|---|---|
| groq | OK | `qwen/qwen3.8-27b` answered in 461 ms. The `gpt-oss-*` models answer with an **empty body** at low token budgets, so they are not in any stage chain. |
| mistral | OK | `ministral-3b-latest` answered in 4897 ms. |
| gemini | Quota | `gemini-3-flash-preview` answered in 1228 ms on an earlier probe; the key is valid but the daily allowance was spent at the time of this probe. 20 requests/day/model. |
| together_ai | Broken | Key rejected as invalid. |
| openrouter | Broken | Key has a $0.00 total limit that does not reset. |
| sambanova | Broken | Published model ids return 403; others report high demand. |
| duckduckgo | OK | The retriever needs no API key; it returned a result in 3636 ms. |

3 of 23 probes succeeded on this run. The remaining 16 providers are
registered and their model ids are verified against LiteLLM, but no key is
configured for them yet.

### A disclosure that was fixed

The nightly workflow used to grep the raw `--doctor` output into the public
job summary. That output included an OpenRouter error body containing a
SHA-256 hash of the key. It was not the key itself, and the hash is
rejected as a credential, but it was key-derived material in a public
place. The summary is now filtered to status lines and long token-like
strings are redacted, and the secrets file is deleted from the runner
workspace afterwards.

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

Free tiers retire models without warning. The nightly probe is what catches
it, and it has already paid for itself several times over:

| Date | Model | What happened |
|---|---|---|
| 2026-10-29 | `groq/openai/gpt-oss-120b` | Answers with an empty body at low token budgets. |
| 2026-10-29 | `gemini/gemini-2.5-flash` | `404`: *"no longer available to new users"*. Google points new accounts at `gemini-3.8-flash`. Every stage chain pointed at the retired ids until 1.0.1. |
| 2026-10-29 | `gemini/gemini-3-flash-preview` | Works, but only with a token budget above ~16: it spends part of the budget reasoning and answers with nothing below that. The probe budget was raised from 16 to 64 tokens. |
| 2026-10-29 | every model | Upstream's `article_gen` budget of 700 tokens is too small for a whole section — the article came back cut off mid-sentence ("Johannes Gensfleisch zur Laden zum Gutenberg (c."). Writing stages now get at least 1400 tokens, and reasoning models four times that. |

## Notes

- **The daily quota is the binding constraint**, not the rate limit. Gemini
  allows 20 requests/day/model and a full run needs 60–80 calls, which is
  why every stage has a three-provider chain and why the run log exists.
- **A run is cheaper in stages.** `--do-research --do-generate-outline`
  followed by the remaining stages reuses the cached research instead of
  paying for it twice.
- **The chain is the product.** The registry and the CLI are ordinary; the
  thing that makes this project useful is that a run survives the failure
  of any single provider. Every change to `classify_error` should be
  checked against that, in both directions: a provider-scoped error must
  advance the chain, and a request-scoped error must still abort it.
- **One provider's failure must never be able to sink a run.** That was
  true in principle and false in practice until 1.0.1 — the greedy-sampling
  recovery was the sharpest case, and it killed a run with two healthy
  providers still queued behind it.
