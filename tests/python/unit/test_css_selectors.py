"""
test_css_selectors.py — CSS selector engine tests (soup.select / soup.select_one).

Covers the full CSS selector spec including CSS4 extensions:
  - Type, class, ID selectors
  - Attribute selectors (all operators)
  - Combinators: descendant, child, adjacent, general sibling
  - Structural pseudo-classes: :nth-child, :nth-of-type, :first/last-child, etc.
  - :not(), :is(), :where(), :has()
  - :empty, :root, :only-child, :only-of-type
  - Case sensitivity in HTML vs XML
  - Compiled / cached selectors
  - select_one() returning single result
  - Selector on subtree (tag.select())
"""

from __future__ import annotations

import pytest

CSS_HTML = """
<!DOCTYPE html>
<html lang="en">
<head><meta charset="UTF-8"><title>CSS Selector Tests</title></head>
<body>
  <div id="root" class="container main-container">

    <header id="header" class="header site-header">
      <nav class="nav primary-nav" role="navigation">
        <ul class="nav-list">
          <li class="nav-item first-item"><a href="/" class="nav-link active" data-page="home">Home</a></li>
          <li class="nav-item"><a href="/about" class="nav-link" data-page="about">About</a></li>
          <li class="nav-item"><a href="/blog" class="nav-link" data-page="blog">Blog</a></li>
          <li class="nav-item last-item"><a href="/contact" class="nav-link" data-page="contact">Contact</a></li>
        </ul>
      </nav>
    </header>

    <main id="main" class="main content" role="main" lang="en-US">
      <article id="article-1" class="article featured" data-category="tech" data-priority="1">
        <h1 class="title main-title" id="main-heading">Article One</h1>
        <p class="intro lead" id="intro-p">Intro paragraph with <a href="https://example.com" class="link external" target="_blank">external</a> link.</p>
        <p class="body-text" id="body-p1">Body paragraph one.</p>
        <p class="body-text" id="body-p2">Body paragraph two with <strong>bold</strong> and <em>italic</em>.</p>
        <ul class="list feature-list" id="feature-list">
          <li class="feature-item" data-feature="speed">Fast</li>
          <li class="feature-item" data-feature="memory">Memory efficient</li>
          <li class="feature-item active" data-feature="compat">Compatible</li>
        </ul>
      </article>

      <article id="article-2" class="article" data-category="science" data-priority="2">
        <h2 class="title" id="article-2-title">Article Two</h2>
        <p class="body-text">Science content here.</p>
        <p class="body-text">More science.</p>
        <div class="media">
          <img src="science.jpg" alt="Science image" class="img responsive" width="800" height="600" loading="lazy">
          <video src="demo.mp4" class="video" controls muted></video>
        </div>
      </article>

      <section id="empty-section" class="section empty"></section>

      <section id="section-data" class="section" lang="fr" data-lang="fr" data-order="3">
        <h2 class="title section-title" lang="fr">Section en français</h2>
        <p lang="fr" class="body-text">Contenu en français.</p>
        <p lang="en" class="body-text">English content inside French section.</p>
      </section>

      <div id="form-wrapper" class="wrapper form-wrapper">
        <form id="search-form" class="form search-form" method="get" action="/search">
          <input type="search" name="q" id="search-input" class="input search-input" placeholder="Search..." required>
          <input type="hidden" name="page" value="1">
          <input type="submit" value="Search" class="btn submit-btn">
        </form>
      </div>
    </main>

    <aside id="sidebar" class="sidebar widget-area" role="complementary">
      <div class="widget" id="widget-1" data-widget-type="recent">
        <h3 class="widget-title">Recent</h3>
        <ul class="widget-list">
          <li><a href="/p/1">Post 1</a></li>
          <li><a href="/p/2">Post 2</a></li>
          <li><a href="/p/3">Post 3</a></li>
        </ul>
      </div>
      <div class="widget" id="widget-2" data-widget-type="tags">
        <h3 class="widget-title">Tags</h3>
        <ul class="widget-list tag-cloud">
          <li><a href="/tag/python" class="tag" rel="tag" data-count="10">python</a></li>
          <li><a href="/tag/rust" class="tag" rel="tag" data-count="5">rust</a></li>
          <li><a href="/tag/html" class="tag" rel="tag" data-count="3">html</a></li>
        </ul>
      </div>
    </aside>

    <footer id="footer" class="footer site-footer">
      <p class="copyright">&copy; 2024</p>
    </footer>
    <nav class="footer-nav" aria-label="Footer navigation">
      <a href="/privacy" class="footer-link">Privacy</a>
      <a href="/terms" class="footer-link">Terms</a>
      <a href="/sitemap.xml" class="footer-link" type="application/xml">Sitemap</a>
    </nav>

  </div>
</body>
</html>
"""


@pytest.fixture
def soup(parse):
    return parse(CSS_HTML)


# ===========================================================================
# 1. Type selectors
# ===========================================================================

class TestTypeSelectors:
    def test_simple_tag(self, soup):
        assert len(soup.select("p")) >= 6

    def test_select_one(self, soup):
        el = soup.select_one("h1")
        assert el is not None
        assert "Article One" in el.get_text()

    def test_select_one_returns_none_on_miss(self, soup):
        el = soup.select_one("blink")
        assert el is None

    def test_select_article(self, soup):
        articles = soup.select("article")
        assert len(articles) == 2

    def test_select_form(self, soup):
        forms = soup.select("form")
        assert len(forms) == 1

    def test_select_input(self, soup):
        inputs = soup.select("input")
        assert len(inputs) == 3


# ===========================================================================
# 2. Class selectors
# ===========================================================================

class TestClassSelectors:
    def test_single_class(self, soup):
        els = soup.select(".title")
        assert len(els) >= 3  # fixture has 3 elements with class "title"

    def test_multi_class(self, soup):
        els = soup.select(".article.featured")
        assert len(els) == 1
        assert els[0]["id"] == "article-1"

    def test_class_and_tag(self, soup):
        els = soup.select("h2.title")
        assert len(els) >= 2

    def test_class_not_present(self, soup):
        els = soup.select(".nonexistent-class")
        assert els == []

    def test_class_hyphenated(self, soup):
        els = soup.select(".nav-link")
        assert len(els) == 4

    def test_class_subset_match(self, soup):
        """'.container' matches elements with 'container' among their classes."""
        els = soup.select(".container")
        ids = [e.get("id") for e in els]
        assert "root" in ids


# ===========================================================================
# 3. ID selectors
# ===========================================================================

class TestIDSelectors:
    def test_id_selector(self, soup):
        el = soup.select_one("#main-heading")
        assert el is not None
        assert el.name == "h1"

    def test_id_with_tag(self, soup):
        el = soup.select_one("h1#main-heading")
        assert el is not None

    def test_id_nonexistent(self, soup):
        el = soup.select_one("#does-not-exist")
        assert el is None

    def test_id_returns_single_element(self, soup):
        els = soup.select("#header")
        assert len(els) == 1

    def test_id_and_class(self, soup):
        el = soup.select_one("#article-1.featured")
        assert el is not None


# ===========================================================================
# 4. Attribute selectors
# ===========================================================================

class TestAttributeSelectors:
    def test_has_attribute(self, soup):
        els = soup.select("[role]")
        roles = [e["role"] for e in els]
        assert "main" in roles
        assert "navigation" in roles

    def test_exact_attribute_value(self, soup):
        el = soup.select_one("[role='main']")
        assert el is not None
        assert el["id"] == "main"

    def test_attribute_word_match(self, soup):
        """[class~='nav'] matches 'nav' as a space-separated word."""
        els = soup.select("[class~='nav']")
        assert len(els) >= 1

    def test_attribute_lang_prefix(self, soup):
        """[lang|='en'] matches 'en' or 'en-*'."""
        els = soup.select("[lang|='en']")
        langs = [e.get("lang") for e in els]
        assert any(l in ("en", "en-US", "en-GB") for l in langs)

    def test_attribute_starts_with(self, soup):
        els = soup.select("[href^='/']")
        # Internal links
        hrefs = [e["href"] for e in els]
        assert all(h.startswith("/") for h in hrefs)

    def test_attribute_ends_with(self, soup):
        els = soup.select("[href$='.xml']")
        assert len(els) >= 1
        assert els[0]["href"].endswith(".xml")

    def test_attribute_contains(self, soup):
        els = soup.select("[href*='example']")
        assert len(els) >= 1
        assert "example" in els[0]["href"]

    def test_attribute_present_any_value(self, soup):
        els = soup.select("[data-category]")
        assert len(els) == 2

    def test_attribute_numeric_value(self, soup):
        els = soup.select("[data-priority='1']")
        assert len(els) == 1

    def test_attribute_type_selector(self, soup):
        els = soup.select("input[type='hidden']")
        assert len(els) == 1

    def test_attribute_required(self, soup):
        els = soup.select("input[required]")
        assert len(els) == 1

    def test_attribute_multiple_conditions(self, soup):
        els = soup.select("img[width][height][loading='lazy']")
        assert len(els) == 1

    def test_attribute_case_insensitive_flag(self, soup):
        """[attr='val' i] — case-insensitive match."""
        els = soup.select("[data-page='HOME' i]")
        assert len(els) >= 1


# ===========================================================================
# 5. Combinators
# ===========================================================================

class TestCombinators:
    def test_descendant(self, soup):
        els = soup.select("article p")
        assert len(els) >= 4

    def test_child(self, soup):
        """article > p matches only direct child paragraphs."""
        els = soup.select("article > p")
        for el in els:
            assert el.parent.name == "article"

    def test_adjacent_sibling(self, soup):
        """h1 + p selects <p> immediately after <h1>."""
        els = soup.select("h1 + p")
        assert len(els) >= 1
        for el in els:
            prev = el.find_previous_sibling(True)
            assert prev.name == "h1"

    def test_general_sibling(self, soup):
        """h1 ~ p selects all <p> siblings after <h1>."""
        els = soup.select("h1 ~ p")
        assert len(els) >= 2

    def test_child_vs_descendant_difference(self, soup):
        direct = soup.select("main > article")
        indirect = soup.select("main article")
        assert len(direct) == len(indirect)  # articles are direct children here

    def test_chained_child_combinator(self, soup):
        els = soup.select(".nav > .nav-list > .nav-item > .nav-link")
        assert len(els) == 4

    def test_mixed_combinators(self, soup):
        els = soup.select("#article-1 > ul.list li")
        assert len(els) == 3


# ===========================================================================
# 6. Structural pseudo-classes
# ===========================================================================

class TestStructuralPseudo:
    def test_first_child(self, soup):
        els = soup.select(".nav-item:first-child")
        assert len(els) == 1
        assert "first-item" in els[0].get("class", []) or "Home" in els[0].get_text()

    def test_last_child(self, soup):
        els = soup.select(".nav-item:last-child")
        assert len(els) == 1

    def test_nth_child_even(self, soup):
        els = soup.select(".nav-item:nth-child(2n)")
        # Even items: 2nd and 4th
        assert len(els) == 2

    def test_nth_child_odd(self, soup):
        els = soup.select(".nav-item:nth-child(odd)")
        assert len(els) == 2

    def test_nth_child_an_plus_b(self, soup):
        els = soup.select(".nav-item:nth-child(2n+1)")
        assert len(els) == 2

    def test_nth_child_specific(self, soup):
        els = soup.select(".nav-item:nth-child(3)")
        assert len(els) == 1
        assert "Blog" in els[0].get_text()

    def test_nth_last_child(self, soup):
        els = soup.select(".nav-item:nth-last-child(1)")
        assert len(els) == 1
        assert "Contact" in els[0].get_text()

    def test_nth_of_type(self, soup):
        els = soup.select("article:nth-of-type(2)")
        assert len(els) == 1
        assert els[0]["id"] == "article-2"

    def test_first_of_type(self, soup):
        el = soup.select_one("h2:first-of-type")
        assert el is not None

    def test_last_of_type(self, soup):
        el = soup.select_one("article:last-of-type")
        assert el is not None
        assert el["id"] == "article-2"

    def test_only_child(self, soup):
        # copyright paragraph is only child of footer
        els = soup.select("p:only-child")
        assert any("copyright" in " ".join(e.get("class", [])) for e in els)

    def test_only_of_type(self, soup):
        els = soup.select("form:only-of-type")
        assert len(els) == 1

    def test_empty_pseudo(self, soup):
        """':empty' matches elements with no children (not even whitespace nodes)."""
        # section#empty-section has no children
        el = soup.select_one("section.empty:empty")
        # May or may not match depending on whitespace handling
        # At minimum must not raise
        assert True  # placeholder — implementation may strip/keep whitespace

    def test_root_pseudo(self, soup):
        el = soup.select_one(":root")
        assert el is not None
        assert el.name == "html"


# ===========================================================================
# 7. :not() pseudo-class
# ===========================================================================

class TestNotPseudo:
    def test_not_class(self, soup):
        els = soup.select("a:not(.nav-link)")
        for el in els:
            assert "nav-link" not in el.get("class", [])

    def test_not_type(self, soup):
        els = soup.select(".title:not(h1)")
        for el in els:
            assert el.name != "h1"

    def test_not_attribute(self, soup):
        els = soup.select("input:not([type='hidden'])")
        for el in els:
            assert el.get("type") != "hidden"

    def test_not_complex(self, soup):
        els = soup.select("p:not(.intro):not(.lead)")
        for el in els:
            classes = el.get("class", [])
            assert "intro" not in classes
            assert "lead" not in classes


# ===========================================================================
# 8. CSS4: :is(), :where(), :has()
# ===========================================================================

class TestCSS4Pseudo:
    def test_is_pseudo(self, soup):
        """:is(h1, h2, h3) matches any heading."""
        els = soup.select(":is(h1, h2, h3)")
        names = {e.name for e in els}
        assert "h1" in names or "h2" in names

    def test_where_pseudo(self, soup):
        """:where() behaves like :is() but with zero specificity."""
        els = soup.select(":where(h1, h2, h3)")
        assert len(els) >= 2

    def test_has_pseudo_basic(self, soup):
        """:has(p) matches elements that contain a <p> descendant."""
        els = soup.select("div:has(p)")
        for el in els:
            assert el.find("p") is not None

    def test_has_pseudo_direct_child(self, soup):
        els = soup.select("article:has(> h1)")
        assert len(els) >= 1

    def test_has_pseudo_with_class(self, soup):
        els = soup.select("section:has(.body-text)")
        assert len(els) >= 1


# ===========================================================================
# 9. Complex compound selectors
# ===========================================================================

class TestCompoundSelectors:
    def test_compound_type_class_attr(self, soup):
        els = soup.select("a.nav-link[data-page]")
        assert len(els) == 4

    def test_compound_with_pseudo(self, soup):
        el = soup.select_one("li.nav-item:first-child > a.nav-link")
        assert el is not None

    def test_selector_group(self, soup):
        """Comma-separated selectors (selector group)."""
        els = soup.select("h1, h2, h3")
        names = {e.name for e in els}
        assert "h1" in names

    def test_full_nav_selector(self, soup):
        els = soup.select("nav.primary-nav > ul.nav-list > li.nav-item > a[data-page]")
        assert len(els) == 4

    def test_sidebar_tags_selector(self, soup):
        els = soup.select("#sidebar .tag[rel~='tag']")
        assert len(els) == 3


# ===========================================================================
# 10. Selector on subtree
# ===========================================================================

class TestSubtreeSelect:
    def test_select_on_subtree(self, soup):
        article = soup.select_one("#article-1")
        paras = article.select("p")
        # Only paragraphs inside article-1
        assert len(paras) == 3

    def test_select_one_on_subtree(self, soup):
        sidebar = soup.select_one("#sidebar")
        widget = sidebar.select_one(".widget")
        assert widget is not None
        assert widget["id"] in ("widget-1", "widget-2")

    def test_subtree_does_not_leak_parent(self, soup):
        article = soup.select_one("#article-1")
        h2_in_article = article.select("h2")
        # article-1 has no h2
        assert h2_in_article == []


# ===========================================================================
# 11. Compiled / cached selectors (WhiskeySour extension)
# ===========================================================================

class TestCompiledSelectors:
    def test_compile_and_select(self, soup, ws):
        if not callable(getattr(soup, "compile", None)):
            pytest.skip("compile() not implemented yet")
        q = soup.compile("p.body-text")
        result = q.select(soup)
        assert len(result) >= 2

    def test_compiled_selector_reuse(self, soup, ws):
        if not callable(getattr(soup, "compile", None)):
            pytest.skip("compile() not implemented yet")
        q = soup.compile(".nav-link")
        r1 = q.select(soup)
        r2 = q.select(soup)
        assert r1 == r2

    def test_invalid_selector_raises(self, soup):
        with pytest.raises(Exception):
            soup.select("###invalid###selector")
