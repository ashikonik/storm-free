---
name: Bug report
about: Something did not work as documented
title: ""
labels: bug
assignees: ""
---

## What happened

<!-- What did you do, and what happened instead? -->

## What you expected

<!-- What should have happened instead? -->

## The output of `--doctor`

<!--
This is the single most useful thing you can paste. It shows which
providers answered, which did not, and why.

    ./venv/bin/python run_storm_free.py --doctor

Please paste the whole thing, including the error lines. Remove any API
key that appears in it -- the doctor prints the provider name and the
error, not the key, but please check.
-->

```
Paste the output here
```

## The version

<!-- Which commit are you on? -->

```
git rev-parse HEAD
```

## How you ran it

<!--
The full command, including the flags. Please do not paste your API
keys; the flags are what matter.
-->

```
./venv/bin/python run_storm_free.py "your topic" --do-research
```

## The run log

<!--
A failed run writes a log under `logs/runs/`. It records every provider
decision, the token usage and the per-stage timings, and it is usually
enough to diagnose the problem on its own.

    tail -50 logs/runs/$(ls -t logs/runs | head -1)

Please paste it, with any key-shaped text removed.
-->

```
Paste the log here
```

## Anything else

<!--
Which provider keys you have (by name, not by value), whether the run
was on a fresh clone, whether you changed the config, and anything else
that might matter.
-->
