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

# Override the model for every stage
./venv/bin/python run_storm_free.py "topic" --model groq/openai/gpt-oss-120b

# Write a per-run log (default: logs/runs/<date>-<topic>.log)
./venv/bin/python run_storm_free.py "topic" --log-file /tmp/run.log

# Skip the log entirely
./venv/bin/python run_storm_free.py "topic" --no-log
```

Any stage flag you omit reuses the cached result from a previous run, so you
can research once and write later without paying twice.

Each run writes a log recording the command, the resolved provider chains,
every fallback decision and the token summary. The logs are gitignored;
[`STATUS.md`](STATUS.md) is the committed record of what has been achieved
and what is still open.

## Configuration

`free_config.toml` declares the model chain for each of STORM's five stages:

```toml
[stages.article_gen]
model = "gemini/gemini-2.5-flash"
fallbacks = [
    "groq/openai/gpt-oss-120b",
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

Two GitHub Actions workflows run on every push:

- **`ci.yml`** — lint (`ruff check`), format check (`ruff format --check`),
  the test suite, a type check (`mypy`), and a secret scan that fails the
  build if a key-shaped string reaches a committed file.
- **`nightly.yml`** — probes every configured provider once a day and writes
  the result to the job summary. This is what catches a provider retiring a
  model id before a user finds out the hard way. It skips itself cleanly
  when no provider keys are configured in the repository secrets.

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

Only **transient** failures move on to the next provider: rate limits (429),
connection errors and 5xx responses. Authentication errors (401/403) and bad
requests (400) raise immediately, because a wrong API key is a configuration
bug that should be reported rather than hidden by burning every provider in the
chain.

## Using with Claude Code

This project includes a `CLAUDE.md` that gives Claude Code full context.

```bash
claude    # reads CLAUDE.md automatically
```

## License

MIT — see [LICENSE](LICENSE). The upstream STORM project is also MIT licensed.

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md).
