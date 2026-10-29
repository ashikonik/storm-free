# Changelog

All notable changes to this project are recorded here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and the project uses [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

Nothing yet.

## [1.0.1] — 2026-10-29

The "finishing touch" release. This release closes the correctness holes
that made the project's headline guarantee — that one provider's failure
never sinks a run — untrue, and fills in the onboarding and release gaps
that made the project hard to start with and hard to trust.

### Fixed

- **A provider's sampling quirk could kill the whole run.** When a
  provider rejected a request because of its greedy-sampling settings, the
  recovery retry was not itself guarded, so the second failure escaped as
  a fatal `BadRequestError` and the chain was never continued. The
  production run log shows it: a Mistral error ended the run with Groq and
  Gemini still queued. The greedy-sampling rejection is now converted into
  a dedicated `GreedySamplingError` before it reaches the classifier, so
  the chain moves on to the next provider.
- **A provider with a bad key could sink a run that had healthy providers
  queued behind it.** Authentication and permission errors are
  provider-scoped — the provider is dead, not the request — so the chain
  now advances to the next provider instead of aborting. Request-scoped
  errors (a malformed request, a context overflow, a content-policy
  refusal) still abort immediately, because retrying them elsewhere would
  only burn the same quota again.
- **The daily quota counter counted attempts rather than admitted calls,
  and was never clamped.** With `max_per_day=10` and fifty threads the
  counter reached fifty, and once past the cap it stayed past it for the
  rest of the day. The counter now records only calls that were admitted,
  and never exceeds the cap.
- **The run log carried no token counts and no timings.** The log was
  built from `runner.summary()`, but upstream's `Engine.summary()` prints
  its report and returns `None`, so the section was never written. The
  summary is now built from the usage tracker the wrapper already
  maintains, and each stage's elapsed time is recorded. The log also
  records which provider *served* each request, not only which ones
  failed — previously a successful run left no sign of who had answered.
- **`llm_call_history.jsonl` was always empty.** The history was appended
  to the inner model, but upstream reads the *wrapper's* list. The
  project's own flagship artifact was zero bytes. Both lists now receive
  the entry, under one lock.
- **A fresh clone without the upstream STORM clone died with a raw
  traceback.** `ModuleNotFoundError: No module named 'knowledge_storm'`,
  pointing at an internal file and never mentioning `setup.sh`. The
  clone's absence is now reported with the exact remedy.
- **`--model` silently discarded the entire fallback chain.** It replaced
  the primary model *and* the fallbacks, disabling the failover the
  project exists to provide. It now overrides the primary and keeps the
  configured chain.
- **A topic of `..` wrote the article outside the output directory.**
  The topic becomes a directory name, and `..` resolves to the output
  directory's parent. The topic is now checked against the resolved path
  before the run starts.
- **`setup.sh` reported success on a broken install.** The import checks
  were piped through `sed`, which defeated `set -e`, so a failed import
  printed a traceback and the script still said `=== Setup complete ===`.
  It also rebuilt the virtual environment on every run, making
  `INSTALL_UI=1 ./setup.sh` a 2.5 GB reinstall. It now reuses an existing
  environment and exits non-zero when the clone does not import.

### Changed

- **The nightly workflow no longer interpolates secrets into shell
  commands.** The keys were written with `echo 'GROQ_API_KEY = "${{
  secrets.GROQ_API_KEY }}"'`, which is a script-injection vector for a
  secret containing a quote or `$(...)`. They are passed through the
  environment and written with `printf` instead.
- **The nightly workflow no longer publishes raw provider errors.** The
  captured text included an OpenRouter error body holding a SHA-256 hash
  of the key, in a public job summary. The summary is now filtered to
  status lines and long token-like strings are redacted.
- **The nightly workflow deletes `secrets.toml`.** It ran in the runner
  workspace and left the file there.
- **The upstream STORM clone is pinned.** `setup.sh` tracked the default
  branch, and upstream can change its API without warning, so an upstream
  change could break the project at any moment. Both CI workflows clone
  the same commit, so the tested tree is the shipped tree.
- **The stage chains moved off the retired Gemini models.**
  `gemini/gemini-2.5-flash` and `gemini/gemini-2.5-flash-lite` are no
  longer served to new accounts, and the config pointed every stage at
  them.
- **OpenRouter is no longer recommended as a free provider.** Its
  configured key is permanently exhausted.
- **The retry classification covers more provider-side conditions.**
  `BudgetExceededError`, `RejectedRequestError`, `UnsupportedParamsError`,
  `BlockedPiiEntityError` and `OpenAIError` now advance the chain rather
  than aborting it.

### Added

- **A troubleshooting section in the README.** The most likely first-run
  failure — daily quota exhaustion — was never explained, though the CLI
  already had a good message for it.
- **A complete flag reference.** The README documented 8 of the CLI's 24
  flags.
- **A test that keeps the README and the parser in step.** Every flag
  the parser accepts must appear in the README, and every flag the README
  mentions must be one the parser accepts. A rename now fails CI instead
  of silently rotting the docs.
- **Tests for the CLI entry point**, which was previously untested. They
  invoke it as a subprocess, the way a user does.
- **Tests for the retriever wrapper**, covering result normalisation, the
  source filter and the failure handling.
- **A thread-safety test** that backs the claim in `STATUS.md`: under
  twenty concurrent threads the daily counter stays within its cap, no
  call is lost, and the call history is not corrupted.
- **A dedicated CI job for the CLI**, so a break in the entry point fails
  with a name that says so.
- **`SECURITY.md`**, **`CODE_OF_CONDUCT.md`**, a bug-report issue template,
  a pull-request template and `dependabot.yml`.

### Fixed (documentation)

- The README claimed the nightly workflow ran on every push. It runs on a
  schedule and on demand.
- `CLAUDE.md`'s architecture tree omitted three modules that exist, and
  documented neither the token-budget rules nor the fallback
  classification.
- `CONTRIBUTING.md` never mentioned `pytest` or `mypy`, though CI runs
  both, and described a fallback rule that the provider-scoped fix had
  changed.

[Unreleased]: https://github.com/ashikonik/storm-free/compare/v1.0.1...HEAD
[1.0.1]: https://github.com/ashikonik/storm-free/releases/tag/v1.0.1
