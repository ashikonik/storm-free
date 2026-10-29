"""Validation of the topic a run is asked to research.

The topic is user input and upstream turns it into a directory name: it
replaces ``/`` with ``_`` and joins the result onto the output directory.
That is enough to escape the output directory with a topic of ``..``, which
resolves to the output directory's *parent* -- so the article and the log
would be written outside ``outputs/`` altogether.

Upstream's sanitisation is not ours to change, and it is the wrong layer
for it: the check belongs where the untrusted value enters.  This module
rejects a topic that would not stay inside the output directory, so the
problem is reported as a clear error rather than as a surprise directory
appearing somewhere else.

The same rules are applied to the run log's filename, which is built from
the topic too.
"""

from __future__ import annotations

import os

__all__ = ["InvalidTopicError", "validate_topic"]


class InvalidTopicError(ValueError):
    """The topic cannot be used to build a safe output path."""


def validate_topic(topic: str) -> str:
    """Return ``topic`` if it is safe to use, or explain why it is not.

    A topic is rejected when it would place the output directory outside
    the directory it belongs in.  The check is made against the *resolved*
    path rather than against a list of forbidden strings, so it catches
    the ways a path can escape rather than the ways it is usually spelled.

    Raises:
        InvalidTopicError: if the topic would escape the output directory.
    """
    if not topic.strip():
        raise InvalidTopicError("The topic is empty.")

    # Upstream builds the directory name by replacing every space and
    # slash with an underscore, so the name that reaches the filesystem is
    # exactly this.  The check has to be made against that name, not
    # against the topic as typed, or it would judge a different path from
    # the one the run will actually write to.
    name = topic.replace(" ", "_").replace("/", "_")

    # A name made only of dots is the way out: ``..`` resolves to the
    # output directory's parent.
    if name and set(name) == {"."}:
        raise InvalidTopicError(
            f"The topic {topic!r} would write the article outside the "
            "output directory. Please use a real topic."
        )

    # Belt and braces: whatever the name is, the joined path must still
    # resolve to something inside the output directory.  This catches any
    # escape the rules above do not anticipate.
    base = os.path.abspath("outputs")
    joined = os.path.abspath(os.path.join(base, name))
    if os.path.commonpath([base, joined]) != base:
        raise InvalidTopicError(
            f"The topic {topic!r} would write the article outside the "
            "output directory. Please use a real topic."
        )

    return topic
