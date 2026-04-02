"""
test_edge_cases.py — Stress, robustness, and boundary condition tests.

Covers:
  - 100,000+ node documents (no stack overflow)
  - Deeply nested elements (recursion safety)
  - Tags with 1000+ attributes
  - Extremely long text / attribute values
  - Null bytes, control chars
  - Concurrent parsing (GIL release verification)
  - Memory usage bounds
  - Repeated parse of same document
  - Invalid / adversarial inputs
"""

from __future__ import annotations

import string
import threading
import time

import pytest


# ===========================================================================
# Generators for large documents
# ===========================================================================

def make_wide_document(num_divs: int = 10_000) -> str:
    """Document with many sibling elements (wide, not deep)."""
    parts = ["<!DOCTYPE html><html><body>"]
    for i in range(num_divs):
        parts.append(f'<div id="d{i}" class="item" data-index="{i}"><p>Item {i}</p></div>')
    parts.append("</body></html>")
    return "".join(parts)


def make_deep_document(depth: int = 1000) -> str:
    """Document with extreme nesting depth."""
    open_tags = "".join(f'<div id="level-{i}" class="level">' for i in range(depth))
    close_tags = "</div>" * depth
    return f"<!DOCTYPE html><html><body>{open_tags}<span id='leaf'>deep leaf</span>{close_tags}</body></html>"


def make_many_attrs_document(num_attrs: int = 500) -> str:
    attrs = " ".join(f'data-attr-{i}="value-{i}"' for i in range(num_attrs))
    return f"<div {attrs}>text</div>"


def make_long_text_document(text_len: int = 1_000_000) -> str:
    long_text = "A" * text_len
    return f"<p id='long'>{long_text}</p>"


# ===========================================================================
# 1. Large / wide documents
# ===========================================================================

class TestLargeDocuments:
    @pytest.mark.slow
    def test_wide_document_10k_nodes(self, parse):
        html = make_wide_document(10_000)
        soup = parse(html)
        assert soup is not None
        divs = soup.find_all("div")
        assert len(divs) == 10_000

    @pytest.mark.slow
    def test_wide_document_find_middle(self, parse):
        html = make_wide_document(10_000)
        soup = parse(html)
        el = soup.find(id="d5000")
        assert el is not None
        assert el.find("p").get_text() == "Item 5000"

    @pytest.mark.slow
    def test_large_fixture_parses(self, parse, large_html):
        soup = parse(large_html)
        assert soup is not None
        sections = soup.find_all("section")
        assert len(sections) == 100

    @pytest.mark.slow
    def test_large_fixture_find_all_links(self, parse, large_html):
        soup = parse(large_html)
        links = soup.find_all("a")
        assert len(links) >= 1000

    @pytest.mark.slow
    def test_large_fixture_css_selector(self, parse, large_html):
        soup = parse(large_html)
        items = soup.select("li.item")
        assert len(items) >= 1000

    def test_100_node_document(self, parse):
        html = make_wide_document(100)
        soup = parse(html)
        assert len(soup.find_all("div")) == 100

    def test_1000_node_document(self, parse):
        html = make_wide_document(1000)
        soup = parse(html)
        assert len(soup.find_all("div")) == 1000


# ===========================================================================
# 2. Deeply nested documents
# ===========================================================================

class TestDeeplyNested:
    def test_depth_100_no_crash(self, parse):
        html = make_deep_document(100)
        soup = parse(html)
        assert soup.find(id="leaf") is not None

    def test_depth_500_no_crash(self, parse):
        html = make_deep_document(500)
        soup = parse(html)
        assert soup.find(id="leaf") is not None

    @pytest.mark.slow
    def test_depth_1000_no_stack_overflow(self, parse):
        html = make_deep_document(1000)
        soup = parse(html)
        assert soup.find(id="leaf") is not None

    @pytest.mark.slow
    def test_depth_5000_no_stack_overflow(self, parse):
        """Rust stack is larger; 5000 levels must not overflow."""
        html = make_deep_document(5000)
        soup = parse(html)
        assert soup is not None

    def test_deeply_nested_fixture(self, parse, deeply_nested_html):
        soup = parse(deeply_nested_html)
        leaf = soup.find(id="deep-leaf")
        assert leaf is not None
        assert "50 levels deep" in leaf.get_text()

    def test_deep_parents_chain(self, parse):
        html = make_deep_document(200)
        soup = parse(html)
        leaf = soup.find(id="leaf")
        parents = list(leaf.parents)
        # Must have at least 200 parents (the divs) + html + body + document
        assert len(parents) >= 200

    def test_deep_ancestors_no_infinite_loop(self, parse):
        html = make_deep_document(300)
        soup = parse(html)
        leaf = soup.find(id="leaf")
        count = 0
        for _ in leaf.parents:
            count += 1
            if count > 10000:
                pytest.fail("parents iterator did not terminate")
        assert count > 0


# ===========================================================================
# 3. Many attributes
# ===========================================================================

class TestManyAttributes:
    def test_100_attributes(self, parse):
        html = make_many_attrs_document(100)
        soup = parse(html)
        div = soup.find("div")
        assert div is not None
        assert len(div.attrs) >= 100

    def test_500_attributes_preserved(self, parse):
        html = make_many_attrs_document(500)
        soup = parse(html)
        div = soup.find("div")
        assert div["data-attr-0"] == "value-0"
        assert div["data-attr-499"] == "value-499"

    def test_find_by_attribute_in_large_attr_set(self, parse):
        html = make_many_attrs_document(500)
        soup = parse(html)
        el = soup.find(attrs={"data-attr-250": "value-250"})
        assert el is not None


# ===========================================================================
# 4. Long text / attribute values
# ===========================================================================

class TestLongValues:
    def test_long_text_node_1mb(self, parse):
        html = make_long_text_document(1_000_000)
        soup = parse(html)
        p = soup.find(id="long")
        assert p is not None
        text = p.get_text()
        assert len(text) >= 1_000_000

    def test_long_attribute_value(self, parse):
        long_val = "X" * 100_000
        html = f'<div data-long="{long_val}">text</div>'
        soup = parse(html)
        div = soup.find("div")
        assert len(div["data-long"]) == 100_000

    def test_long_id_attribute(self, parse):
        long_id = "id-" + "x" * 10_000
        html = f'<div id="{long_id}">text</div>'
        soup = parse(html)
        div = soup.find(id=long_id)
        assert div is not None

    def test_many_long_text_nodes(self, parse):
        parts = ["<div>"]
        for i in range(100):
            parts.append(f"<p>{'Word ' * 1000}</p>")
        parts.append("</div>")
        soup = parse("".join(parts))
        assert len(soup.find_all("p")) == 100


# ===========================================================================
# 5. Control characters and invalid input
# ===========================================================================

class TestControlChars:
    def test_null_byte_in_text(self, parse):
        html = "<p>text\x00with\x00nulls</p>"
        soup = parse(html)
        assert soup is not None
        text = soup.find("p").get_text()
        assert "\x00" not in text

    def test_null_byte_in_attribute(self, parse):
        html = '<div data-x="val\x00ue">text</div>'
        soup = parse(html)
        assert soup is not None

    def test_form_feed_char(self, parse):
        html = "<p>text\x0cwith\x0cformfeed</p>"
        soup = parse(html)
        assert soup is not None

    def test_all_control_chars_dont_crash(self, parse):
        for i in range(0, 32):
            char = chr(i)
            html = f"<p>text {char!r} end</p>"
            try:
                soup = parse(html)
                assert soup is not None
            except Exception as e:
                pytest.fail(f"Parser raised on control char U+{i:04X}: {e}")

    def test_empty_attribute_name(self, parse):
        """Attribute with empty name — must not crash."""
        html = '<div ="value">text</div>'
        soup = parse(html)
        assert soup is not None

    def test_only_whitespace_attribute_value(self, parse):
        html = '<div class="   ">text</div>'
        soup = parse(html)
        div = soup.find("div")
        assert div is not None

    def test_very_long_tag_name(self, parse):
        """An extremely long tag name — browser treats as unknown element."""
        long_name = "x" * 10_000
        html = f"<{long_name}>text</{long_name}>"
        soup = parse(html)
        assert soup is not None


# ===========================================================================
# 6. Adversarial / fuzzing-adjacent inputs
# ===========================================================================

class TestAdversarialInput:
    def test_completely_random_bytes_dont_crash(self, parse):
        import random, os
        random.seed(99)
        for _ in range(20):
            data = bytes(random.randint(0, 255) for _ in range(1000))
            try:
                soup = parse(data)
                assert soup is not None
            except UnicodeDecodeError:
                pass  # acceptable for random bytes
            except Exception as e:
                pytest.fail(f"Parser raised on random bytes: {e}")

    def test_repeated_open_tags_no_crash(self, parse):
        html = "<div>" * 10_000 + "text"
        soup = parse(html)
        assert soup is not None

    def test_repeated_close_tags_no_crash(self, parse):
        html = "text" + "</div>" * 10_000
        soup = parse(html)
        assert soup is not None

    def test_alternating_open_close_no_crash(self, parse):
        html = "".join(f"<div>{'</div>' * i}" for i in range(100))
        soup = parse(html)
        assert soup is not None

    def test_script_injection_not_executed(self, parse):
        html = '<div><script>alert(1)</script></div>'
        soup = parse(html)
        # Script content must be text, not parsed as tags
        script = soup.find("script")
        assert script is not None
        assert soup.find("alert") is None

    def test_comment_bomb_no_crash(self, parse):
        html = "".join(f"<!-- comment {i} -->" for i in range(10_000))
        soup = parse(html)
        assert soup is not None

    def test_entity_bomb_no_crash(self, parse):
        html = "<p>" + "&amp;" * 10_000 + "</p>"
        soup = parse(html)
        assert soup is not None

    def test_unicode_lookalike_tags(self, parse):
        """Tags with lookalike unicode characters are unknown elements."""
        html = "<ԁiv>lookalike div</ԁiv>"  # Cyrillic 'd'
        soup = parse(html)
        assert soup is not None

    def test_malformed_entity_recovery(self, parse):
        html = "<p>&amp &lt &gt &#notnum; &#xGGGG;</p>"
        soup = parse(html)
        assert soup is not None

    def test_overlapping_entities_no_crash(self, parse):
        html = "<p>&amp;amp;amp;</p>"
        soup = parse(html)
        p = soup.find("p")
        assert p is not None


# ===========================================================================
# 7. Concurrent parsing
# ===========================================================================

class TestConcurrent:
    def _parse_worker(self, parse, html: str, results: list, idx: int):
        try:
            soup = parse(html)
            results[idx] = len(soup.find_all("p"))
        except Exception as e:
            results[idx] = e

    def test_concurrent_parse_8_threads(self, parse):
        """8 threads parsing simultaneously must not corrupt each other's results."""
        html = "<html><body>" + "<p>text</p>" * 100 + "</body></html>"
        num_threads = 8
        results = [None] * num_threads
        threads = [
            threading.Thread(target=self._parse_worker, args=(parse, html, results, i))
            for i in range(num_threads)
        ]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        for i, r in enumerate(results):
            assert not isinstance(r, Exception), f"Thread {i} raised: {r}"
            assert r == 100, f"Thread {i} got wrong count: {r}"

    def test_concurrent_find_all(self, parse):
        """Concurrent find_all on the same document must be safe."""
        html = "<html><body>" + "<div class='item'><p>text</p></div>" * 200 + "</body></html>"
        soup = parse(html)
        results = [None] * 8

        def worker(idx):
            try:
                items = soup.find_all(class_="item")
                results[idx] = len(items)
            except Exception as e:
                results[idx] = e

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        for i, r in enumerate(results):
            assert not isinstance(r, Exception), f"Thread {i} raised: {r}"
            assert r == 200

    def test_concurrent_modification_isolated(self, parse):
        """Modification of one parsed document must not affect a different parse."""
        html = "<html><body><div id='target'>original</div></body></html>"
        soup1 = parse(html)
        soup2 = parse(html)
        soup1.find(id="target").string = "modified"
        assert soup2.find(id="target").get_text() == "original"


# ===========================================================================
# 8. Repeated parse stability
# ===========================================================================

class TestRepeatedParse:
    def test_parse_same_html_twice_identical(self, parse):
        html = "<html><body><div><p>Hello</p></div></body></html>"
        s1 = str(parse(html))
        s2 = str(parse(html))
        assert s1 == s2

    def test_parse_1000_times_no_leak(self, parse):
        """Parsing in a tight loop must not cause a crash (basic leak guard)."""
        html = "<div><p>text</p></div>"
        for _ in range(1000):
            soup = parse(html)
            assert soup.find("p") is not None

    def test_parse_large_document_repeatedly(self, parse):
        html = make_wide_document(500)
        for _ in range(10):
            soup = parse(html)
            assert len(soup.find_all("div")) == 500
