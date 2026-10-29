# Contributing

Thanks for helping improve the free-tier STORM setup.

## Development setup

```bash
./setup.sh
source venv/bin/activate
```

The setup script clones the upstream repository into `storm/`, builds a Python
3.11 virtual environment and installs the dependencies. The upstream clone is
kept separate so `git pull` inside `storm/` stays clean.

## Running the code

```bash
python run_storm_free.py --doctor          # probe providers and the retriever
python run_storm_free.py "topic" --dry-run # resolve the plan, no API calls
python run_storm_free.py "topic"           # full pipeline
```

## Code style

- Python 3.11, type hints on public functions.
- `from __future__ import annotations` at the top of each module.
- Docstrings on every public function and class, explaining *why* where it is
  not obvious.
- No bare `except:` clauses, and no silently swallowed exceptions — log with
  `logger.warning`/`logger.debug` and continue, or re-raise.
- Format with `ruff format`; lint with `ruff check`. Both are what CI runs,
  so a change that passes them locally will pass there too:

  ```bash
  python -m ruff format .
  python -m ruff check .
  ```

## Adding a provider

1. Add an entry to `PROVIDERS` in `free_setup/providers.py` with the LiteLLM
   prefix, the environment variable holding the key, and a default model.
2. Add its free models to `FREE_MODELS` in the same file.
3. Add the key *name* to `secrets.toml.example` with an empty value, and
   document the signup URL in the comment above it. Never put a real key in
   the template — only `secrets.toml` (gitignored) may hold one.

Provider prefixes and environment variable names should be taken from LiteLLM's
own provider logic (`litellm/litellm_core_utils/get_llm_provider_logic.py`)
rather than guessed, so they stay correct across renames.

## Adding a pipeline stage

STORM exposes five language-model slots. They are listed in
`free_setup/config.py` as `STAGES`, mapped to the upstream attribute names in
`STAGE_ATTRS`, and given a token budget in
`free_setup/runner.py` as `STAGE_MAX_TOKENS`. A new stage needs entries in all
three places, plus a `[stages.<name>]` block in `free_config.toml`.

## Changing the fallback behaviour

`free_setup/fallback.py` decides which errors advance to the next provider.
The rule is deliberate: rate limits and transient server errors fall through,
but authentication and bad-request errors raise immediately. A 401 means a
misconfigured key, and hiding that behind a silent fallback just makes the
problem harder to find. Please keep that distinction when editing
`classify_error`.

## Working around upstream bugs

The upstream clone in `storm/` is not modified, so a fix for an upstream bug
goes in `free_setup/` as a runtime patch instead. `free_setup/outline_patch.py`
is the worked example: it replaces `StormArticle.from_outline_str`, whose
upstream implementation raises a bare `IndexError` when an outline opens with
the article title and later contains another top-level heading. The patch is
installed by `free_setup/__init__.py` on import, so every entry point — the
CLI, the tests and the Streamlit demo — gets it by importing the package.

Two rules apply to such a patch:

- Keep it idempotent, and keep the original reachable so a failure can fall
  back to it rather than losing the run.
- Cover it with a test that reproduces the upstream failure exactly. The
  regression test is the only thing that stops the patch being dropped when
  upstream changes.

## Committing

- Keep the upstream `storm/` directory out of commits.
- Never commit `secrets.toml`. It is gitignored, and the template
  `secrets.toml.example` is the only version that belongs in the repository.
- Write commit messages in the imperative mood, describing what changed and
  why.

## Using with Claude Code

This project includes a `CLAUDE.md` that gives Claude Code full context about
the architecture, commands and constraints.

```bash
claude    # reads CLAUDE.md automatically
```

The `CLAUDE.md` records constraints that are easy to break by accident —
notably that the project must stay on Python 3.11 and that `dspy` must not be
upgraded — so it is worth reading before making changes.
