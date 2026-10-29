"""Tests for the repaired STORM outline parser.

Upstream's ``StormArticle.from_outline_str`` shifts every heading level down
by one when the outline opens with a heading that repeats the article title.
That shift makes a later top-level heading compute level 0, which pops the
node stack past its root and raises a bare ``IndexError``.  The outline stage
then dies and no article is produced.

These tests pin the repaired behaviour: the topic echo is dropped, the
remaining headings keep the levels the model wrote, and the tree always has
somewhere to attach.

Run with:  ./venv/bin/python -m pytest tests/test_outline_patch.py -v
"""

from __future__ import annotations

import pytest

from free_setup.outline_patch import apply_outline_patch, parse_outline

# The exact shape that broke a real run on 2026-10-29: the outline opens with
# the article title, and a second top-level heading appears further down.
TOPIC = "history of the printing press"
CRASHING_OUTLINE = """# History of the Printing Press
## Origins and Early Developments
### Woodblock printing
#### In China and Korea
### Movable type
#### Bi Sheng
# Precursors to the Printing Press
## Cylinder seals
### Proto-Elamite seals
"""


@pytest.fixture(autouse=True)
def _patched():
    """Make sure the repair is installed before each test."""
    apply_outline_patch()


class TestParseOutline:
    """The repaired parser's behaviour."""

    def test_the_crashing_outline_no_longer_raises(self) -> None:
        """This is the shape that produced ``IndexError`` upstream."""
        article = parse_outline(TOPIC, CRASHING_OUTLINE)
        assert len(article.root.children) > 0

    def test_the_topic_echo_is_not_a_section(self) -> None:
        """The redundant title heading must not become a section."""
        article = parse_outline(TOPIC, CRASHING_OUTLINE)
        names = [child.section_name for child in article.root.children]
        assert "History of the Printing Press" not in names

    def test_every_heading_becomes_a_node(self) -> None:
        """No heading is silently dropped."""
        article = parse_outline(TOPIC, CRASHING_OUTLINE)
        lines = [line.strip() for line in CRASHING_OUTLINE.split("\n") if line.strip()]
        # Nine headings in the fixture, one of which is the topic echo.
        assert len(lines) == 9
        # The root is included by default, so eight sections plus the root.
        assert len(article.get_outline_as_list()) == 9
        assert len(article.get_outline_as_list(include_root=False)) == 8

    def test_the_heading_levels_are_preserved(self) -> None:
        """The repair must not shift levels, only drop the echo line.

        Shifting is what breaks the parser upstream, so the levels the model
        wrote are the levels the tree keeps.  The tree is checked rather than
        the rendered outline, because ``get_outline_as_list`` re-derives the
        hashtag depth from the tree and so cannot show the difference.
        """
        article = parse_outline(TOPIC, CRASHING_OUTLINE)
        first = article.root.children[0]

        assert first.section_name == "Origins and Early Developments"
        assert first.children[0].section_name == "Woodblock printing"
        # A level-4 heading stays under its level-3 parent.
        assert first.children[0].children[0].section_name == "In China and Korea"

    def test_top_level_sections_stay_top_level(self) -> None:
        """Two top-level headings must both sit under the root."""
        article = parse_outline(TOPIC, CRASHING_OUTLINE)
        assert len(article.root.children) == 2

    def test_nesting_is_preserved(self) -> None:
        """A level-3 heading sits under its level-2 parent."""
        article = parse_outline(TOPIC, CRASHING_OUTLINE)
        first = article.root.children[0]
        assert first.section_name == "Origins and Early Developments"
        assert first.children[0].section_name == "Woodblock printing"

    def test_an_outline_without_a_topic_echo_is_unchanged(self) -> None:
        """The ordinary case must behave exactly as upstream does."""
        outline = "# Origins\n## Woodblock\n### China\n# Precursors\n## Seals"
        article = parse_outline(TOPIC, outline)

        assert len(article.root.children) == 2
        assert article.root.children[0].section_name == "Origins"
        assert article.root.children[1].section_name == "Precursors"

    def test_underscored_topics_are_handled(self) -> None:
        """Upstream normalises underscores to spaces in one place only."""
        article = parse_outline("history_of_the_printing_press", CRASHING_OUTLINE)
        names = [child.section_name for child in article.root.children]
        assert "History of the Printing Press" not in names

    def test_an_empty_outline_yields_an_empty_article(self) -> None:
        """No headings means no sections, but no crash either."""
        article = parse_outline(TOPIC, "")
        assert len(article.root.children) == 0

    def test_a_blank_outline_yields_an_empty_article(self) -> None:
        article = parse_outline(TOPIC, "   \n\n  \n")
        assert len(article.root.children) == 0

    def test_a_section_named_after_the_topic_is_dropped(self) -> None:
        """A later section named after the topic adds nothing."""
        outline = "# Origins\n## History of the printing press\n## Woodblock"
        article = parse_outline(TOPIC, outline)

        names = [child.section_name for child in article.root.children[0].children]
        assert "History of the printing press" not in names
        assert "Woodblock" in names

    def test_a_non_string_outline_is_rejected(self) -> None:
        """A wrong type is a programming error, not an empty outline."""
        with pytest.raises(TypeError):
            parse_outline(TOPIC, None)  # type: ignore[arg-type]


class TestApplyOutlinePatch:
    """Installing the repair on the upstream class."""

    def test_the_patch_is_installed(self) -> None:
        from knowledge_storm.storm_wiki.modules.storm_dataclass import StormArticle

        apply_outline_patch()
        # The installed parser must survive the crashing outline.
        article = StormArticle.from_outline_str(
            topic=TOPIC, outline_str=CRASHING_OUTLINE
        )
        assert len(article.root.children) == 2

    def test_the_patch_is_idempotent(self) -> None:
        """Applying it twice must not stack wrappers."""
        assert apply_outline_patch() is False
        assert apply_outline_patch() is False

    def test_the_upstream_parser_is_preserved_for_fallback(self) -> None:
        """The original must stay reachable so a failure can fall back."""
        from knowledge_storm.storm_wiki.modules.storm_dataclass import StormArticle

        apply_outline_patch()
        original = getattr(StormArticle, "_free_setup_original_outline_parser", None)
        assert original is not None
        assert callable(original)
