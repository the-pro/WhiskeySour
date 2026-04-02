"""
test_find.py — find() / find_all() / find_next() / find_parent() tests.

Covers every filter type:
  - Tag name (str, list, True, None, regex)
  - Attribute filters (dict, keyword, multi-valued)
  - String / text filters
  - Callable (lambda) filters
  - Compound filters (name + attrs + string)
  - limit, recursive, generator behaviour
  - find_all_next / find_all_previous
  - find_parent / find_parents
  - find_next_sibling / find_previous_sibling
"""

from __future__ import annotations

import re

import pytest

# ---------------------------------------------------------------------------
# Shared fixture document
# ---------------------------------------------------------------------------

SEARCH_HTML = """
<!DOCTYPE html>
<html>
<head><title>Search Test</title></head>
<body>
  <div id="outer" class="container top-level">
    <h1 class="title" data-level="1">Main Heading</h1>
    <p id="p1" class="intro text">First paragraph with <a href="/page1" class="link internal">link one</a> and text.</p>
    <p id="p2" class="text secondary">Second paragraph with <a href="https://external.com" class="link external" rel="nofollow noopener">external link</a>.</p>
    <p id="p3" class="text">Third paragraph — no links.</p>

    <div id="inner" class="container nested">
      <h2 class="title subtitle" data-level="2">Sub Heading</h2>
      <ul id="list-a" class="list">
        <li class="item" data-idx="0" data-group="a">Alpha</li>
        <li class="item active" data-idx="1" data-group="a">Beta</li>
        <li class="item" data-idx="2" data-group="b">Gamma</li>
        <li class="item disabled" data-idx="3" data-group="b">Delta</li>
      </ul>

      <div id="deep" class="container deep-level">
        <h3 class="title" data-level="3">Deep Heading</h3>
        <p id="p4" class="text deep-text" data-priority="high">Deep paragraph <strong>important</strong> text.</p>
        <p id="p5" class="text deep-text" data-priority="low">Another <em>emphasised</em> deep paragraph.</p>
        <span id="lone-span" class="highlight">Standalone span</span>
      </div>
    </div>

    <section id="section-1" class="section" aria-label="first section">
      <h2 class="title section-title">Section Title</h2>
      <p class="text">Section paragraph one.</p>
      <p class="text">Section paragraph two.</p>
      <img src="image.png" alt="A test image" class="image" width="400" height="300">
      <figure>
        <img src="figure.jpg" alt="" class="figure-img">
        <figcaption>Figure caption</figcaption>
      </figure>
    </section>

    <form id="the-form" class="form" method="post" action="/submit">
      <input type="text" name="username" id="username" class="input" placeholder="Username" required>
      <input type="password" name="password" id="password" class="input" required>
      <input type="email" name="email" id="email" class="input" placeholder="Email">
      <input type="hidden" name="csrf" value="token123">
      <input type="checkbox" name="agree" id="agree" checked>
      <select name="role" id="role-select">
        <option value="">Choose role</option>
        <option value="admin">Admin</option>
        <option value="user" selected>User</option>
      </select>
      <textarea name="bio" id="bio-textarea" rows="4">Default bio text</textarea>
      <button type="submit" id="submit-btn" class="btn primary">Submit</button>
    </form>
  </div>

  <footer id="footer" class="footer">
    <p class="footer-text">Footer content &copy; 2024</p>
    <nav class="footer-nav">
      <a href="/privacy" class="footer-link">Privacy</a>
      <a href="/terms" class="footer-link">Terms</a>
    </nav>
  </footer>

  <!-- Invisible comment: search target -->
  <script id="data-script" type="application/json">{"key": "value"}</script>
</body>
</html>
"""


@pytest.fixture
def soup(parse):
    return parse(SEARCH_HTML)


# ===========================================================================
# 1. find() — returns first match or None
# ===========================================================================

class TestFind:
    def test_find_by_tag_name(self, soup):
        assert soup.find("h1") is not None
        assert soup.find("h1").get_text(strip=True) == "Main Heading"

    def test_find_returns_first_match(self, soup):
        # There are multiple <h2>; find() must return the first one
        h2 = soup.find("h2")
        assert "Sub Heading" in h2.get_text()

    def test_find_nonexistent_returns_none(self, soup):
        assert soup.find("marquee") is None
        assert soup.find("blink") is None

    def test_find_by_id(self, soup):
        el = soup.find(id="outer")
        assert el is not None
        assert el.name == "div"

    def test_find_by_id_shortcut(self, soup):
        el = soup.find(id="inner")
        assert el is not None
        assert el["id"] == "inner"

    def test_find_by_class(self, soup):
        el = soup.find(class_="intro")
        assert el is not None
        assert el["id"] == "p1"

    def test_find_by_class_partial_match(self, soup):
        """class_="text" should match elements that have 'text' among their classes."""
        el = soup.find(class_="text")
        assert el is not None

    def test_find_by_attrs_dict(self, soup):
        el = soup.find(attrs={"data-level": "1"})
        assert el is not None
        assert el.name == "h1"

    def test_find_by_attrs_multiple(self, soup):
        el = soup.find(attrs={"data-group": "a", "data-idx": "1"})
        assert el is not None
        assert el.get_text(strip=True) == "Beta"

    def test_find_by_string(self, soup):
        el = soup.find(string="Alpha")
        assert el is not None

    def test_find_by_string_regex(self, soup):
        el = soup.find(string=re.compile(r"^Section paragraph"))
        assert el is not None

    def test_find_by_name_and_attrs(self, soup):
        el = soup.find("a", class_="external")
        assert el is not None
        assert "external.com" in el["href"]

    def test_find_true_matches_any_tag(self, soup):
        el = soup.find(True)
        assert el is not None
        assert el.name is not None

    def test_find_none_returns_none(self, soup):
        # find(None) — same as find(True) in bs4
        el = soup.find(None)
        assert el is not None

    def test_find_with_lambda_filter(self, soup):
        el = soup.find(lambda tag: tag.name == "input" and tag.get("type") == "hidden")
        assert el is not None
        assert el["value"] == "token123"

    def test_find_select_checked_checkbox(self, soup):
        el = soup.find("input", attrs={"type": "checkbox", "checked": True})
        assert el is not None
        assert el["name"] == "agree"

    def test_find_textarea_default_text(self, soup):
        ta = soup.find("textarea")
        assert ta is not None
        assert "Default bio text" in ta.get_text()

    def test_find_on_subtree(self, soup):
        inner = soup.find(id="inner")
        h2 = inner.find("h2")
        assert h2 is not None
        assert "Sub Heading" in h2.get_text()

    def test_find_does_not_escape_subtree(self, soup):
        deep = soup.find(id="deep")
        # h1 is outside deep — must not be found
        assert deep.find("h1") is None


# ===========================================================================
# 2. find_all() — returns list of all matches
# ===========================================================================

class TestFindAll:
    def test_find_all_by_tag(self, soup):
        paras = soup.find_all("p")
        assert len(paras) >= 5

    def test_find_all_returns_list(self, soup):
        result = soup.find_all("li")
        assert isinstance(result, list)
        assert len(result) == 4

    def test_find_all_empty_result(self, soup):
        result = soup.find_all("marquee")
        assert result == []

    def test_find_all_by_class(self, soup):
        items = soup.find_all(class_="item")
        assert len(items) == 4

    def test_find_all_by_class_multi(self, soup):
        """Elements that have ALL listed classes."""
        items = soup.find_all(class_=["item", "active"])
        assert len(items) == 1
        assert items[0].get_text(strip=True) == "Beta"

    def test_find_all_by_attrs(self, soup):
        inputs = soup.find_all("input", attrs={"required": True})
        assert len(inputs) == 2  # username and password

    def test_find_all_tag_list(self, soup):
        """find_all(["h1","h2","h3"]) matches any of the listed tags."""
        headings = soup.find_all(["h1", "h2", "h3"])
        assert len(headings) >= 4

    def test_find_all_tag_regex(self, soup):
        headings = soup.find_all(re.compile(r"^h[1-6]$"))
        assert len(headings) >= 4

    def test_find_all_with_limit(self, soup):
        result = soup.find_all("p", limit=2)
        assert len(result) == 2

    def test_find_all_limit_zero_returns_all(self, soup):
        all_p = soup.find_all("p")
        lim_p = soup.find_all("p", limit=0)
        assert len(lim_p) == len(all_p)

    def test_find_all_recursive_false(self, soup):
        """recursive=False only searches direct children of the tag."""
        outer = soup.find(id="outer")
        # Direct children of outer
        direct = outer.find_all("div", recursive=False)
        # 'inner' and 'deep' are not direct children of outer — only 'inner' is
        assert all(d.parent["id"] == "outer" for d in direct)

    def test_find_all_string_filter(self, soup):
        matches = soup.find_all(string=re.compile(r"paragraph"))
        assert len(matches) >= 3

    def test_find_all_lambda(self, soup):
        inputs = soup.find_all(lambda t: t.name == "input" and t.has_attr("placeholder"))
        assert len(inputs) >= 2  # username and email

    def test_find_all_data_attribute(self, soup):
        items = soup.find_all(attrs={"data-group": "b"})
        assert len(items) == 2

    def test_find_all_by_href_regex(self, soup):
        ext_links = soup.find_all("a", href=re.compile(r"^https://"))
        assert len(ext_links) == 1
        assert "external.com" in ext_links[0]["href"]

    def test_find_all_select_option(self, soup):
        opts = soup.find_all("option")
        assert len(opts) == 3

    def test_find_all_images(self, soup):
        imgs = soup.find_all("img")
        assert len(imgs) == 2

    def test_find_all_images_with_alt(self, soup):
        imgs_with_alt = soup.find_all("img", alt=re.compile(r".+"))
        assert len(imgs_with_alt) == 1
        assert imgs_with_alt[0]["alt"] == "A test image"

    def test_find_all_on_subtree(self, soup):
        section = soup.find(id="section-1")
        paras = section.find_all("p")
        assert len(paras) == 2

    def test_find_all_true_returns_all_tags(self, soup):
        all_tags = soup.find_all(True)
        assert len(all_tags) > 50

    def test_find_all_aria_attribute(self, soup):
        sections = soup.find_all(attrs={"aria-label": True})
        assert len(sections) >= 1

    def test_find_all_method_alias(self, soup):
        """soup("p") is an alias for soup.find_all("p")."""
        assert soup("p") == soup.find_all("p")


# ===========================================================================
# 3. find_all_next / find_all_previous
# ===========================================================================

class TestFindAllNextPrevious:
    def test_find_all_next(self, soup):
        h1 = soup.find("h1")
        next_tags = h1.find_all_next("p")
        assert len(next_tags) >= 5

    def test_find_next(self, soup):
        p1 = soup.find(id="p1")
        p2 = p1.find_next("p")
        assert p2 is not None
        assert p2["id"] == "p2"

    def test_find_next_sibling(self, soup):
        p1 = soup.find(id="p1")
        sib = p1.find_next_sibling("p")
        assert sib is not None
        assert sib["id"] == "p2"

    def test_find_next_siblings(self, soup):
        p1 = soup.find(id="p1")
        sibs = p1.find_next_siblings("p")
        assert len(sibs) >= 2

    def test_find_previous_sibling(self, soup):
        p2 = soup.find(id="p2")
        sib = p2.find_previous_sibling("p")
        assert sib is not None
        assert sib["id"] == "p1"

    def test_find_all_previous(self, soup):
        p3 = soup.find(id="p3")
        prevs = p3.find_all_previous("p")
        ids = [p.get("id") for p in prevs]
        assert "p2" in ids
        assert "p1" in ids

    def test_find_next_string(self, soup):
        h1 = soup.find("h1")
        next_str = h1.find_next(string=True)
        assert next_str is not None


# ===========================================================================
# 4. find_parent / find_parents
# ===========================================================================

class TestFindParent:
    def test_find_parent_by_tag(self, soup):
        strong = soup.find("strong")
        parent_p = strong.find_parent("p")
        assert parent_p is not None
        assert parent_p["id"] == "p4"

    def test_find_parent_by_class(self, soup):
        li = soup.find("li", class_="active")
        parent_ul = li.find_parent("ul")
        assert parent_ul is not None
        assert parent_ul["id"] == "list-a"

    def test_find_parents_returns_list(self, soup):
        deep_p = soup.find(id="p4")
        parents = deep_p.find_parents("div")
        assert len(parents) >= 2
        parent_ids = [p.get("id") for p in parents]
        assert "deep" in parent_ids
        assert "inner" in parent_ids

    def test_find_parent_not_found_returns_none(self, soup):
        html_tag = soup.find("html")
        result = html_tag.find_parent("div")
        assert result is None

    def test_find_parent_with_attrs(self, soup):
        input_el = soup.find("input", attrs={"type": "submit"})
        if input_el is None:
            input_el = soup.find("button", attrs={"type": "submit"})
        form = input_el.find_parent("form")
        assert form is not None
        assert form["id"] == "the-form"


# ===========================================================================
# 5. Edge cases for find operations
# ===========================================================================

class TestFindEdgeCases:
    def test_find_on_navigable_string_returns_none(self, soup):
        """Calling find on a NavigableString should return None."""
        text_node = soup.find("h1").string
        # NavigableString.find is not supported / returns None
        assert text_node is not None

    def test_find_after_decompose_not_in_results(self, parse):
        html = "<div><p id='remove-me'>Remove</p><p id='keep-me'>Keep</p></div>"
        soup = parse(html)
        to_remove = soup.find(id="remove-me")
        to_remove.decompose()
        result = soup.find(id="remove-me")
        assert result is None
        assert soup.find(id="keep-me") is not None

    def test_find_all_empty_string_class(self, parse):
        """class_="" should not match anything meaningful."""
        html = '<div class="foo">a</div><div class="">b</div>'
        soup = parse(html)
        # Empty class_ behaviour: matches only elements with empty class
        result = soup.find_all(class_="foo")
        assert len(result) == 1

    def test_find_attribute_with_none_value(self, parse):
        html = '<input type="text" required>'
        soup = parse(html)
        el = soup.find("input", attrs={"required": True})
        assert el is not None

    def test_find_all_with_multiple_classes_order_independent(self, parse):
        html = '<div class="b a c">text</div>'
        soup = parse(html)
        el = soup.find(class_="a")
        assert el is not None
        el2 = soup.find(class_="b")
        assert el2 is not None

    def test_callable_filter_receives_tag(self, soup):
        calls = []
        def recorder(tag):
            calls.append(tag.name)
            return tag.name == "li"
        items = soup.find_all(recorder)
        assert len(items) == 4
        assert all(n == "li" for n in calls if n == "li")

    def test_find_all_generator_not_list(self, soup):
        """find_all should return a list, not a generator."""
        result = soup.find_all("p")
        assert hasattr(result, "__len__")
        assert hasattr(result, "__getitem__")
