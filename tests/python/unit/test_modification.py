"""
test_modification.py — Tree mutation tests.

Covers:
  - decompose() — remove node and all descendants
  - extract() — remove and return detached node
  - replace_with() — swap node with new content
  - insert(pos, tag) — insert at position
  - append() / prepend()
  - insert_before() / insert_after()
  - clear() — remove all children
  - wrap() / unwrap()
  - Modifying .string
  - Modifying .attrs dict in-place
  - new_tag() / new_string()
  - Mutation during iteration safety
"""

from __future__ import annotations

import pytest

MOD_HTML = """
<!DOCTYPE html>
<html><head><title>Modification Tests</title></head>
<body>
  <div id="root">
    <h1 id="h1" class="title">Original Heading</h1>
    <p id="p1" class="text">Paragraph <span id="sp1">one</span> text.</p>
    <p id="p2" class="text">Paragraph two.</p>
    <p id="p3" class="text">Paragraph three.</p>
    <ul id="list">
      <li id="li1" class="item">Item 1</li>
      <li id="li2" class="item">Item 2</li>
      <li id="li3" class="item">Item 3</li>
    </ul>
    <div id="inner">
      <span id="sp2">Inner span</span>
      <em id="em1">Inner emphasis</em>
    </div>
  </div>
</body>
</html>
"""


@pytest.fixture
def soup(parse):
    return parse(MOD_HTML)


# ===========================================================================
# 1. decompose()
# ===========================================================================

class TestDecompose:
    def test_decompose_removes_from_tree(self, soup):
        p2 = soup.find(id="p2")
        p2.decompose()
        assert soup.find(id="p2") is None

    def test_decompose_removes_descendants_too(self, soup):
        inner = soup.find(id="inner")
        inner.decompose()
        assert soup.find(id="inner") is None
        assert soup.find(id="sp2") is None
        assert soup.find(id="em1") is None

    def test_decompose_does_not_affect_siblings(self, soup):
        p2 = soup.find(id="p2")
        p2.decompose()
        assert soup.find(id="p1") is not None
        assert soup.find(id="p3") is not None

    def test_decompose_returns_none(self, soup):
        el = soup.find(id="p3")
        result = el.decompose()
        assert result is None

    def test_decompose_all_items_in_loop(self, soup):
        """Decomposing all li items must empty the ul."""
        for li in soup.find_all("li"):
            li.decompose()
        assert soup.find("li") is None
        ul = soup.find(id="list")
        assert ul is not None  # The <ul> itself survives


# ===========================================================================
# 2. extract()
# ===========================================================================

class TestExtract:
    def test_extract_removes_from_tree(self, soup):
        p1 = soup.find(id="p1")
        extracted = p1.extract()
        assert soup.find(id="p1") is None

    def test_extract_returns_the_node(self, soup):
        p1 = soup.find(id="p1")
        extracted = p1.extract()
        assert extracted is not None
        assert extracted["id"] == "p1"

    def test_extracted_node_is_detached(self, soup):
        p1 = soup.find(id="p1")
        extracted = p1.extract()
        assert extracted.parent is None

    def test_extracted_node_children_intact(self, soup):
        p1 = soup.find(id="p1")
        extracted = p1.extract()
        span = extracted.find(id="sp1")
        assert span is not None
        assert span.get_text() == "one"

    def test_extract_sibling_links_updated(self, soup):
        p2 = soup.find(id="p2")
        p2.extract()
        p1 = soup.find(id="p1")
        p3 = soup.find(id="p3")
        # p1 and p3 must now be adjacent siblings (possibly with whitespace between)
        sibs = [s for s in p1.next_siblings if s.name is not None]
        assert any(s["id"] == "p3" for s in sibs)


# ===========================================================================
# 3. replace_with()
# ===========================================================================

class TestReplaceWith:
    def test_replace_with_new_tag(self, soup):
        p2 = soup.find(id="p2")
        new_tag = soup.new_tag("section", id="new-section")
        new_tag.string = "Replaced content"
        p2.replace_with(new_tag)
        assert soup.find(id="p2") is None
        assert soup.find(id="new-section") is not None
        assert "Replaced content" in soup.find(id="new-section").get_text()

    def test_replace_with_string(self, soup):
        span = soup.find(id="sp1")
        span.replace_with("REPLACED")
        p1 = soup.find(id="p1")
        assert "REPLACED" in p1.get_text()
        assert soup.find(id="sp1") is None

    def test_replace_with_multiple_nodes(self, soup):
        h1 = soup.find(id="h1")
        new1 = soup.new_tag("h2")
        new1.string = "New H2"
        new2 = soup.new_tag("p")
        new2.string = "New P"
        h1.replace_with(new1, new2)
        assert soup.find(id="h1") is None
        assert soup.find("h2") is not None
        assert "New H2" in soup.get_text()

    def test_replace_with_returns_replaced_node(self, parse):
        soup = parse("<div><p id='target'>text</p></div>")
        p = soup.find(id="target")
        new = soup.new_tag("span")
        result = p.replace_with(new)
        # bs4 replace_with returns the calling tag (now detached)
        assert result is not None


# ===========================================================================
# 4. insert()
# ===========================================================================

class TestInsert:
    def test_insert_at_position(self, soup):
        ul = soup.find(id="list")
        new_li = soup.new_tag("li", id="li-new")
        new_li.string = "Inserted item"
        ul.insert(1, new_li)
        items = ul.find_all("li")
        assert items[1]["id"] == "li-new"

    def test_insert_at_zero_is_prepend(self, soup):
        ul = soup.find(id="list")
        new_li = soup.new_tag("li", id="li-first")
        new_li.string = "First"
        ul.insert(0, new_li)
        items = ul.find_all("li")
        assert items[0]["id"] == "li-first"

    def test_insert_at_end(self, soup):
        ul = soup.find(id="list")
        original_count = len(ul.find_all("li"))
        new_li = soup.new_tag("li", id="li-last")
        new_li.string = "Last"
        ul.insert(len(ul.contents), new_li)
        items = ul.find_all("li")
        assert items[-1]["id"] == "li-last"

    def test_insert_string(self, soup):
        div = soup.find(id="inner")
        div.insert(0, "Prepended text")
        assert "Prepended text" in div.get_text()


# ===========================================================================
# 5. append() / prepend()
# ===========================================================================

class TestAppendPrepend:
    def test_append_tag(self, soup):
        ul = soup.find(id="list")
        new_li = soup.new_tag("li", id="li-appended")
        new_li.string = "Appended"
        ul.append(new_li)
        items = ul.find_all("li")
        assert items[-1]["id"] == "li-appended"

    def test_append_string(self, soup):
        p1 = soup.find(id="p1")
        p1.append(" appended text")
        assert "appended text" in p1.get_text()

    def test_prepend_tag(self, soup):
        ul = soup.find(id="list")
        new_li = soup.new_tag("li", id="li-prepended")
        new_li.string = "Prepended"
        ul.prepend(new_li)
        items = ul.find_all("li")
        assert items[0]["id"] == "li-prepended"

    def test_prepend_string(self, soup):
        p1 = soup.find(id="p1")
        p1.prepend("START ")
        assert p1.get_text().startswith("START")

    def test_append_moves_existing_node(self, soup):
        """Appending a node that's already in the tree moves it (not copies)."""
        ul = soup.find(id="list")
        li1 = soup.find(id="li1")
        ul.append(li1)
        items = ul.find_all("li")
        # li1 must appear only once, and at the end
        assert items[-1]["id"] == "li1"
        assert len([i for i in items if i["id"] == "li1"]) == 1


# ===========================================================================
# 6. insert_before() / insert_after()
# ===========================================================================

class TestInsertBeforeAfter:
    def test_insert_before_tag(self, soup):
        p2 = soup.find(id="p2")
        new_p = soup.new_tag("p", id="p-before")
        new_p.string = "Before p2"
        p2.insert_before(new_p)
        sibs = [s for s in p2.previous_siblings if s.name is not None]
        assert any(s["id"] == "p-before" for s in sibs)

    def test_insert_after_tag(self, soup):
        p2 = soup.find(id="p2")
        new_p = soup.new_tag("p", id="p-after")
        new_p.string = "After p2"
        p2.insert_after(new_p)
        sibs = [s for s in p2.next_siblings if s.name is not None]
        assert any(s["id"] == "p-after" for s in sibs)

    def test_insert_before_string(self, soup):
        h1 = soup.find(id="h1")
        h1.insert_before("BEFORE HEADING\n")
        body = soup.find("body")
        assert "BEFORE HEADING" in body.get_text()

    def test_insert_after_string(self, soup):
        h1 = soup.find(id="h1")
        h1.insert_after("\nAFTER HEADING")
        body = soup.find("body")
        assert "AFTER HEADING" in body.get_text()

    def test_insert_before_multiple(self, soup):
        p2 = soup.find(id="p2")
        a = soup.new_tag("p", id="before-a")
        b = soup.new_tag("p", id="before-b")
        p2.insert_before(a, b)
        sibs = [s for s in p2.previous_siblings if s.name is not None]
        ids = [s.get("id") for s in sibs]
        assert "before-a" in ids
        assert "before-b" in ids


# ===========================================================================
# 7. clear()
# ===========================================================================

class TestClear:
    def test_clear_removes_all_children(self, soup):
        ul = soup.find(id="list")
        ul.clear()
        assert ul.find("li") is None
        assert list(c for c in ul.children if c.name is not None) == []

    def test_clear_preserves_element_itself(self, soup):
        ul = soup.find(id="list")
        ul.clear()
        assert soup.find(id="list") is not None

    def test_clear_on_leaf_element(self, soup):
        h1 = soup.find(id="h1")
        h1.clear()
        assert h1.get_text() == ""
        assert soup.find(id="h1") is not None


# ===========================================================================
# 8. wrap() / unwrap()
# ===========================================================================

class TestWrapUnwrap:
    def test_wrap(self, soup):
        p1 = soup.find(id="p1")
        wrapper = soup.new_tag("div", id="wrapper")
        p1.wrap(wrapper)
        assert soup.find(id="wrapper") is not None
        assert soup.find(id="wrapper").find(id="p1") is not None

    def test_unwrap(self, soup):
        span = soup.find(id="sp1")
        parent_p = span.parent
        span.unwrap()
        # span is gone, its text is in parent
        assert soup.find(id="sp1") is None
        assert "one" in parent_p.get_text()

    def test_wrap_returns_wrapper(self, soup):
        p1 = soup.find(id="p1")
        new_div = soup.new_tag("div", id="wrap-result")
        result = p1.wrap(new_div)
        assert result["id"] == "wrap-result"


# ===========================================================================
# 9. Modifying .string
# ===========================================================================

class TestModifyString:
    def test_set_string_on_element(self, soup):
        h1 = soup.find(id="h1")
        h1.string = "New Heading"
        assert h1.get_text() == "New Heading"

    def test_set_string_replaces_children(self, soup):
        """Setting .string on an element with multiple children replaces all children."""
        p1 = soup.find(id="p1")
        p1.string = "Simple text"
        assert p1.get_text() == "Simple text"
        assert p1.find("span") is None

    def test_set_string_on_new_tag(self, soup):
        tag = soup.new_tag("p")
        tag.string = "Hello"
        assert tag.get_text() == "Hello"


# ===========================================================================
# 10. Modifying .attrs
# ===========================================================================

class TestModifyAttrs:
    def test_set_existing_attr(self, soup):
        h1 = soup.find(id="h1")
        h1["class"] = ["title", "modified"]
        assert "modified" in h1["class"]

    def test_add_new_attr(self, soup):
        p1 = soup.find(id="p1")
        p1["data-new"] = "new-value"
        assert p1["data-new"] == "new-value"

    def test_delete_attr(self, soup):
        p1 = soup.find(id="p1")
        del p1["class"]
        assert not p1.has_attr("class")

    def test_delete_nonexistent_attr_silent(self, soup):
        p1 = soup.find(id="p1")
        # BS4-compatible: deleting a missing attribute is a no-op
        del p1["nonexistent"]  # should not raise

    def test_attrs_dict_direct_modification(self, soup):
        li1 = soup.find(id="li1")
        li1.attrs["data-x"] = "123"
        assert li1["data-x"] == "123"


# ===========================================================================
# 11. new_tag() / new_string()
# ===========================================================================

class TestNewTagNewString:
    def test_new_tag_basic(self, soup):
        tag = soup.new_tag("a")
        assert tag.name == "a"
        assert tag.attrs == {}

    def test_new_tag_with_attrs(self, soup):
        tag = soup.new_tag("a", href="/page", class_="link active")
        assert tag["href"] == "/page"

    def test_new_tag_append_to_tree(self, soup):
        body = soup.find("body")
        new_div = soup.new_tag("div", id="brand-new")
        body.append(new_div)
        assert soup.find(id="brand-new") is not None

    def test_new_string_basic(self, soup):
        s = soup.new_string("Hello, world!")
        assert str(s) == "Hello, world!"

    def test_new_string_is_navigable_string(self, soup):
        s = soup.new_string("text")
        assert isinstance(s, str)

    def test_new_string_append_to_tag(self, soup):
        p = soup.find(id="p3")
        p.append(soup.new_string(" extra"))
        assert "extra" in p.get_text()

    def test_new_tag_class_keyword(self, soup):
        """new_tag("span", class_=...) must map class_ → class."""
        tag = soup.new_tag("span", class_="highlight bold")
        assert tag.has_attr("class")
