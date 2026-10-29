# CLAUDE.md

**Version:** 1.0.0 | **Python:** 3.11 (venv) | **Upstream:** stanford-oval/storm

Runs STORM — the LLM system that researches a topic and writes a
Wikipedia-style article with citations — against free-tier LLM APIs, with
automatic failover between providers.

## Quick Start

```bash
./setup.sh                                     # clone upstream + build venv
cp secrets.toml.example secrets.toml           # add any free API key
./venv/bin/python run_storm_free.py --doctor    # probe providers + retriever
./venv/bin/python run_storm_free.py "topic"     # run the pipeline
```

## Commands

```bash
# CLI (primary interface)
./venv/bin/python run_storm_free.py --list-providers
./venv/bin/python run_storm_free.py --doctor
./venv/bin/python run_storm_free.py "topic" --dry-run
./venv/bin/python run_storm_free.py "topic" [stage flags] [hyperparameter flags]

# Stage flags (omit one to reuse its cached output)
--do-research  --do-generate-outline  --do-generate-article  --do-polish-article

# Hyperparameters
--max-conv-turn  --max-perspective  --search-top-k  --retrieve-top-k  --max-thread-num

# Streamlit UI (optional, needs INSTALL_UI=1 ./setup.sh)
./venv/bin/python -m streamlit run storm/frontend/demo_light/storm.py
```

## Architecture

```
storm-free/
├── run_storm_free.py    CLI entrypoint: arg parsing, orchestration, reporting
├── free_config.toml     Per-stage model + fallback chains, hyperparameters
├── secrets.toml         API keys (gitignored; copy from .example)
├── free_setup/          Our package — all new code lives here
│   ├── providers.py     Provider registry, env-var discovery, model splitting
│   ├── fallback.py      FallbackLM: thread-safe failover across a model chain
│   ├── config.py        TOML loading/validation, stage definitions
│   ├── runner.py        Builds STORMWikiLMConfigs + STORMWikiRunner
│   ├── retrievers.py    DuckDuckGo retriever (ddgs, backend="auto")
│   ├── doctor.py        Provider/retriever probing
│   ├── run_log.py       Per-run log: provider decisions, tokens, timings
│   ├── secrets.py       Loads secrets.toml into the environment
│   ├── outline_patch.py Runtime patch for upstream's outline parser
│   └── progress.py      BaseCallbackHandler -> terminal output
└── storm/               Upstream clone (read-only in practice)
```

**Data flow:** `run_storm_free.py` loads `free_config.toml` -> `runner.py`
resolves each stage's model chain into a `FallbackLM` -> `STORMWikiRunner`
drives the upstream 4-module pipeline -> results land in `outputs/<topic>/`.

## Key Files

| File | Purpose |
|---|---|
| `free_setup/providers.py` | `PROVIDERS` registry; `discover_available()`; `split_model()` |
| `free_setup/fallback.py` | `FallbackLM`; `classify_error()` splits retryable from provider-scoped from fatal |
| `free_setup/config.py` | `STAGES` (the 5 slots); `load_config()`; `describe()` |
| `free_setup/runner.py` | `build_runner()`; `build_lm_configs()`; `build_retriever()`; `budget_for_model()` |
| `free_setup/retrievers.py` | `get_duckduckgo_retriever()` — `ddgs` with `backend="auto"` |
| `free_setup/doctor.py` | `probe_provider()`; `probe_retriever()`; `render_results()` |
| `free_setup/run_log.py` | `RunLog`; `build_run_log()`; `attach_log_capture()` |
| `free_setup/secrets.py` | `load_secrets()` — reads `secrets.toml` into the environment |
| `free_setup/outline_patch.py` | `apply_outline_patch()` — replaces upstream's broken outline parser |
| `free_setup/progress.py` | `CLICallbackHandler` |
| `run_storm_free.py` | CLI: `--doctor`, `--list-providers`, `--dry-run`, pipeline run |

## Configuration

API keys live in `secrets.toml` (gitignored; copy from `secrets.toml.example`).
The registry in `free_setup/providers.py` lists them all — 22 providers:
`GROQ_API_KEY`, `GEMINI_API_KEY`, `MISTRAL_API_KEY`, `NVIDIA_NIM_API_KEY`,
`COHERE_API_KEY`, `AI21_API_KEY`, `GALADRIEL_API_KEY`, `NEBIUS_API_KEY`,
`DEEPINFRA_API_KEY`, `HUGGINGFACE_API_KEY`, `OVH_API_KEY`, `LLAMA_API_KEY`,
`FIREWORKS_AI_API_KEY`, `HYPERBOLIC_API_KEY`, `NOVITA_API_KEY`,
`DEEPSEEK_API_KEY`, `PERPLEXITY_API_KEY`, `XAI_API_KEY`, `CEREBRAS_API_KEY`,
`TOGETHER_API_KEY`, `SAMBANOVA_API_KEY`, `OPENROUTER_API_KEY`.
**At least one is required** — every provider is a hosted API and there is no
local-model fallback.
`free_config.toml` holds per-stage `model` + `fallbacks` lists and
`[defaults]` hyperparameters.

## Token budgets

`budget_for_model(model_id, stage)` in `free_setup/runner.py` decides how
many output tokens a request may carry. It starts from the per-stage budget
in `STAGE_MAX_TOKENS`, raises the writing stages (`article_gen`,
`article_polish`, `outline_gen`) to a floor of `MIN_WRITING_MAX_TOKENS`
(1400), multiplies by `REASONING_TOKEN_MULTIPLIER` (4) when the model id
matches one of `REASONING_MODEL_MARKERS` (`gemini-3`, `gpt-oss`, `o1`, `o3`,
`o4`, `r1`, `deepseek-reasoner`, `magistral-medium`), and caps the result at
`MAX_STAGE_MAX_TOKENS` (16,000).

Two reasons for the shape:

- **The writing floor.** Upstream's `article_gen` budget of 700 tokens is
  sized for a short answer, but the stage asks for a whole Wikipedia
  section. A section does not fit in 700 tokens: a 700-token request to
  `groq/qwen/qwen3.8-27b` came back cut off mid-sentence, while the same
  request at 1400 tokens came back complete. A truncated section reads as
  broken.
- **The reasoning multiplier.** A reasoning model spends part of its budget
  thinking. Without the multiplier that part eats into the part that has to
  hold the answer.

## Constraints

- **Python 3.11 only.** Upstream pins `dspy_ai==2.4.9`, which predates
  Python 3.12+ and breaks on 3.14. Do not upgrade dspy: STORM imports
  `dspy.dsp` internals that changed in later releases.
- **Do not modify `storm/`** except `frontend/demo_light/demo_util.py`, whose
  `set_storm_runner()` is patched to use `free_setup`. Everything else is a
  new file so `git pull` stays clean. When an upstream bug has to be worked
  around, patch it at runtime from `free_setup/` instead —
  `free_setup/outline_patch.py` replaces the broken
  `StormArticle.from_outline_str` and is installed on import of
  `free_setup`. Keep such patches idempotent, keep the original reachable as
  a fallback, and cover the upstream failure with a regression test.
- **Hosted APIs only — no local models.** The target machine cannot serve a
  usable local model, so the provider registry contains no local entries and
  `ProviderSpec.local` no longer exists. Do not reintroduce one.
- **The STORM wiki pipeline needs no embedding API key.** `Encoder`
  (`knowledge_storm/encoder.py`) supports only openai/azure but is used only
  by Co-STORM.
- **Free tiers are rate limited.** Keep `max_thread_num` low (2) and
  `max_conv_turn` at 2; article polish alone requests 4,000 output tokens.
  The *daily* cap is the binding constraint: one Gemini model allows 20
  requests/day, so a run needs several models across several providers.
- **Fallback distinguishes three kinds of failure, and the distinction
  matters.** `classify_error()` in `free_setup/fallback.py` returns one of
  four verdicts:
  - `retry` — transient (429, 5xx, network). The chain waits for the
    provider's own retry hint and then moves on.
  - `provider_dead` — provider-scoped (401/403, 404). This provider is
    unusable, so the chain moves to the next one. It does **not** abort the
    run: a broken primary must not sink a run that has healthy providers
    queued behind it.
  - `fatal` — request-scoped (400, context overflow, content policy).
    Retrying elsewhere will not help, so the chain raises immediately.
  - `unknown` — not retryable, but not clearly request-scoped either.

  A `BadRequestError` raised by a provider's *sampling* quirk is the
  exception that motivated this split: it is converted into
  `GreedySamplingError` before it reaches the classifier, because a
  provider that refuses `top_p` is a broken provider, not a broken request.
  Without that conversion a single provider's sampling quirk killed the
  whole run. Keep the three sets disjoint and keep both directions tested.
- **The run log is the durable record of a run.** `free_setup/run_log.py`
  captures the `free_setup` and `storm_free` loggers, so a provider
  decision only reaches the log if it is logged at those levels. Upstream's
  `Engine.summary()` prints and returns `None`, so the token and timing
  summary is built from `FallbackLM.usage_summary()` instead.
- **Every provider call is time-boxed.** LiteLLM's own default is a 6000-second
  timeout, which lets one stalled free-tier provider hold the pipeline for over
  an hour. `build_lm()` sets `timeout=REQUEST_TIMEOUT_SECONDS` (60) and
  `num_retries=NUM_RETRIES` (1). Keep those in place, and keep the model ids in
  `free_config.toml` to ones a live probe has confirmed — `tests/test_providers.py`
  checks that every id in `FREE_MODELS` is one LiteLLM recognises.
