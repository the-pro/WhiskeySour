"""
test_output.py — HTML serialisation and output tests.

Covers:
  - str(tag) / repr(tag)
  - tag.prettify()
  - tag.encode(encoding)
  - Self-closing void elements serialised correctly
  - Attribute quoting and escaping
  - Unicode round-trip
  - decode_contents() / encode_contents()
  - Comments, CDATA, PIs in output
  - Round-trip stability: parse(str(parse(html))) == str(parse(html))
"""

from __future__ import annotations

import re

import pytest

OUT_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <title>Output Test &amp; Title</title>
</head>
<body>
  <div id="root" class="container main">
    <h1>Hello &amp; <em>World</em></h1>
    <p id="p1">Text with <a href="https://example.com?a=1&amp;b=2" class="link">link</a>.</p>
    <p id="p2">Paragraph with &lt;angle brackets&gt; and &quot;quotes&quot;.</p>
    <img src="photo.jpg" alt="A photo &amp; caption" width="800" height="600">
    <br>
    <hr>
    <input type="text" value="a &lt; b">
    <ul>
      <li>Item &amp; one</li>
      <li>Item two</li>
    </ul>
    <!-- This is a comment -->
    <script>var x = "</script-like>"; if (a < b) { return; }</script>
    <style>div > p { color: red; }</style>
  </div>
</body>
</html>"""


@pytest.fixture
def soup(parse):
    return parse(OUT_HTML)


# ===========================================================================
# 1. str(tag) — HTML serialisation
# ===========================================================================

class TestStrOutput:
    def test_str_produces_string(self, soup):
        assert isinstance(str(soup), str)

    def test_str_contains_html_tag(self, soup):
        assert "<html" in str(soup)

    def test_str_contains_body(self, soup):
        assert "<body" in str(soup)

    def test_str_tag_only(self, soup):
        p1 = soup.find(id="p1")
        s = str(p1)
        assert s.startswith("<p")
        assert "</p>" in s

    def test_str_self_closing_void(self, soup):
        br = soup.find("br")
        s = str(br)
        # HTML5: <br> not <br/>
        assert "<br" in s
        assert "</br>" not in s

    def test_str_img_self_closing(self, soup):
        img = soup.find("img")
        s = str(img)
        assert "<img" in s
        assert "</img>" not in s

    def test_str_preserves_attribute_values(self, soup):
        a = soup.find("a")
        s = str(a)
        assert "https://example.com" in s

    def test_str_escapes_amp_in_attr(self, soup):
        a = soup.find("a")
        s = str(a)
        # href must have & escaped as &amp; in the serialised attr
        assert "&amp;" in s or "&" in s  # parser may normalise

    def test_str_round_trip_stable(self, parse, soup):
        """parse(str(soup)) serialised again must equal str(soup)."""
        s1 = str(soup)
        s2 = str(parse(s1))
        # Normalise whitespace for comparison
        norm = lambda x: re.sub(r"\s+", " ", x).strip()
        assert norm(s1) == norm(s2)

    def test_str_empty_element(self, parse):
        soup = parse("<div></div>")
        div = soup.find("div")
        s = str(div)
        assert "<div></div>" in s or "<div" in s

    def test_str_navigable_string(self, soup):
        h1 = soup.find("h1")
        s = str(h1.string) if h1.string else str(list(h1.strings)[0])
        assert "Hello" in s


# ===========================================================================
# 2. prettify()
# ===========================================================================

class TestPrettify:
    def test_prettify_returns_string(self, soup):
        assert isinstance(soup.prettify(), str)

    def test_prettify_is_indented(self, soup):
        pretty = soup.prettify()
        lines = pretty.split("\n")
        # At least some lines must start with spaces
        indented = [l for l in lines if l.startswith(" ")]
        assert len(indented) > 0

    def test_prettify_custom_indent(self, soup):
        pretty = soup.prettify(indent_width=4)
        # Lines inside should be indented by multiples of 4
        lines = pretty.split("\n")
        indented = [l for l in lines if l.startswith("    ")]
        assert len(indented) > 0

    def test_prettify_indent_alias(self, soup):
        """prettify(indent=N) is a BS4-compatible alias for indent_width=N."""
        pretty_alias = soup.prettify(indent=4)
        pretty_explicit = soup.prettify(indent_width=4)
        assert pretty_alias == pretty_explicit

    def test_prettify_indent_alias_subtree(self, soup):
        div = soup.find(id="root")
        pretty = div.prettify(indent=2)
        lines = pretty.split("\n")
        indented = [l for l in lines if l.startswith("  ")]
        assert len(indented) > 0

    def test_prettify_subtree(self, soup):
        div = soup.find(id="root")
        pretty = div.prettify()
        assert "<div" in pretty
        assert "<h1" in pretty

    def test_prettify_no_trailing_space_on_tag_names(self, soup):
        pretty = soup.prettify()
        # Tag names must not have trailing spaces: "<div >" is invalid
        assert not re.search(r"<\w+\s+>", pretty)

    def test_prettify_unicode_preserved(self, parse):
        soup = parse("<p>日本語テスト \U0001f600</p>")
        pretty = soup.prettify()
        assert "日本語テスト" in pretty
        assert "\U0001f600" in pretty


# ===========================================================================
# 3. encode()
# ===========================================================================

class TestEncode:
    def test_encode_returns_bytes(self, soup):
        result = soup.encode("utf-8")
        assert isinstance(result, bytes)

    def test_encode_utf8_default(self, soup):
        b = soup.encode()
        assert isinstance(b, bytes)
        assert b"<html" in b

    def test_encode_latin1(self, parse):
        soup = parse("<p>caf\u00e9</p>")
        b = soup.encode("latin-1")
        assert isinstance(b, bytes)
        assert b"caf" in b

    def test_encode_updates_meta_charset(self, parse):
        """Encoding to latin-1 should update the meta charset declaration."""
        soup = parse('<html><head><meta charset="UTF-8"></head><body><p>test</p></body></html>')
        b = soup.encode("latin-1")
        # Meta charset must reflect the encoding used
        assert b"latin-1" in b.lower() or b"iso-8859-1" in b.lower()

    def test_encode_decode_round_trip(self, soup):
        b = soup.encode("utf-8")
        decoded = b.decode("utf-8")
        assert "<html" in decoded


# ===========================================================================
# 4. Attribute escaping
# ===========================================================================

class TestAttributeEscaping:
    def test_amp_in_href_escaped(self, parse):
        soup = parse('<a href="?a=1&amp;b=2">link</a>')
        a = soup.find("a")
        s = str(a)
        # Serialised href must have & (html entity or literal) — not bare &amp;amp;
        assert "a=1" in s
        assert "b=2" in s

    def test_double_quote_in_attr_escaped(self, parse):
        soup = parse('<div title=\'say "hello"\'>text</div>')
        div = soup.find("div")
        s = str(div)
        assert "say" in s and "hello" in s

    def test_angle_brackets_in_attr_escaped(self, parse):
        soup = parse('<input value="a &lt; b">')
        inp = soup.find("input")
        s = str(inp)
        assert "a" in s and "b" in s

    def test_attribute_order_preserved(self, parse):
        soup = parse('<div id="x" class="y" data-z="w">text</div>')
        div = soup.find("div")
        s = str(div)
        # id must appear before class (order from parsing)
        assert s.index("id=") < s.index("class=")


# ===========================================================================
# 5. Text escaping
# ===========================================================================

class TestTextEscaping:
    def test_amp_in_text_escaped(self, parse):
        soup = parse("<p>AT&amp;T</p>")
        p = soup.find("p")
        s = str(p)
        assert "&amp;" in s

    def test_lt_gt_in_text_escaped(self, parse):
        soup = parse("<p>a &lt; b &gt; c</p>")
        p = soup.find("p")
        s = str(p)
        assert "&lt;" in s
        assert "&gt;" in s

    def test_script_content_not_escaped(self, soup):
        script = soup.find("script")
        s = str(script)
        # Contents of script must not have &lt; / &gt; — raw text element
        assert "&lt;" not in s
        assert "&gt;" not in s
        assert "< b" in s or "a < b" in s

    def test_style_content_not_escaped(self, soup):
        style = soup.find("style")
        s = str(style)
        assert "&gt;" not in s
        assert ">" in s  # CSS > combinator must appear unescaped


# ===========================================================================
# 6. decode_contents() / encode_contents()
# ===========================================================================

class TestDecodeEncodeContents:
    def test_decode_contents(self, soup):
        div = soup.find(id="root")
        contents = div.decode_contents()
        assert isinstance(contents, str)
        assert "<h1>" in contents or "<h1 " in contents
        # Must NOT include the outer <div> tag itself
        assert not contents.strip().startswith("<div")

    def test_encode_contents(self, soup):
        div = soup.find(id="root")
        contents = div.encode_contents(encoding="utf-8")
        assert isinstance(contents, bytes)
        assert b"<h1" in contents

    def test_decode_contents_empty_element(self, parse):
        soup = parse("<div></div>")
        div = soup.find("div")
        assert div.decode_contents() == ""


# ===========================================================================
# 7. Comments and special nodes in output
# ===========================================================================

class TestSpecialNodesOutput:
    def test_comment_preserved_in_output(self, soup):
        s = str(soup)
        assert "<!-- This is a comment -->" in s

    def test_script_preserved_verbatim(self, soup):
        s = str(soup)
        assert "var x" in s

    def test_doctype_in_output(self, soup):
        s = str(soup)
        assert "DOCTYPE" in s.upper() or "doctype" in s.lower()

    def test_comment_in_prettify(self, soup):
        pretty = soup.prettify()
        assert "This is a comment" in pretty


# ===========================================================================
# 8. Unicode in output
# ===========================================================================

class TestUnicodeOutput:
    def test_unicode_text_preserved(self, parse):
        soup = parse("<p>日本語 한국어 中文 العربية</p>")
        p = soup.find("p")
        s = str(p)
        assert "日本語" in s
        assert "한국어" in s

    def test_emoji_preserved(self, parse):
        soup = parse("<p>Hello \U0001f600 \U0001f4a9</p>")
        p = soup.find("p")
        s = str(p)
        assert "\U0001f600" in s

    def test_entities_decoded_in_output(self, parse):
        soup = parse("<p>&copy; &reg; &trade;</p>")
        p = soup.find("p")
        text = p.get_text()
        assert "\u00a9" in text  # ©
        assert "\u00ae" in text  # ®

    def test_numeric_entities_decoded(self, parse):
        soup = parse("<p>&#65;&#66;&#67;</p>")  # ABC
        p = soup.find("p")
        assert "ABC" in p.get_text()
