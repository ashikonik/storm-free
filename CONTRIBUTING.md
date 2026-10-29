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

Re-running `./setup.sh` is safe: it reuses the existing virtualenv rather
than rebuilding it. To add the optional Streamlit UI afterwards, run
`INSTALL_UI=1 ./setup.sh`.

The dev tools are not installed by `setup.sh`; install them into the venv
once:

```bash
pip install ruff==0.17.3 pytest mypy types-toml
```

## Running the code

```bash
python run_storm_free.py --doctor          # probe providers and the retriever
python run_storm_free.py "topic" --dry-run # resolve the plan, no API calls
python run_storm_free.py "topic"           # full pipeline
```

## The checks CI runs

CI runs four gates. Run the same four locally before opening a pull
request — a change that passes them locally will pass there too.

```bash
# 1. Lint and format
python -m ruff check .
python -m ruff format --check .

# 2. Types
python -m mypy free_setup run_storm_free.py

# 3. The test suite
python -m pytest tests -v

# 4. The CLI, as a subprocess
python -m pytest tests/test_cli.py -v
```

The test suite needs no API key and makes no network call, so it runs
offline. The tests that touch upstream do need the `storm/` clone, which
`setup.sh` provides.

`python -m pytest tests -v` is the one to run most often. `tests/test_docs.py`
asserts that every flag the CLI accepts is documented in `README.md`, so a
flag rename fails the suite until the README is updated with it.

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
`classify_error()` splits failures into three kinds, and the distinction
between the second and third is the one to be careful with:

- **Transient** — rate limits (429), 5xx, network errors. The chain waits
  for the provider's own retry hint, then moves on. Verdict: `retry`.
- **Provider-scoped** — authentication (401/403) and retired model ids
  (404). This provider is dead, so the chain moves to the next one. The run
  continues. Verdict: `provider_dead`.
- **Request-scoped** — bad requests (400), context overflows, content-policy
  refusals. Retrying elsewhere will not help, so the chain raises
  immediately. Verdict: `fatal`.

The reason provider-scoped errors no longer abort the run: several
registered providers are known-broken, and a 401 on the primary used to
mean providers 2 and 3 were never tried. A single provider with a bad key
cannot sink a run that has healthy providers queued behind it.

When you change `classify_error`, keep the two sets explicit and disjoint,
and test **both** directions: an auth error must advance the chain, a bad
request must still abort it. A request-scoped error misfiled as
provider-scoped would silently retry a bad request on every provider,
burning the whole quota.

One special case worth knowing about: a provider that rejects a request
because of its *sampling* settings raises a `BadRequestError`, but it is a
broken provider rather than a broken request. `_normalise_greedy_sampling()`
converts it into `GreedySamplingError` before it reaches the classifier.
Without that conversion a single provider's sampling quirk killed the whole
run — which is exactly what the committed production log showed.

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

## Adding a CLI flag

`run_storm_free.py::build_parser()` is the single source of truth for the
command line. Two things have to move together when a flag is added:

1. The `add_argument` call in `build_parser()`.
2. The flag's row in `README.md`'s "All options" tables.

`tests/test_docs.py` enforces this: it fails if the parser accepts a flag the
README does not mention, and if the README mentions a flag the parser does
not accept. The test also checks that every provider advertised as free in
the README's table is in the registry, so a provider added to the code but
not to the docs is caught too.

## The upstream pin

`setup.sh` clones `stanford-oval/storm` at a pinned commit
(`STORM_COMMIT`), and both CI workflows clone the same commit. Upstream can
change its API without warning, and its `requirements.txt` is used
unmodified, so tracking the default branch would let an upstream change
break the project at any moment.

To move the pin, change `STORM_COMMIT` in `setup.sh` and the matching
`git -C storm checkout` lines in `.github/workflows/ci.yml` and
`.github/workflows/nightly.yml`, then re-run the test suite.

## Committing

- Keep the upstream `storm/` directory out of commits.
- Never commit `secrets.toml`. It is gitignored, and the template
  `secrets.toml.example` is the only version that belongs in the repository.
- Write commit messages in the imperative mood, describing what changed and
  why. The convention is Conventional Commits: `fix:`, `feat:`, `docs:`,
  `test:`, `chore:`.

## Using with Claude Code

This project includes a `CLAUDE.md` that gives Claude Code full context about
the architecture, commands and constraints.

```bash
claude    # reads CLAUDE.md automatically
```

The `CLAUDE.md` records constraints that are easy to break by accident —
notably that the project must stay on Python 3.11 and that `dspy` must not be
upgraded — so it is worth reading before making changes.
