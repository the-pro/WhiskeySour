"""
test_bs4_compat.py — BeautifulSoup API compatibility tests.

Every public API of bs4 must work identically in WhiskeySour.
Tests are parameterised so they run against both libraries when bs4 is installed,
allowing direct comparison.

Public API surface:
  - BeautifulSoup constructor (markup, features, from_encoding, ...)
  - Tag: name, attrs, string, strings, stripped_strings, get_text, find, find_all,
         select, select_one, parent, parents, children, contents, descendants,
         next_sibling, previous_sibling, next_element, previous_element,
         find_next, find_previous, find_next_sibling, find_previous_sibling,
         find_parent, encode, decode, prettify, wrap, unwrap, append, insert,
         insert_before, insert_after, replace_with, extract, decompose, clear
  - NavigableString, Comment, CData, ProcessingInstruction, Doctype
  - ResultSet: list subclass with .source
  - soup.title, soup.body, soup.head shortcuts
"""

from __future__ import annotations

import re

import pytest

pytestmark = pytest.mark.compat

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

COMPAT_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Compat Test</title>
  <link rel="stylesheet" href="style.css">
  <link rel="alternate stylesheet" href="alt.css">
</head>
<body>
  <div id="main" class="container wrapper">
    <h1 id="title" class="heading">Hello World</h1>
    <p id="p1" class="text intro">
      First paragraph with <a href="/page" class="link internal" rel="nofollow">link</a>.
    </p>
    <p id="p2" class="text">Second paragraph.</p>
    <!-- A comment -->
    <ul id="list" class="items">
      <li id="item-1" class="item">One</li>
      <li id="item-2" class="item active">Two</li>
      <li id="item-3" class="item">Three</li>
    </ul>
    <img src="image.png" alt="Test image" id="img1" class="img">
    <script id="js1">var x = 1;</script>
    <style id="css1">body { margin: 0; }</style>
  </div>
</body>
</html>
"""


@pytest.fixture
def ws_soup(parse):
    return parse(COMPAT_HTML)


@pytest.fixture
def bs4_soup():
    bs4 = pytest.importorskip("bs4", reason="bs4 not installed — compat comparison skipped")
    return bs4.BeautifulSoup(COMPAT_HTML, "html.parser")


# ===========================================================================
# 1. Constructor / module-level API
# ===========================================================================

class TestConstructor:
    def test_whiskysour_callable(self, ws):
        soup = ws.WhiskeySour(COMPAT_HTML, "html.parser")
        assert soup is not None

    def test_beautifulsoup_alias(self, ws):
        """ws.BeautifulSoup must be an alias for ws.WhiskeySour."""
        assert hasattr(ws, "BeautifulSoup")
        soup = ws.BeautifulSoup(COMPAT_HTML, "html.parser")
        assert soup is not None

    def test_constructor_bytes_input(self, ws):
        soup = ws.WhiskeySour(COMPAT_HTML.encode("utf-8"), "html.parser")
        assert soup.find("h1") is not None

    def test_constructor_from_encoding_kwarg(self, ws):
        data = COMPAT_HTML.encode("utf-8")
        soup = ws.WhiskeySour(data, "html.parser", from_encoding="utf-8")
        assert soup.find("h1") is not None


# ===========================================================================
# 2. Tag properties
# ===========================================================================

class TestTagProperties:
    def test_name(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1.name == "h1"

    def test_attrs_dict(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1.attrs["id"] == "title"
        assert isinstance(h1.attrs["class"], list)

    def test_string_single_text(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1.string == "Hello World"

    def test_string_multiple_children_none(self, ws_soup):
        p1 = ws_soup.find(id="p1")
        assert p1.string is None

    def test_strings_iterator(self, ws_soup):
        p1 = ws_soup.find(id="p1")
        strings = list(p1.strings)
        assert any("First paragraph" in s for s in strings)

    def test_stripped_strings(self, ws_soup):
        ul = ws_soup.find(id="list")
        items = list(ul.stripped_strings)
        assert "One" in items
        assert "Two" in items
        assert "Three" in items

    def test_get_text(self, ws_soup):
        ul = ws_soup.find(id="list")
        text = ul.get_text()
        assert "One" in text
        assert "Three" in text

    def test_get_text_separator(self, ws_soup):
        ul = ws_soup.find(id="list")
        text = ul.get_text(separator=",")
        assert "," in text

    def test_get_text_strip(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1.get_text(strip=True) == "Hello World"

    def test_subscript_get(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1["id"] == "title"

    def test_get_method(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1.get("id") == "title"
        assert h1.get("missing") is None
        assert h1.get("missing", "default") == "default"

    def test_has_attr(self, ws_soup):
        h1 = ws_soup.find("h1")
        assert h1.has_attr("id")
        assert not h1.has_attr("href")


# ===========================================================================
# 3. Document-level shortcuts
# ===========================================================================

class TestDocumentShortcuts:
    def test_soup_title(self, ws_soup):
        assert ws_soup.title is not None
        assert ws_soup.title.string == "Compat Test"

    def test_soup_head(self, ws_soup):
        assert ws_soup.head is not None
        assert ws_soup.head.name == "head"

    def test_soup_body(self, ws_soup):
        assert ws_soup.body is not None
        assert ws_soup.body.name == "body"

    def test_soup_html(self, ws_soup):
        assert ws_soup.html is not None
        assert ws_soup.html.name == "html"

    def test_dot_access_first_child_tag(self, ws_soup):
        """soup.div returns first <div>."""
        first_div = ws_soup.div
        assert first_div is not None
        assert first_div.name == "div"

    def test_dot_access_missing_returns_none(self, ws_soup):
        assert ws_soup.marquee is None


# ===========================================================================
# 4. find / find_all
# ===========================================================================

class TestFindFindAll:
    def test_find_returns_tag(self, ws_soup):
        el = ws_soup.find("h1")
        assert el is not None
        assert el.name is not None

    def test_find_all_returns_list(self, ws_soup):
        items = ws_soup.find_all("li")
        assert isinstance(items, list)
        assert len(items) == 3

    def test_find_all_callable_shorthand(self, ws_soup):
        assert ws_soup("li") == ws_soup.find_all("li")

    def test_result_set_is_list_subclass(self, ws_soup):
        result = ws_soup.find_all("li")
        assert isinstance(result, list)

    def test_find_none_returns_none(self, ws_soup):
        assert ws_soup.find("nonexistent") is None

    def test_find_all_empty_list(self, ws_soup):
        assert ws_soup.find_all("nonexistent") == []


# ===========================================================================
# 5. CSS select
# ===========================================================================

class TestSelect:
    def test_select_returns_list(self, ws_soup):
        result = ws_soup.select("li")
        assert isinstance(result, list)
        assert len(result) == 3

    def test_select_one_returns_tag(self, ws_soup):
        el = ws_soup.select_one("#title")
        assert el is not None
        assert el["id"] == "title"

    def test_select_one_none_on_miss(self, ws_soup):
        assert ws_soup.select_one(".nonexistent") is None

    def test_select_class(self, ws_soup):
        items = ws_soup.select(".item")
        assert len(items) == 3

    def test_select_descendant(self, ws_soup):
        links = ws_soup.select("#main a")
        assert len(links) == 1


# ===========================================================================
# 6. Tree navigation properties
# ===========================================================================

class TestTreeNavigation:
    def test_parent(self, ws_soup):
        li = ws_soup.find(id="item-1")
        assert li.parent.name == "ul"

    def test_parents(self, ws_soup):
        li = ws_soup.find(id="item-1")
        parent_names = [p.name for p in li.parents]
        assert "ul" in parent_names
        assert "div" in parent_names
        assert "body" in parent_names

    def test_children(self, ws_soup):
        ul = ws_soup.find(id="list")
        tag_children = [c for c in ul.children if c.name is not None]
        assert len(tag_children) == 3

    def test_contents(self, ws_soup):
        ul = ws_soup.find(id="list")
        assert isinstance(ul.contents, list)

    def test_descendants(self, ws_soup):
        div = ws_soup.find(id="main")
        descs = [d for d in div.descendants if d.name is not None]
        assert any(d.name == "li" for d in descs)

    def test_next_sibling(self, ws_soup):
        item1 = ws_soup.find(id="item-1")
        sib = item1.next_sibling
        while sib and sib.name is None:
            sib = sib.next_sibling
        assert sib is not None
        assert sib["id"] == "item-2"

    def test_previous_sibling(self, ws_soup):
        item2 = ws_soup.find(id="item-2")
        sib = item2.previous_sibling
        while sib and sib.name is None:
            sib = sib.previous_sibling
        assert sib["id"] == "item-1"

    def test_next_siblings(self, ws_soup):
        item1 = ws_soup.find(id="item-1")
        sibs = [s for s in item1.next_siblings if s.name is not None]
        assert len(sibs) == 2

    def test_previous_siblings(self, ws_soup):
        item3 = ws_soup.find(id="item-3")
        sibs = [s for s in item3.previous_siblings if s.name is not None]
        assert len(sibs) == 2


# ===========================================================================
# 7. NavigableString types
# ===========================================================================

class TestNavigableStringTypes:
    def test_navigable_string_is_str(self, ws, ws_soup):
        h1 = ws_soup.find("h1")
        s = h1.string
        assert isinstance(s, str)

    def test_comment_type(self, ws, parse):
        soup = parse("<!-- a comment --><p>after</p>")
        try:
            from whiskysour import Comment  # type: ignore[import]
        except ImportError:
            Comment = type(None)
        comment = soup.find(string=re.compile("a comment"))
        assert comment is not None

    def test_script_string_type(self, ws_soup):
        script = ws_soup.find(id="js1")
        assert "var x = 1;" in script.string

    def test_style_string_type(self, ws_soup):
        style = ws_soup.find(id="css1")
        assert "margin" in style.string


# ===========================================================================
# 8. Multi-valued attributes (class / rel)
# ===========================================================================

class TestMultiValuedAttrs:
    def test_class_list(self, ws_soup):
        div = ws_soup.find(id="main")
        assert isinstance(div["class"], list)
        assert "container" in div["class"]
        assert "wrapper" in div["class"]

    def test_rel_list(self, ws_soup):
        link = ws_soup.find("link", href="alt.css")
        assert isinstance(link["rel"], list)
        assert "stylesheet" in link["rel"]
        assert "alternate" in link["rel"]


# ===========================================================================
# 9. Encoding / decode
# ===========================================================================

class TestCompatEncoding:
    def test_encode_returns_bytes(self, ws_soup):
        b = ws_soup.encode("utf-8")
        assert isinstance(b, bytes)

    def test_decode_returns_str(self, ws_soup):
        s = ws_soup.decode()
        assert isinstance(s, str)

    def test_prettify_returns_str(self, ws_soup):
        s = ws_soup.prettify()
        assert isinstance(s, str)
        assert "\n" in s


# ===========================================================================
# 10. Cross-library parity (ws vs bs4)
# ===========================================================================

class TestCrossLibraryParity:
    def test_find_all_parity(self, ws_soup, bs4_soup):
        ws_items = [el.get_text(strip=True) for el in ws_soup.find_all("li")]
        bs_items = [el.get_text(strip=True) for el in bs4_soup.find_all("li")]
        assert ws_items == bs_items

    def test_get_text_parity(self, ws_soup, bs4_soup):
        import re as _re
        ws_text = _re.sub(r"\s+", " ", ws_soup.get_text()).strip()
        bs_text = _re.sub(r"\s+", " ", bs4_soup.get_text()).strip()
        assert ws_text == bs_text

    def test_title_parity(self, ws_soup, bs4_soup):
        assert ws_soup.title.string == bs4_soup.title.string

    def test_attr_parity(self, ws_soup, bs4_soup):
        ws_h1 = ws_soup.find("h1")
        bs_h1 = bs4_soup.find("h1")
        assert ws_h1["id"] == bs_h1["id"]
        assert set(ws_h1["class"]) == set(bs_h1["class"])

    def test_select_parity(self, ws_soup, bs4_soup):
        ws_items = [el["id"] for el in ws_soup.select(".item")]
        bs_items = [el["id"] for el in bs4_soup.select(".item")]
        assert ws_items == bs_items

    def test_parent_chain_parity(self, ws_soup, bs4_soup):
        ws_li = ws_soup.find(id="item-1")
        bs_li = bs4_soup.find(id="item-1")
        ws_parents = [p.name for p in ws_li.parents]
        bs_parents = [p.name for p in bs_li.parents]
        assert ws_parents == bs_parents

    def test_prettify_same_structure(self, ws_soup, bs4_soup):
        import re as _re
        ws_p = _re.sub(r"\s+", " ", ws_soup.prettify()).strip()
        bs_p = _re.sub(r"\s+", " ", bs4_soup.prettify()).strip()
        # Must contain same tags even if whitespace differs slightly
        for tag in ["<html", "<head", "<body", "<title", "<h1", "<p", "<ul", "<li"]:
            assert tag in ws_p and tag in bs_p
