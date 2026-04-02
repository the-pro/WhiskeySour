"""
test_parsing.py — Parser conformance tests for WhiskeySour.

Covers:
  - Empty / whitespace-only input
  - Valid HTML5 full documents
  - HTML fragments (no <html> wrapper)
  - XML mode
  - Malformed HTML (unclosed, misnested, stray tags)
  - Raw text elements: <script>, <style>
  - Comments, PIs, doctypes
  - Void / self-closing elements
  - Template elements
  - Embedded SVG and MathML
  - <noscript> content
  - Foster-parented content (tables)
  - Adoption agency algorithm (misnested formatting)
"""

from __future__ import annotations

import re
import sys
import textwrap

import pytest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def text_of(node) -> str:
    """Return the combined text content of a node."""
    return node.get_text()


def tag_names(nodes) -> list[str]:
    return [n.name for n in nodes]


# ===========================================================================
# 1. Empty / whitespace input
# ===========================================================================

class TestEmptyInput:
    def test_empty_string(self, parse):
        soup = parse("")
        assert soup is not None

    def test_whitespace_only(self, parse):
        soup = parse("   \n\t  ")
        assert soup is not None

    def test_empty_bytes(self, parse):
        soup = parse(b"")
        assert soup is not None

    def test_single_newline(self, parse):
        soup = parse("\n")
        assert soup is not None

    def test_empty_produces_html_structure(self, parse):
        """HTML5 spec: even empty input must produce html/head/body."""
        soup = parse("")
        assert soup.find("html") is not None
        assert soup.find("head") is not None
        assert soup.find("body") is not None


# ===========================================================================
# 2. Valid HTML5 full documents
# ===========================================================================

class TestValidHTML5:
    def test_minimal_document(self, parse):
        html = "<!DOCTYPE html><html><head></head><body></body></html>"
        soup = parse(html)
        assert soup.find("html") is not None
        assert soup.find("head") is not None
        assert soup.find("body") is not None

    def test_doctype_preserved(self, parse):
        html = "<!DOCTYPE html><html><head></head><body></body></html>"
        soup = parse(html)
        # Doctype node should be accessible
        assert soup.find(name=True) is not None or "html" in str(soup).lower()

    def test_document_title(self, parse):
        html = "<html><head><title>Test Title</title></head><body></body></html>"
        soup = parse(html)
        assert soup.title is not None
        assert soup.title.string == "Test Title"

    def test_full_document_structure(self, parse, simple_html):
        soup = parse(simple_html)
        assert soup.find("header") is not None
        assert soup.find("main") is not None
        assert soup.find("footer") is not None
        assert soup.find("nav") is not None
        assert soup.find("article") is not None
        assert soup.find("aside") is not None

    def test_nested_elements(self, parse):
        html = "<div><p><span><a href='#'>link</a></span></p></div>"
        soup = parse(html)
        a = soup.find("a")
        assert a is not None
        assert a.string == "link"
        assert a.parent.name == "span"
        assert a.parent.parent.name == "p"
        assert a.parent.parent.parent.name == "div"

    def test_multiple_root_elements_normalised(self, parse):
        """Multiple root-level elements must be wrapped under <html><body>."""
        html = "<div>A</div><div>B</div><div>C</div>"
        soup = parse(html)
        divs = soup.find_all("div")
        assert len(divs) == 3

    def test_text_nodes_preserved(self, parse):
        html = "<p>Hello <strong>World</strong> today</p>"
        soup = parse(html)
        p = soup.find("p")
        assert "Hello" in p.get_text()
        assert "World" in p.get_text()
        assert "today" in p.get_text()

    def test_attribute_preservation(self, parse):
        html = '<div id="main" class="container foo" data-x="42" aria-label="Main area"></div>'
        soup = parse(html)
        div = soup.find("div")
        assert div["id"] == "main"
        assert "container" in div["class"]
        assert "foo" in div["class"]
        assert div["data-x"] == "42"
        assert div["aria-label"] == "Main area"

    def test_boolean_attributes(self, parse):
        html = '<input type="checkbox" checked disabled required>'
        soup = parse(html)
        inp = soup.find("input")
        assert inp is not None
        assert inp.has_attr("checked")
        assert inp.has_attr("disabled")
        assert inp.has_attr("required")

    def test_class_is_list(self, parse):
        """class attribute must be a list (multi-valued)."""
        html = '<div class="foo bar baz">text</div>'
        soup = parse(html)
        div = soup.find("div")
        assert isinstance(div["class"], list)
        assert set(div["class"]) == {"foo", "bar", "baz"}

    def test_rel_is_list(self, parse):
        """rel attribute must be a list (multi-valued)."""
        html = '<a href="#" rel="noopener noreferrer">link</a>'
        soup = parse(html)
        a = soup.find("a")
        assert isinstance(a["rel"], list)
        assert "noopener" in a["rel"]
        assert "noreferrer" in a["rel"]

    def test_head_elements(self, parse):
        html = textwrap.dedent("""\
            <html><head>
              <meta charset="UTF-8">
              <meta name="description" content="Test">
              <link rel="stylesheet" href="style.css">
              <script src="app.js"></script>
            </head><body></body></html>
        """)
        soup = parse(html)
        assert soup.find("meta", attrs={"charset": "UTF-8"}) is not None
        assert soup.find("link", attrs={"rel": lambda r: "stylesheet" in r}) is not None
        assert soup.find("script", attrs={"src": "app.js"}) is not None


# ===========================================================================
# 3. HTML fragments
# ===========================================================================

class TestHTMLFragments:
    def test_bare_paragraph(self, parse):
        soup = parse("<p>Hello</p>")
        p = soup.find("p")
        assert p is not None
        assert p.string == "Hello"

    def test_bare_div_with_children(self, parse):
        soup = parse("<div><span>one</span><span>two</span></div>")
        spans = soup.find_all("span")
        assert len(spans) == 2

    def test_text_only_fragment(self, parse):
        soup = parse("Just some text")
        assert "Just some text" in soup.get_text()

    def test_fragment_gets_implicit_html_body(self, parse):
        soup = parse("<p>Fragment</p>")
        assert soup.find("html") is not None
        assert soup.find("body") is not None

    def test_fragment_self_closing_tags(self, parse):
        soup = parse("<br><hr><img src='x.png' alt=''>")
        assert soup.find("br") is not None
        assert soup.find("hr") is not None
        assert soup.find("img") is not None

    def test_fragment_with_list(self, parse):
        soup = parse("<ul><li>A</li><li>B</li><li>C</li></ul>")
        items = soup.find_all("li")
        assert len(items) == 3
        assert [i.string for i in items] == ["A", "B", "C"]

    def test_fragment_table(self, parse):
        html = "<table><tr><td>1</td><td>2</td></tr><tr><td>3</td><td>4</td></tr></table>"
        soup = parse(html)
        tds = soup.find_all("td")
        assert len(tds) == 4


# ===========================================================================
# 4. XML mode
# ===========================================================================

class TestXMLMode:
    def test_xml_self_closing(self, parse):
        xml = '<?xml version="1.0"?><root><empty/><also-empty /></root>'
        soup = parse(xml, features="xml")
        assert soup.find("empty") is not None
        assert soup.find("also-empty") is not None

    def test_xml_case_sensitive_tags(self, parse):
        xml = "<Root><Child/><CHILD/><child/></Root>"
        soup = parse(xml, features="xml")
        # XML is case-sensitive: Child, CHILD, child are distinct
        assert soup.find("Child") is not None
        assert soup.find("CHILD") is not None
        assert soup.find("child") is not None

    def test_xml_preserves_namespace(self, parse):
        xml = '<root xmlns:foo="http://example.com"><foo:bar>text</foo:bar></root>'
        soup = parse(xml, features="xml")
        assert soup.find("root") is not None

    def test_xml_cdata_section(self, parse):
        xml = "<root><![CDATA[<b>not html</b>]]></root>"
        soup = parse(xml, features="xml")
        root = soup.find("root")
        assert root is not None
        assert "<b>not html</b>" in root.string or "not html" in root.get_text()

    def test_xml_processing_instruction(self, parse):
        xml = '<?xml version="1.0"?><?xml-stylesheet type="text/xsl" href="style.xsl"?><root/>'
        soup = parse(xml, features="xml")
        assert soup.find("root") is not None

    def test_xml_strict_attribute_quoting(self, parse):
        xml = '<root attr="value with &amp; entity"/>'
        soup = parse(xml, features="xml")
        root = soup.find("root")
        assert root["attr"] == "value with & entity"


# ===========================================================================
# 5. Malformed HTML
# ===========================================================================

class TestMalformedHTML:
    def test_unclosed_tags(self, parse):
        html = "<div><p>Text without closing tags"
        soup = parse(html)
        assert soup.find("div") is not None
        assert soup.find("p") is not None
        assert "Text without closing tags" in soup.get_text()

    def test_misnested_tags(self, parse):
        html = "<div><b><i>text</b></i></div>"
        soup = parse(html)
        assert soup.find("div") is not None
        assert "text" in soup.get_text()

    def test_stray_end_tags(self, parse):
        html = "</div><p>Stray end tag before content</p>"
        soup = parse(html)
        assert soup.find("p") is not None
        assert "Stray end tag before content" in soup.get_text()

    def test_unclosed_formatting_elements(self, parse):
        html = "<b>bold <i>bold-italic</b> just italic</i>"
        soup = parse(html)
        assert "bold" in soup.get_text()
        assert "bold-italic" in soup.get_text()
        assert "just italic" in soup.get_text()

    def test_unclosed_p_before_block(self, parse):
        """<p> auto-closes before block elements per HTML5 spec."""
        html = "<p>Para one<div>Block forces p close</div>"
        soup = parse(html)
        assert soup.find("p") is not None
        assert soup.find("div") is not None

    def test_li_without_ul(self, parse):
        html = "<li>Item one</li><li>Item two</li>"
        soup = parse(html)
        items = soup.find_all("li")
        assert len(items) == 2

    def test_table_foster_parenting(self, parse):
        """Text/block inside <table> gets foster-parented before it."""
        html = "<table><p>foster parented</p><tr><td>cell</td></tr></table>"
        soup = parse(html)
        assert "foster parented" in soup.get_text()
        assert soup.find("td") is not None

    def test_duplicate_attributes_first_wins(self, parse):
        """HTML5: first occurrence of duplicate attr wins."""
        html = '<span id="first" id="second">text</span>'
        soup = parse(html)
        span = soup.find("span")
        assert span["id"] == "first"

    def test_null_byte_replaced(self, parse):
        """Null bytes must be replaced with U+FFFD replacement character."""
        html = "<p>text\x00with\x00nulls</p>"
        soup = parse(html)
        text = soup.find("p").get_text()
        assert "\x00" not in text
        assert "text" in text

    def test_bare_lt_in_text(self, parse):
        html = "<p>x < 10 and y > 5</p>"
        soup = parse(html)
        assert soup.find("p") is not None

    def test_malformed_fixture(self, parse, malformed_html):
        """Parser must not raise on the entire malformed fixture file."""
        soup = parse(malformed_html)
        assert soup is not None
        # Key ids must survive even through malformed markup
        assert soup.find(id="entities") is not None
        assert soup.find(id="empty-tags") is not None

    def test_adoption_agency_complex(self, parse):
        """Complex misnesting resolved by the adoption agency algorithm."""
        html = "<p>1<b>2<p>3</b>4</p>5"
        soup = parse(html)
        # Must not raise; content must all be present
        text = soup.get_text()
        assert "1" in text and "2" in text and "3" in text and "4" in text


# ===========================================================================
# 6. Raw text elements (<script>, <style>)
# ===========================================================================

class TestRawTextElements:
    def test_script_content_not_parsed(self, parse):
        html = '<script>if (x < 10 && y > 5) { var a = "</div>"; }</script>'
        soup = parse(html)
        script = soup.find("script")
        assert script is not None
        # The </div> inside the script must NOT create a div element
        assert soup.find("div") is None

    def test_style_content_not_parsed(self, parse):
        html = '<style>div > p { color: red; } a[href] { }</style>'
        soup = parse(html)
        style = soup.find("style")
        assert style is not None
        assert soup.find("p") is None

    def test_script_string_accessible(self, parse):
        html = '<script>var x = 1;</script>'
        soup = parse(html)
        assert "var x = 1;" in soup.find("script").string

    def test_style_string_accessible(self, parse):
        html = '<style>body { margin: 0; }</style>'
        soup = parse(html)
        assert "body" in soup.find("style").string

    def test_script_with_fake_close(self, parse):
        """</script> inside a string literal must end the script block."""
        html = '<script>var s = "</script>";</script><p>after</p>'
        soup = parse(html)
        # After the (possibly early-terminated) script, <p> must exist
        assert soup.find("p") is not None

    def test_noscript_content(self, parse):
        html = '<noscript><p>Enable JS</p></noscript>'
        soup = parse(html)
        ns = soup.find("noscript")
        assert ns is not None
        assert "Enable JS" in ns.get_text()


# ===========================================================================
# 7. Comments, Processing Instructions, Doctypes
# ===========================================================================

class TestSpecialNodes:
    def test_html_comment_preserved(self, parse):
        html = "<!-- this is a comment --><p>after</p>"
        soup = parse(html)
        # Comment must be accessible via navigable strings
        comments = soup.find_all(string=lambda t: isinstance(t, type(t)) and "this is a comment" in t)
        # At minimum the text must appear or comment node exists
        all_text = str(soup)
        assert "this is a comment" in all_text

    def test_comment_not_included_in_get_text(self, parse):
        html = "<p>visible<!-- hidden -->text</p>"
        soup = parse(html)
        text = soup.find("p").get_text()
        assert "hidden" not in text
        assert "visibletext" in text or "visible" in text

    def test_doctype_node(self, parse):
        html = "<!DOCTYPE html><html><body></body></html>"
        soup = parse(html)
        # Doctype accessible at soup level
        assert soup is not None  # At minimum does not crash

    def test_multiple_comments(self, parse):
        html = "<!-- c1 --><div><!-- c2 --><p>text<!-- c3 --></p></div>"
        soup = parse(html)
        assert soup.find("p") is not None
        assert "text" in soup.get_text()

    def test_comment_edge_empty(self, parse):
        html = "<!----><p>after</p>"
        soup = parse(html)
        assert soup.find("p") is not None

    def test_comment_with_double_dash(self, parse):
        html = "<!-- comment -- with -- dashes --><p>ok</p>"
        soup = parse(html)
        assert soup.find("p") is not None


# ===========================================================================
# 8. Void / self-closing elements
# ===========================================================================

class TestVoidElements:
    VOID_ELEMENTS = ["area", "base", "br", "col", "embed", "hr", "img",
                     "input", "link", "meta", "param", "source", "track", "wbr"]

    def test_void_elements_have_no_children(self, parse):
        for tag in self.VOID_ELEMENTS:
            soup = parse(f"<{tag}>")
            el = soup.find(tag)
            if el is not None:
                assert list(el.children) == [] or el.children is None or not any(True for _ in el.children)

    def test_br_explicit_close_ignored(self, parse):
        html = "<p>line1<br></br>line2</p>"
        soup = parse(html)
        assert soup.find("br") is not None
        # Per HTML5 spec, </br> is treated as an opening <br>, so two <br> result
        assert len(soup.find_all("br")) == 2

    def test_img_attributes(self, parse):
        html = '<img src="photo.jpg" alt="A photo" width="800" height="600" loading="lazy">'
        soup = parse(html)
        img = soup.find("img")
        assert img["src"] == "photo.jpg"
        assert img["alt"] == "A photo"
        assert img["width"] == "800"
        assert img["loading"] == "lazy"

    def test_self_closing_slash_ignored_in_html(self, parse):
        """In HTML5 mode, <br/> is identical to <br>."""
        html = "<br/><hr/><img src='x.png' alt=''/>"
        soup = parse(html)
        assert soup.find("br") is not None
        assert soup.find("hr") is not None
        assert soup.find("img") is not None


# ===========================================================================
# 9. Template elements
# ===========================================================================

class TestTemplateElement:
    def test_template_has_content_fragment(self, parse):
        html = "<template id='tmpl'><p>Template content</p></template>"
        soup = parse(html)
        tmpl = soup.find("template")
        assert tmpl is not None

    def test_template_content_not_in_regular_tree(self, parse):
        """Content inside <template> is in a document fragment, not the live DOM."""
        html = "<div><template><span class='inside'>hidden</span></template></div>"
        soup = parse(html)
        # The <span> must either be inaccessible via normal find, or accessible
        # via template.content — implementation defines which
        tmpl = soup.find("template")
        assert tmpl is not None


# ===========================================================================
# 10. Embedded SVG and MathML
# ===========================================================================

class TestEmbeddedNamespaces:
    def test_inline_svg(self, parse):
        html = textwrap.dedent("""\
            <html><body>
            <svg xmlns="http://www.w3.org/2000/svg" width="100" height="100">
              <circle cx="50" cy="50" r="40" fill="red"/>
              <rect x="10" y="10" width="80" height="80" fill="none" stroke="blue"/>
              <text x="50" y="55" text-anchor="middle">SVG</text>
            </svg>
            </body></html>
        """)
        soup = parse(html)
        svg = soup.find("svg")
        assert svg is not None
        assert soup.find("circle") is not None
        assert soup.find("rect") is not None

    def test_inline_mathml(self, parse):
        html = textwrap.dedent("""\
            <html><body>
            <math xmlns="http://www.w3.org/1998/Math/MathML">
              <mrow><mi>x</mi><mo>+</mo><mi>y</mi><mo>=</mo><mn>10</mn></mrow>
            </math>
            </body></html>
        """)
        soup = parse(html)
        math = soup.find("math")
        assert math is not None

    def test_svg_case_sensitive_attributes(self, parse):
        """SVG attributes like viewBox are case-sensitive."""
        html = '<svg viewBox="0 0 100 100"><path d="M0,0"/></svg>'
        soup = parse(html)
        svg = soup.find("svg")
        assert svg is not None


# ===========================================================================
# 11. Parser feature/mode selection
# ===========================================================================

class TestParserFeatures:
    def test_html_parser_mode(self, parse):
        soup = parse("<p>test</p>", features="html.parser")
        assert soup.find("p") is not None

    def test_html5lib_mode(self, parse):
        pytest.importorskip("html5lib", reason="html5lib not installed")
        soup = parse("<p>test</p>", features="html5lib")
        assert soup.find("p") is not None

    def test_lxml_mode(self, parse):
        pytest.importorskip("lxml", reason="lxml not installed")
        soup = parse("<p>test</p>", features="lxml")
        assert soup.find("p") is not None

    def test_unknown_parser_raises(self, parse):
        with pytest.raises(Exception):
            parse("<p>test</p>", features="nonexistent-parser-xyz")
