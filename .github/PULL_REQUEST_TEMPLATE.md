## What this changes

<!-- What the pull request does, and why. -->

## Checklist

- [ ] The change is described above, and the commit messages say *why* it
      was needed, not only *what* changed.
- [ ] `python -m ruff check .` passes.
- [ ] `python -m ruff format --check .` passes.
- [ ] `python -m mypy free_setup run_storm_free.py` passes.
- [ ] `python -m pytest tests -v` passes.
- [ ] New behaviour has a test that would fail without the change.
- [ ] `README.md` is updated if a CLI flag was added, renamed or removed.
      `tests/test_docs.py` enforces this, so CI will say so if it was
      missed.
- [ ] `STATUS.md` is updated if the change affects what has been achieved.
- [ ] No API key appears anywhere in the diff. `secrets.toml` is
      gitignored; only `secrets.toml.example` belongs in the repository.
- [ ] The upstream `storm/` directory is not in the diff.

## Notes for the reviewer

<!--
Anything that deserves a closer look: a design decision you are unsure
about, a test that is hard to read, a place where you were forced to
choose between two reasonable options.
-->
