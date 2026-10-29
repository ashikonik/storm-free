# Free-Tier STORM

[![CI](https://github.com/ashikonik/storm-free/actions/workflows/ci.yml/badge.svg)](https://github.com/ashikonik/storm-free/actions/workflows/ci.yml)

Run [STORM](https://github.com/stanford-oval/storm) — the LLM knowledge-curation
system that writes Wikipedia-style articles with citations — using **only free
API keys**, with automatic failover between providers.

STORM researches a topic by simulating a conversation between a Wikipedia
writer and a topic expert, then writes a cited article from what it learned.

## Features

- **Multi-provider fallback** — when one provider hits its rate limit, the
  pipeline moves to the next one automatically. No stage fails just because a
  single free tier is busy.
- **Model diversity across stages** — research, outline, article generation
  and polishing can each use a *different* model, so the pipeline benefits from
  several models' perspectives rather than one model's view repeated.
- **Keyless web retrieval** — DuckDuckGo is the default search backend and
  needs no signup.
- **No embedding key required** — the STORM wiki pipeline does not use the
  encoder that would otherwise demand an OpenAI key.
- **Local CLI first** — a plain command-line runner; the Streamlit UI is
  optional.
- **Hosted APIs only** — every provider is a remote free-tier API. There is
  no local-model fallback, so nothing depends on the machine's hardware.

## Quick Start

```bash
./setup.sh                                    # clone upstream, build the venv
cp secrets.toml.example secrets.toml          # add whichever API key you have
./venv/bin/python run_storm_free.py --doctor   # confirm your providers answer
./venv/bin/python run_storm_free.py "your topic"
```

`setup.sh` accepts `INSTALL_UI=1` to also install the Streamlit dependencies.

**At least one API key is required.** Every provider is a hosted free-tier
API; there is no local model to fall back on.

## Free providers

Every one of these works without a credit card:

| Provider | Env var | Signup |
|---|---|---|
| Groq | `GROQ_API_KEY` | https://console.groq.com/keys |
| Google Gemini | `GEMINI_API_KEY` | https://aistudio.google.com/apikey |
| Mistral | `MISTRAL_API_KEY` | https://console.mistral.ai/api-keys |
| NVIDIA NIM | `NVIDIA_NIM_API_KEY` | https://build.nvidia.com |
| Cohere | `COHERE_API_KEY` | https://dashboard.cohere.com/api-keys |
| AI21 Studio | `AI21_API_KEY` | https://studio.ai21.com |
| Galadriel | `GALADRIEL_API_KEY` | https://galadriel.ai |
| Nebius AI Studio | `NEBIUS_API_KEY` | https://studio.nebius.ai |
| DeepInfra | `DEEPINFRA_API_KEY` | https://deepinfra.com |
| Hugging Face | `HUGGINGFACE_API_KEY` | https://huggingface.co/settings/tokens |
| OVHcloud AI Endpoints | `OVH_API_KEY` | https://endpoints.ai.cloud.ovh.net |
| Meta Llama API | `LLAMA_API_KEY` | https://llama.developer.meta.com |
| Fireworks AI | `FIREWORKS_AI_API_KEY` | https://fireworks.ai/api-keys |
| Hyperbolic | `HYPERBOLIC_API_KEY` | https://app.hyperbolic.xyz/ |
| Novita | `NOVITA_API_KEY` | https://novita.ai/dashboard |
| DeepSeek | `DEEPSEEK_API_KEY` | https://platform.deepseek.com/api_keys |
| Perplexity | `PERPLEXITY_API_KEY` | https://www.perplexity.ai/settings/api |
| xAI | `XAI_API_KEY` | https://console.x.ai/ |

Any provider whose key is missing is skipped automatically, so the config works
with one key or with all of them.

`--list-providers` shows the full registry — **22 hosted free-tier providers**.
Cerebras, SambaNova, Together and OpenRouter are registered too, but note that
Cerebras now requires a verified payment method and the other three had their
configured keys rejected by the API (see `STATUS.md` for the details).

Free tiers retire models without warning — Google stopped serving
`gemini-2.5-flash` to new accounts in October 2026. The nightly workflow
probes the configured providers every day precisely to catch that, and
`STATUS.md` records each retirement and the replacement that answers.

## Commands

```bash
# List every provider and the free models it offers
./venv/bin/python run_storm_free.py --list-providers

# Probe providers and the retriever with a real (tiny) request
./venv/bin/python run_storm_free.py --doctor

# Show the resolved plan without spending any tokens
./venv/bin/python run_storm_free.py "topic" --dry-run

# Run the full pipeline
./venv/bin/python run_storm_free.py "topic"

# Run one stage, then reuse it later
./venv/bin/python run_storm_free.py "topic" --do-research --do-generate-outline

# Restrict to one provider
./venv/bin/python run_storm_free.py "topic" --provider groq

# Override the model for every stage (the fallback chain is kept)
./venv/bin/python run_storm_free.py "topic" --model groq/qwen/qwen3.8-27b

# Write a per-run log (default: logs/runs/<date>-<topic>.log)
./venv/bin/python run_storm_free.py "topic" --log-file /tmp/run.log

# Skip the log entirely
./venv/bin/python run_storm_free.py "topic" --no-log
```

Any stage flag you omit reuses the cached result from a previous run, so you
can research once and write later without paying twice.

Each run writes a log recording the command, the resolved provider chains,
every provider decision, the token usage and the per-stage timings. The logs
are gitignored; [`STATUS.md`](STATUS.md) is the committed record of what has
been achieved and what is still open.

### All options

Run `./venv/bin/python run_storm_free.py --help` for the authoritative list.

**Run control**

| Flag | Default | What it does |
|---|---|---|
| `topic` | — | The topic to research and write about. Required, unless `--doctor` or `--list-providers` is used. |
| `--dry-run` | off | Resolve and print the plan without making any API calls. |
| `--doctor` | off | Probe every provider and the retriever, then exit. |
| `--list-providers` | off | Show every known provider and its free models, then exit. |
| `--verbose` | off | Print detailed progress to stderr. |

**Stages** — any flag you omit reuses the cached result of a previous run.

| Flag | What it does |
|---|---|
| `--do-research` | Run the research stage. |
| `--do-generate-outline` | Run the outline stage. |
| `--do-generate-article` | Run the article stage. |
| `--do-polish-article` | Run the polish stage. |

**Model and provider selection**

| Flag | Default | What it does |
|---|---|---|
| `--provider` | all | Restrict the fallback chain to this provider. Repeatable. |
| `--model` | from config | Override the *primary* model for every stage. The configured fallback chain is kept. |
| `--config` | `free_config.toml` | Path to the TOML configuration file. |
| `--secrets` | `secrets.toml` | Path to the TOML file with API keys. |

**Retrieval and pipeline size**

| Flag | Default | What it does |
|---|---|---|
| `--retriever` | `duckduckgo` | Search backend. Only `duckduckgo` is available. |
| `--max-conv-turn` | 2 | Questions per simulated conversation. |
| `--max-perspective` | 2 | Viewpoints to explore. |
| `--search-top-k` | 3 | Search results per query. |
| `--retrieve-top-k` | 5 | References per outline section. |
| `--max-thread-num` | 2 | Concurrent requests. Lower it if a provider rate-limits. |

**Output and logging**

| Flag | Default | What it does |
|---|---|---|
| `--output-dir` | `outputs` | Directory for generated articles. |
| `--log-file` | `logs/runs/<date>-<topic>.log` | Write the per-run log to this path. |
| `--no-log` | off | Do not write a per-run log. |
| `--use-cache` | off | Enable response caching. Off by default: upstream's cache is keyed on the request alone, so it could serve one provider's answer for another's request. |

## Troubleshooting

**"Every provider in the fallback chain has used up its daily request budget."**
The daily quota, not the rate limit, is what usually stops a run. Gemini's free
plan allows 20 requests per day per model, and a full run makes roughly 60–80
model calls, so one model can never finish a run on its own. Either add a
second provider's key, or run the stages in separate sittings:

```bash
./venv/bin/python run_storm_free.py "topic" --do-research --do-generate-outline
# ... wait for the quota to reset (midnight Pacific time for Gemini) ...
./venv/bin/python run_storm_free.py "topic" --do-generate-article --do-polish-article
```

The second command reuses the cached research, so you do not pay for it twice.

**"The upstream STORM clone is missing."**
The upstream clone lives in `storm/` and is gitignored, so a fresh checkout does
not have it. Run `./setup.sh` from the project root.

**A provider returns `AuthenticationError` or `PermissionDeniedError`.**
The key is wrong, revoked or out of credit. The chain moves on to the next
provider automatically — the run continues unless every provider in the chain
has failed. Check the key in `secrets.toml`, then confirm with `--doctor`.

**The article comes back cut off mid-sentence.**
The token budget for the stage was too small for a whole section. The writing
stages are given at least 1400 tokens, and reasoning models four times that,
but a provider that ignores `max_tokens` can still truncate. Try a different
model with `--model`.

**Everything is slow, or the run keeps retrying.**
Lower `--max-thread-num`. Free tiers are rate limited, and STORM drives the
pipeline from a thread pool; fewer threads means fewer simultaneous requests
and fewer rate-limit responses.

**A run failed — what happened?**
Read the run log. It records the command, the resolved chains, every provider
decision with the reason, the token usage and the per-stage timings:

```bash
tail -50 logs/runs/$(ls -t logs/runs | head -1)
```

## Configuration

`free_config.toml` declares the model chain for each of STORM's five stages:

```toml
[stages.article_gen]
model = "gemini/gemini-3-flash-preview"
fallbacks = [
    "groq/qwen/qwen3.8-27b",
    "nvidia_nim/meta-llama/llama-3.3-70b-instruct",
    "mistral/ministral-3b-latest",
]
```

`[defaults]` holds the pipeline hyperparameters (`max_conv_turn`,
`max_perspective`, `search_top_k`, `retrieve_top_k`, `max_thread_num`). The
defaults are deliberately small because free tiers are rate limited — Groq's
free plan allows roughly 30 requests per minute, while article polishing alone
asks for 4,000 output tokens.

## Continuous integration

Two GitHub Actions workflows:

- **`ci.yml`** — runs on every push and pull request: lint (`ruff check`),
  format check (`ruff format --check`), the test suite, a type check (`mypy`),
  and a secret scan that fails the build if a key-shaped string reaches a
  committed file.
- **`nightly.yml`** — runs once a day (and on demand) and probes every
  configured provider, writing the result to the job summary. This is what
  catches a provider retiring a model id before a user finds out the hard
  way. It skips itself cleanly when no provider keys are configured in the
  repository secrets.

## Streamlit UI (optional)

```bash
INSTALL_UI=1 ./setup.sh
./venv/bin/python -m streamlit run storm/frontend/demo_light/storm.py
```

The UI uses the same configuration and fallback chains as the CLI.

## Output

Articles are written to `outputs/<topic>/`:

| File | Contents |
|---|---|
| `conversation_log.json` | The simulated writer/expert conversation |
| `raw_search_results.json` | Every retrieved source |
| `storm_gen_outline.txt` | The generated outline |
| `storm_gen_article.txt` | The article with citations |
| `storm_gen_article_polished.txt` | The final polished article |
| `url_to_info.json` | Citation index |

## How the fallback works

`free_setup/fallback.py` wraps a chain of models behind the same interface
STORM expects, so it drops into any of the five language-model slots without
patching upstream code.

The chain distinguishes three kinds of failure:

- **Transient** — rate limits (429), connection errors and 5xx responses.
  The chain waits for the provider's own retry hint and then moves on.
- **Provider-scoped** — authentication errors (401/403) and retired model ids
  (404). These condemn the provider, not the request, so the chain moves on to
  the next one. A single provider with a broken key cannot sink a run that has
  healthy providers queued behind it.
- **Request-scoped** — bad requests (400), context-window overflows and content
  policy refusals. Retrying these elsewhere will not help, so they raise
  immediately rather than burning every provider in the chain.

Once a provider succeeds it stays sticky, so a healthy provider carries the
load instead of every call round-robining the chain.

## Upstream pin

`setup.sh` and both CI workflows clone `stanford-oval/storm` at a pinned
commit. Upstream can change its API without warning, and its
`requirements.txt` is used unmodified, so tracking the default branch would
let an upstream change break this project at any moment.

To move the pin, change `STORM_COMMIT` in `setup.sh` and the matching
`git -C storm checkout` lines in `.github/workflows/ci.yml` and
`.github/workflows/nightly.yml`, then re-run the test suite.

## Using with Claude Code

This project includes a `CLAUDE.md` that gives Claude Code full context.

```bash
claude    # reads CLAUDE.md automatically
```

## License

MIT — see [LICENSE](LICENSE). The upstream STORM project is also MIT licensed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for the development workflow, and
[CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md) for the rules that apply to
participation.

To report a security problem, do not open a public issue — see
[SECURITY.md](SECURITY.md).

## Release notes

[CHANGELOG.md](CHANGELOG.md) records what changed in each release.
[STATUS.md](STATUS.md) records what has been achieved, what is still open,
and which providers currently answer.
