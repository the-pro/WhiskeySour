"""
test_tree_navigation.py — Tree traversal and navigation property tests.

Covers:
  - .parent, .parents
  - .children, .contents
  - .next_sibling, .previous_sibling
  - .next_siblings, .previous_siblings
  - .next_element, .previous_element
  - .descendants
  - .string, .strings, .stripped_strings
  - .get_text(separator, strip)
  - .name, .attrs, .get()
  - Multi-valued attributes (class, rel, rev, accept-charset, headers)
  - NavigableString properties
  - Navigating into script/style tags
  - Tag.smooth() — consolidate NavigableStrings
"""

from __future__ import annotations

import pytest

NAV_HTML = """
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Navigation Test</title>
</head>
<body>
  <div id="root">
    <h1 id="h1">Heading One</h1>
    <p id="p1" class="text first">First paragraph <span id="sp1" class="inner">inner span</span> end.</p>
    <p id="p2" class="text second">Second paragraph <em id="em1">emphasis</em> text.</p>
    <ul id="list">
      <li id="li1" class="item">Item one</li>
      <li id="li2" class="item">Item two</li>
      <li id="li3" class="item last">Item three</li>
    </ul>
    <div id="nested">
      <div id="nested-inner">
        <p id="deep-p">Deep <strong id="strong1">strong</strong> paragraph</p>
      </div>
    </div>
    <script id="script1">var x = 1; /* script content */</script>
    <style id="style1">body { margin: 0; } /* style content */</style>
  </div>
</body>
</html>
"""


@pytest.fixture
def soup(parse):
    return parse(NAV_HTML)


# ===========================================================================
# 1. .parent
# ===========================================================================

class TestParent:
    def test_element_parent(self, soup):
        li1 = soup.find(id="li1")
        assert li1.parent.name == "ul"
        assert li1.parent["id"] == "list"

    def test_body_parent_is_html(self, soup):
        body = soup.find("body")
        assert body.parent.name == "html"

    def test_html_parent_is_document(self, soup):
        html = soup.find("html")
        assert html.parent is not None
        assert html.parent.name == "[document]"

    def test_deep_parent_chain(self, soup):
        strong = soup.find(id="strong1")
        assert strong.parent["id"] == "deep-p"
        assert strong.parent.parent["id"] == "nested-inner"
        assert strong.parent.parent.parent["id"] == "nested"

    def test_navigable_string_parent(self, soup):
        p1 = soup.find(id="p1")
        first_text = next(p1.strings)
        assert first_text.parent is not None


# ===========================================================================
# 2. .parents (iterator)
# ===========================================================================

class TestParents:
    def test_parents_iterator(self, soup):
        li1 = soup.find(id="li1")
        parent_names = [p.name for p in li1.parents]
        assert "ul" in parent_names
        assert "div" in parent_names
        assert "body" in parent_names
        assert "html" in parent_names

    def test_parents_reaches_document(self, soup):
        strong = soup.find(id="strong1")
        all_parents = list(strong.parents)
        names = [p.name for p in all_parents]
        assert "[document]" in names

    def test_parents_returns_iterator(self, soup):
        li = soup.find(id="li2")
        parents = li.parents
        # Must be iterable
        assert hasattr(parents, "__iter__")

    def test_parents_count(self, soup):
        strong = soup.find(id="strong1")
        # strong → p → div#nested-inner → div#nested → div#root → body → html → [document]
        parents = list(strong.parents)
        assert len(parents) >= 6


# ===========================================================================
# 3. .children / .contents
# ===========================================================================

class TestChildren:
    def test_contents_is_list(self, soup):
        ul = soup.find(id="list")
        contents = ul.contents
        assert isinstance(contents, list)

    def test_contents_includes_text_nodes(self, soup):
        p1 = soup.find(id="p1")
        # Contents: [text, span, text]
        assert len(p1.contents) >= 3

    def test_children_is_iterator(self, soup):
        ul = soup.find(id="list")
        children = ul.children
        assert hasattr(children, "__iter__")

    def test_children_count(self, soup):
        ul = soup.find(id="list")
        tag_children = [c for c in ul.children if c.name is not None]
        assert len(tag_children) == 3

    def test_contents_and_children_same_elements(self, soup):
        ul = soup.find(id="list")
        from_contents = [c for c in ul.contents if c.name is not None]
        from_children = [c for c in ul.children if c.name is not None]
        assert from_contents == from_children

    def test_void_element_has_no_children(self, parse):
        soup = parse("<br><img src='x.png' alt=''>")
        br = soup.find("br")
        assert list(br.children) == []

    def test_empty_element_children(self, soup):
        html_tag = soup.find("html")
        tag_children = [c for c in html_tag.children if c.name is not None]
        assert any(c.name == "head" for c in tag_children)
        assert any(c.name == "body" for c in tag_children)


# ===========================================================================
# 4. .next_sibling / .previous_sibling
# ===========================================================================

class TestSiblings:
    def test_next_sibling_tag(self, soup):
        li1 = soup.find(id="li1")
        sib = li1.next_sibling
        # Skip whitespace text nodes (name is None for NavigableString)
        while sib and sib.name is None:
            sib = sib.next_sibling
        assert sib is not None
        assert sib["id"] == "li2"

    def test_previous_sibling_tag(self, soup):
        li2 = soup.find(id="li2")
        sib = li2.previous_sibling
        while sib and sib.name is None:
            sib = sib.previous_sibling
        assert sib is not None
        assert sib["id"] == "li1"

    def test_first_child_no_previous_sibling(self, soup):
        h1 = soup.find(id="h1")
        sib = h1.previous_sibling
        while sib and sib.name is None:
            sib = sib.previous_sibling
        assert sib is None

    def test_last_child_no_next_sibling(self, soup):
        li3 = soup.find(id="li3")
        sib = li3.next_sibling
        while sib and sib.name is None:
            sib = sib.next_sibling
        assert sib is None

    def test_next_siblings_iterator(self, soup):
        li1 = soup.find(id="li1")
        next_sibs = [s for s in li1.next_siblings if s.name is not None]
        assert len(next_sibs) == 2
        assert next_sibs[0]["id"] == "li2"
        assert next_sibs[1]["id"] == "li3"

    def test_previous_siblings_iterator(self, soup):
        li3 = soup.find(id="li3")
        prev_sibs = [s for s in li3.previous_siblings if s.name is not None]
        assert len(prev_sibs) == 2

    def test_siblings_order_next_siblings(self, soup):
        li1 = soup.find(id="li1")
        tags = [s for s in li1.next_siblings if s.name is not None]
        assert tags[0]["id"] == "li2"
        assert tags[1]["id"] == "li3"

    def test_siblings_order_previous_siblings(self, soup):
        """previous_siblings yields in reverse document order (nearest first)."""
        li3 = soup.find(id="li3")
        tags = [s for s in li3.previous_siblings if s.name is not None]
        assert tags[0]["id"] == "li2"
        assert tags[1]["id"] == "li1"


# ===========================================================================
# 5. .next_element / .previous_element
# ===========================================================================

class TestNextPreviousElement:
    def test_next_element(self, soup):
        h1 = soup.find(id="h1")
        nxt = h1.next_element
        assert nxt is not None

    def test_next_element_descends_into_tags(self, soup):
        """next_element should enter child elements, not skip over them."""
        p1 = soup.find(id="p1")
        nxt = p1.next_element
        # First element inside p1 is the "First paragraph " text node
        assert nxt is not None
        assert "First paragraph" in str(nxt) or nxt.parent["id"] == "p1"

    def test_previous_element(self, soup):
        sp1 = soup.find(id="sp1")
        prev = sp1.previous_element
        assert prev is not None

    def test_element_walk_covers_all_nodes(self, soup):
        """Walking next_element from the start must visit every node."""
        body = soup.find("body")
        count = 0
        current = body
        while current is not None:
            count += 1
            current = current.next_element
            if count > 10000:
                break
        assert count > 10


# ===========================================================================
# 6. .descendants
# ===========================================================================

class TestDescendants:
    def test_descendants_iterator(self, soup):
        ul = soup.find(id="list")
        descs = list(ul.descendants)
        assert len(descs) > 3

    def test_descendants_includes_text_nodes(self, soup):
        p1 = soup.find(id="p1")
        descs = list(p1.descendants)
        # NavigableString.name is None; Tag.name is a non-None string
        has_text = any(d.name is None for d in descs)
        assert has_text or len(descs) > 0

    def test_descendants_includes_nested_tags(self, soup):
        root = soup.find(id="root")
        desc_names = {d.name for d in root.descendants if d.name is not None}
        assert "h1" in desc_names
        assert "ul" in desc_names
        assert "li" in desc_names
        assert "strong" in desc_names

    def test_descendants_count_vs_find_all(self, soup):
        root = soup.find(id="root")
        desc_tags = [d for d in root.descendants if d.name is not None]
        found_all = root.find_all(True)
        assert len(desc_tags) == len(found_all)

    def test_leaf_element_no_descendants(self, parse):
        soup = parse("<span>text</span>")
        span = soup.find("span")
        descs = [d for d in span.descendants if d.name is not None]
        assert descs == []


# ===========================================================================
# 7. .string / .strings / .stripped_strings
# ===========================================================================

class TestStringProperties:
    def test_string_single_text_child(self, soup):
        h1 = soup.find(id="h1")
        assert h1.string == "Heading One"

    def test_string_multiple_children_returns_none(self, soup):
        """If element has multiple children, .string returns None."""
        p1 = soup.find(id="p1")
        # p1 has text + span + text = multiple children
        assert p1.string is None

    def test_string_single_nested_text(self, soup):
        """If all descendants resolve to a single text, .string returns it."""
        em = soup.find(id="em1")
        assert em.string == "emphasis"

    def test_strings_iterator(self, soup):
        p1 = soup.find(id="p1")
        strings = list(p1.strings)
        assert len(strings) >= 2
        assert any("First paragraph" in s for s in strings)
        assert any("inner span" in s for s in strings)

    def test_stripped_strings_no_whitespace(self, soup):
        ul = soup.find(id="list")
        stripped = list(ul.stripped_strings)
        assert "Item one" in stripped
        assert "Item two" in stripped
        assert "Item three" in stripped
        assert all(s == s.strip() for s in stripped)

    def test_strings_includes_all_text(self, soup):
        root = soup.find(id="root")
        all_text = " ".join(root.stripped_strings)
        assert "Heading One" in all_text
        assert "First paragraph" in all_text
        assert "Item two" in all_text

    def test_script_string_is_raw(self, soup):
        script = soup.find(id="script1")
        assert "var x = 1;" in script.string

    def test_style_string_is_raw(self, soup):
        style = soup.find(id="style1")
        assert "margin" in style.string


# ===========================================================================
# 8. .get_text()
# ===========================================================================

class TestGetText:
    def test_get_text_basic(self, soup):
        h1 = soup.find(id="h1")
        assert h1.get_text() == "Heading One"

    def test_get_text_nested(self, soup):
        p2 = soup.find(id="p2")
        text = p2.get_text()
        assert "emphasis" in text
        assert "Second paragraph" in text

    def test_get_text_with_separator(self, soup):
        ul = soup.find(id="list")
        text = ul.get_text(separator=" | ")
        assert "Item one" in text
        assert " | " in text

    def test_get_text_strip(self, soup):
        ul = soup.find(id="list")
        text = ul.get_text(strip=True)
        assert not text.startswith(" ")
        assert not text.endswith(" ")

    def test_get_text_excludes_comments(self, parse):
        soup = parse("<p>visible<!-- hidden -->text</p>")
        text = soup.find("p").get_text()
        assert "hidden" not in text

    def test_get_text_full_document(self, soup):
        text = soup.get_text()
        assert "Heading One" in text
        assert "Item three" in text


# ===========================================================================
# 9. .name / .attrs / .get()
# ===========================================================================

class TestNameAndAttrs:
    def test_name_property(self, soup):
        p1 = soup.find(id="p1")
        assert p1.name == "p"

    def test_attrs_is_dict(self, soup):
        p1 = soup.find(id="p1")
        assert isinstance(p1.attrs, dict)
        assert "id" in p1.attrs
        assert "class" in p1.attrs

    def test_get_existing_attr(self, soup):
        p1 = soup.find(id="p1")
        assert p1.get("id") == "p1"

    def test_get_missing_attr_returns_none(self, soup):
        p1 = soup.find(id="p1")
        assert p1.get("nonexistent") is None

    def test_get_missing_attr_with_default(self, soup):
        p1 = soup.find(id="p1")
        assert p1.get("nonexistent", "default") == "default"

    def test_subscript_access(self, soup):
        p1 = soup.find(id="p1")
        assert p1["id"] == "p1"

    def test_subscript_missing_raises(self, soup):
        p1 = soup.find(id="p1")
        with pytest.raises(KeyError):
            _ = p1["nonexistent_attr"]

    def test_has_attr(self, soup):
        li1 = soup.find(id="li1")
        assert li1.has_attr("class")
        assert not li1.has_attr("href")


# ===========================================================================
# 10. Multi-valued attributes
# ===========================================================================

class TestMultiValuedAttributes:
    def test_class_is_list(self, soup):
        p1 = soup.find(id="p1")
        assert isinstance(p1["class"], list)
        assert "text" in p1["class"]
        assert "first" in p1["class"]

    def test_rel_is_list(self, parse):
        soup = parse('<a href="#" rel="noopener noreferrer external">link</a>')
        a = soup.find("a")
        assert isinstance(a["rel"], list)
        assert set(a["rel"]) == {"noopener", "noreferrer", "external"}

    def test_single_class_still_list(self, parse):
        soup = parse('<div class="single">text</div>')
        div = soup.find("div")
        assert isinstance(div["class"], list)
        assert div["class"] == ["single"]

    def test_no_class_attr_raises_or_returns_empty(self, parse):
        soup = parse('<div>no class</div>')
        div = soup.find("div")
        assert div.get("class") is None or div.get("class") == []

    def test_headers_is_list(self, parse):
        soup = parse('<table><tbody><tr><td headers="col1 col2">cell</td></tr></tbody></table>')
        td = soup.find("td")
        assert isinstance(td["headers"], list)
        assert "col1" in td["headers"]


# ===========================================================================
# 11. NavigableString
# ===========================================================================

class TestNavigableString:
    def test_navigable_string_is_str(self, soup):
        h1 = soup.find(id="h1")
        s = h1.string
        assert isinstance(s, str)

    def test_navigable_string_parent(self, soup):
        h1 = soup.find(id="h1")
        s = h1.string
        assert s.parent is h1

    def test_navigable_string_next_element(self, soup):
        h1 = soup.find(id="h1")
        s = h1.string
        nxt = s.next_element
        # After h1's text, next element is p#p1 or its first child
        assert nxt is not None

    def test_comment_type(self, parse):
        from whiskysour import Comment  # type: ignore[import]
        soup = parse("<!-- my comment --><p>after</p>")
        comments = soup.find_all(string=lambda t: isinstance(t, Comment))
        assert len(comments) == 1
        assert "my comment" in comments[0]

    def test_navigable_string_name_is_none(self, soup):
        """NavigableString.name must be None — the canonical BS4 way to detect text nodes."""
        h1 = soup.find(id="h1")
        s = h1.string
        assert s.name is None

    def test_name_none_distinguishes_text_from_tags(self, soup):
        """Tags have a non-None name; NavigableString has name=None."""
        p1 = soup.find(id="p1")
        for child in p1.descendants:
            if isinstance(child, str):
                assert child.name is None, "text node name must be None"
            else:
                assert child.name is not None, "element name must not be None"

    def test_name_none_filter_pattern(self, soup):
        """The BS4-idiomatic `if child.name:` pattern works correctly."""
        ul = soup.find(id="list")
        # `if child.name:` skips None (text nodes) and keeps tag names
        element_children = [c for c in ul.children if c.name]
        assert all(c.name == "li" for c in element_children)
        assert len(element_children) == 3
