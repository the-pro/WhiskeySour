"""
test_review_fixes.py — regression tests for codebase-review fixes.

Covers behaviour that was previously broken:
  - find_all(attr=False) must select elements that LACK the attribute
    (previously both True and False mapped to "present").
  - find_next(name, string=...) must honour the string filter
    (previously the string argument was silently ignored in the Rust core).
"""

from __future__ import annotations

import pytest

ATTR_HTML = """
<div>
  <p id="a" class="x">one</p>
  <p>two</p>
  <p id="c" class="x y">three</p>
  <span>four</span>
</div>
"""


@pytest.fixture
def soup(parse):
    return parse(ATTR_HTML)


class TestAttrBooleanFilter:
    def test_attr_false_selects_elements_without_attr(self, soup):
        # bs4 semantics: id=False → elements that do NOT have an id.
        got = [p.get_text() for p in soup.find_all("p", id=False)]
        assert got == ["two"]

    def test_attr_true_selects_elements_with_attr(self, soup):
        got = [p.get_text() for p in soup.find_all("p", id=True)]
        assert got == ["one", "three"]

    def test_attr_false_via_attrs_dict(self, soup):
        got = [p.get_text() for p in soup.find_all("p", attrs={"id": False})]
        assert got == ["two"]

    def test_class_false_selects_elements_without_class(self, soup):
        # <p>two</p> and <span>four</span> have no class.
        got = [t.get_text() for t in soup.find_all(True, class_=False)]
        assert "two" in got and "four" in got
        assert "one" not in got and "three" not in got


class TestFindNextStringFilter:
    def test_find_next_honours_string(self, soup):
        first = soup.find("p")  # "one"
        assert first.find_next("p", string="three").get_text() == "three"

    def test_find_next_string_no_match_returns_none(self, soup):
        first = soup.find("p")
        assert first.find_next("p", string="nonexistent") is None

    def test_find_next_name_only_still_works(self, soup):
        first = soup.find("p")
        assert first.find_next("p").get_text() == "two"
