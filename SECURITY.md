# Security Policy

## Supported versions

This project is pre-1.0 and tracks `master`. Fixes land on `master` and are
released as tags; there are no long-lived maintenance branches.

| Version | Supported |
|---|---|
| `master` | yes |
| older tags | no |

## Reporting a vulnerability

**Do not open a public issue for a security problem.** A public issue
tells everyone about the flaw before it is fixed.

Report it privately instead:

1. Open a [private security advisory](https://github.com/ashikonik/storm-free/security/advisories/new)
   on GitHub, or
2. Email the maintainer through the address on the
   [GitHub profile](https://github.com/ashikonik).

Please include:

- what the problem is and which file or command it affects,
- how to reproduce it,
- what an attacker would gain, and
- any suggested fix you already have in mind.

You should get an acknowledgement within a few days. Fixes for confirmed
problems are released as a tagged version, and the advisory is published
after the fix is available.

## What counts as a security problem here

This project handles API keys, so the obvious classes are:

- **A key being written somewhere public.** The nightly workflow publishes
  a job summary that anyone with read access to the repository can see, so
  key-shaped material must never reach it. The workflow redacts long
  token-like strings before publishing, and passes secrets through the
  environment rather than interpolating them into shell commands.
- **A key being committed.** `secrets.toml` is gitignored and a CI job
  fails the build if it is tracked, or if a key-shaped string appears in
  any committed file.
- **Command injection through configuration or provider errors.** Provider
  error text is attacker-influenced in principle, and it reaches the run
  log and the job summary. It is written as data, never evaluated as
  shell.
- **A path traversal in the output directory.** The output path is built
  from the topic, so `free_setup/topic.py` checks that the topic cannot
  place the output directory outside `--output-dir`. The check is made
  against the resolved path, so it catches the ways a path can escape
  rather than a list of spellings.

## Out of scope

- **The free-tier providers themselves.** Their keys, quotas and rate
  limits are their business.
- **Upstream STORM.** Vulnerabilities in `stanford-oval/storm` should go
  to that project. This project pins a specific commit of it, so report a
  break here only if the pin is what makes it reachable.
- **Runaway API spend.** There is no local model, so a bug can only spend
  the user's own free-tier quota.
- **The availability of a free tier.** Providers retire models and tighten
  quotas without warning; that is a reliability problem, not a security
  one, and it is tracked in `STATUS.md`.
