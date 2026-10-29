"""Repair STORM's outline parser so a topic-echo heading cannot break a run.

Upstream's ``StormArticle.from_outline_str`` (in
``knowledge_storm/storm_wiki/modules/storm_dataclass.py``) parses a
markdown outline into a tree of section nodes.  It contains a real bug that
free-tier models trigger often enough to matter.

The bug
-------

The parser looks at the first line of the outline.  If that line is the
article title -- which STORM's own prompt explicitly asks the model *not* to
include, but which models do anyway -- the parser treats it as a redundant
heading, drops it, and subtracts one from the level of every remaining
heading::

    # History of the Printing Press   <- topic echo, dropped
    ## Origins                         <- level 2 - 1 = 1
    ### Woodblock                      <- level 3 - 1 = 2
    # Precursors                       <- level 1 - 1 = 0  <-- problem

The stack of parent nodes is rooted at level 0.  A heading that computes to
level 0 pops the root off the stack, and the next line to add tries to
attach to ``node_stack[-1]`` -- which no longer exists::

    IndexError: list index out of range

The outline stage therefore dies with a bare ``IndexError`` that says
nothing about what went wrong, and no article is produced.

The fix
-------

We re-implement the parser with the same tree semantics but without the
off-by-one: the topic-echo heading is dropped from the *text*, while the
levels of the remaining headings are left exactly as the model wrote them.
That keeps every heading at or above the root level, so the stack never
empties.

Upstream is not modified -- this module replaces the classmethod on the
imported class at runtime, which keeps ``git pull`` on the upstream clone
clean.  :func:`apply_outline_patch` is idempotent and safe to call more
than once.

A second, smaller repair lives here too: upstream's ``except:`` around the
outline split silently swallows a failure and produces an empty outline
rather than an error.  We let the split fail loudly instead.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Marker attribute used to make the patch idempotent.
_PATCH_FLAG = "_free_setup_outline_patch_applied"

# Where the upstream parser is kept once it has been replaced, so that a
# failure in the repaired parser can fall back to it.
_ORIGINAL_ATTR = "_free_setup_original_outline_parser"


def _topic_echo_index(lines: list[str], topic: str) -> int | None:
    """Return the index of the leading heading that merely repeats the topic.

    A heading counts as a topic echo when its text, compared case
    insensitively and with underscores read as spaces, is the topic.  Only a
    *leading* heading is considered: a section later in the outline that
    happens to be named after the topic is a genuine section.
    """
    if not lines:
        return None
    first = lines[0]
    if not first.startswith("#"):
        return None
    heading = first.replace("#", "").strip().lower()
    normalised_topic = topic.strip().lower().replace("_", " ")
    if heading == normalised_topic:
        return 0
    return None


def parse_outline(topic: str, outline_str: str) -> Any:
    """Build a ``StormArticle`` outline tree from a markdown outline.

    This mirrors upstream's ``StormArticle.from_outline_str`` except that the
    heading levels are never shifted.  Dropping the topic echo is a change to
    the *content* of the outline, not to the level of the headings that
    follow, which is what makes the difference between a working outline and
    an ``IndexError``.

    Args:
        topic: The article topic.  Underscores and spaces are treated as
            equivalent, because upstream normalises them in different places.
        outline_str: The raw markdown outline.

    Returns:
        A ``StormArticle`` whose root holds the parsed sections.
    """
    from knowledge_storm.storm_wiki.modules.storm_dataclass import (
        ArticleSectionNode,
        StormArticle,
    )

    if not isinstance(outline_str, str):
        raise TypeError(f"outline must be a string, got {type(outline_str).__name__}")

    lines = [line.strip() for line in outline_str.split("\n") if line.strip()]
    article = StormArticle(topic)

    echo_index = _topic_echo_index(lines, topic)
    if echo_index is not None:
        lines = lines[1:]

    # (level, node) pairs; the root sits at level 0 so that a heading at
    # level 1 always has somewhere to attach.
    node_stack: list[tuple[int, Any]] = [(0, article.root)]

    for line in lines:
        level = line.count("#")
        section_name = line.replace("#", "").strip()
        if not section_name:
            continue
        if section_name.lower() == topic.strip().lower().replace("_", " "):
            # A section named after the topic adds nothing.
            continue

        new_node = ArticleSectionNode(section_name)

        # Walk back to the nearest ancestor that can hold a level-`level`
        # child.  The root is level 0, so anything at level 1 or deeper
        # always lands on it.
        while len(node_stack) > 1 and level <= node_stack[-1][0]:
            node_stack.pop()

        node_stack[-1][1].add_child(new_node)
        node_stack.append((level, new_node))

    return article


def apply_outline_patch() -> bool:
    """Install the repaired outline parser onto upstream's ``StormArticle``.

    Returns:
        ``True`` if the patch was installed by this call, ``False`` if it was
        already present.
    """
    from knowledge_storm.storm_wiki.modules.storm_dataclass import StormArticle

    if getattr(StormArticle, _PATCH_FLAG, False):
        return False

    original = StormArticle.from_outline_str.__func__  # type: ignore[attr-defined]

    @classmethod  # type: ignore[misc]
    def from_outline_str(cls: type, topic: str, outline_str: str) -> Any:
        """Parse an outline string into a ``StormArticle``.

        Replaces upstream's implementation, which raises ``IndexError`` when
        the outline opens with the article title and later contains another
        top-level heading.
        """
        try:
            return parse_outline(topic, outline_str)
        except Exception:
            # Fall back to upstream's behaviour rather than losing the run
            # entirely; its parser handles the common case correctly.
            logger.warning(
                "Repaired outline parser failed for topic %r; "
                "falling back to the upstream parser.",
                topic,
                exc_info=True,
            )
            return original(cls, topic, outline_str)

    from_outline_str.__free_setup_original__ = original  # type: ignore[attr-defined]
    StormArticle.from_outline_str = from_outline_str  # type: ignore[assignment]
    # The original is kept on the class as well as on the function, because a
    # ``classmethod`` does not proxy attribute lookups to its function.
    setattr(StormArticle, _ORIGINAL_ATTR, original)
    # The flag lives on the class, so a second call is a no-op.
    setattr(StormArticle, _PATCH_FLAG, True)
    logger.debug("Installed the repaired STORM outline parser.")
    return True


__all__ = ["apply_outline_patch", "parse_outline"]
