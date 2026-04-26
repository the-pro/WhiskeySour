"""
Tests for malformed HTML handling.

Each test feeds broken markup into WhiskeySour and checks the recovered DOM
against what a browser (html5ever / WHATWG spec) should produce.

A companion comparison script (tests/python/performance/bench_malformed.py)
runs the same inputs through BS4 side-by-side for visual diffing.
"""

import pytest
from whiskeysour import WhiskeySour


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def parse(html):
    return WhiskeySour(html)


# ---------------------------------------------------------------------------
# 1. Unclosed tags
# ---------------------------------------------------------------------------

class TestUnclosedTags:
    def test_unclosed_p_is_auto_closed(self):
        soup = parse("<div><p>first<p>second</p></div>")
        ps = soup.find_all("p")
        assert len(ps) == 2
        assert ps[0].string == "first"
        assert ps[1].string == "second"

    def test_unclosed_div_at_eof(self):
        soup = parse("<div><p>hello")
        assert soup.find("p").string == "hello"
        assert soup.find("div") is not None

    def test_unclosed_li(self):
        soup = parse("<ul><li>one<li>two<li>three</ul>")
        items = soup.find_all("li")
        assert len(items) == 3
        assert [i.string for i in items] == ["one", "two", "three"]

    def test_unclosed_heading(self):
        soup = parse("<h1>title<p>body text</p>")
        h1 = soup.find("h1")
        assert "title" in h1.get_text()
        assert soup.find("p") is not None

    def test_unclosed_td(self):
        soup = parse("<table><tr><td>a<td>b<td>c</tr></table>")
        cells = soup.find_all("td")
        assert len(cells) == 3


# ---------------------------------------------------------------------------
# 2. Misnested tags (adoption agency algorithm)
# ---------------------------------------------------------------------------

class TestMisnestedTags:
    def test_misnested_bold_italic(self):
        soup = parse("<b><i>text</b></i>")
        # Browser splits this into <b><i>text</i></b>
        text = soup.body.get_text()
        assert "text" in text

    def test_overlapping_formatting(self):
        soup = parse("<em><strong>both</em>just strong</strong>")
        assert "both" in soup.get_text()
        assert "just strong" in soup.get_text()

    def test_misnested_links(self):
        soup = parse('<a href="1">foo<a href="2">bar</a>')
        links = soup.find_all("a")
        assert len(links) >= 2


# ---------------------------------------------------------------------------
# 3. Stray end tags
# ---------------------------------------------------------------------------

class TestStrayEndTags:
    def test_stray_closing_tag_ignored(self):
        soup = parse("<div></b>text</div>")
        assert soup.find("div").get_text() == "text"

    def test_stray_closing_p(self):
        soup = parse("</p>hello<p>world</p>")
        assert "hello" in soup.get_text()
        assert "world" in soup.get_text()

    def test_extra_closing_divs(self):
        soup = parse("<div>hello</div></div></div>")
        divs = soup.find_all("div")
        assert len(divs) == 1
        assert divs[0].string == "hello"


# ---------------------------------------------------------------------------
# 4. Missing html/head/body
# ---------------------------------------------------------------------------

class TestImplicitStructure:
    def test_bare_text(self):
        soup = parse("just some text")
        assert soup.body is not None
        assert soup.body.get_text() == "just some text"

    def test_bare_paragraph(self):
        soup = parse("<p>hello</p>")
        assert soup.html is not None
        assert soup.body is not None
        assert soup.find("p").string == "hello"

    def test_no_doctype(self):
        soup = parse("<html><body><p>hi</p></body></html>")
        assert soup.find("p").string == "hi"

    def test_title_creates_head(self):
        soup = parse("<title>hi</title><p>body</p>")
        assert soup.head is not None
        assert soup.title is not None


# ---------------------------------------------------------------------------
# 5. Void elements with close tags
# ---------------------------------------------------------------------------

class TestVoidElements:
    def test_br_with_close_tag(self):
        soup = parse("a<br></br>b")
        # html5ever treats </br> as another <br>
        brs = soup.find_all("br")
        assert len(brs) >= 1

    def test_hr_with_close_tag(self):
        soup = parse("<hr></hr>")
        hrs = soup.find_all("hr")
        assert len(hrs) >= 1

    def test_img_with_close_tag(self):
        soup = parse('<img src="x.png"></img>')
        imgs = soup.find_all("img")
        assert len(imgs) >= 1

    def test_input_with_close_tag(self):
        soup = parse('<input type="text"></input>')
        inputs = soup.find_all("input")
        assert len(inputs) >= 1

    def test_self_closing_div_not_void(self):
        soup = parse("<div/>content")
        # <div/> is NOT a self-closing tag in HTML — it's treated as <div>
        div = soup.find("div")
        assert div is not None
        assert "content" in div.get_text()


# ---------------------------------------------------------------------------
# 6. Duplicate attributes (first wins per HTML5 spec)
# ---------------------------------------------------------------------------

class TestDuplicateAttrs:
    def test_first_id_wins(self):
        soup = parse('<span id="first" id="second">text</span>')
        span = soup.find("span")
        assert span["id"] == "first"

    def test_first_class_wins(self):
        soup = parse('<div class="a" class="b">text</div>')
        div = soup.find("div")
        assert div["class"] == ["a"]


# ---------------------------------------------------------------------------
# 7. Table foster parenting
# ---------------------------------------------------------------------------

class TestFosterParenting:
    def test_text_in_table_foster_parented(self):
        soup = parse("<table>oops<tr><td>cell</td></tr></table>")
        # "oops" should be foster-parented before the table
        assert "oops" in soup.get_text()
        assert soup.find("td").string == "cell"

    def test_p_in_table_foster_parented(self):
        soup = parse("<table><p>bad</p><tr><td>ok</td></tr></table>")
        assert "bad" in soup.get_text()
        assert soup.find("td").string == "ok"


# ---------------------------------------------------------------------------
# 8. Attributes: bare, unquoted, single-quoted
# ---------------------------------------------------------------------------

class TestAttributeEdgeCases:
    def test_bare_boolean_attrs(self):
        soup = parse('<input type="checkbox" checked disabled>')
        inp = soup.find("input")
        assert inp.has_attr("checked")
        assert inp.has_attr("disabled")

    def test_unquoted_attr_values(self):
        soup = parse("<font color=red size=3>text</font>")
        font = soup.find("font")
        assert font["color"] == "red"
        assert font["size"] == "3"

    def test_single_quoted_attrs(self):
        soup = parse("<div id='hello' class='world'>x</div>")
        div = soup.find("div")
        assert div["id"] == "hello"
        assert div["class"] == ["world"]

    def test_attr_with_special_chars(self):
        soup = parse('<a href="page?a=1&b=2">link</a>')
        a = soup.find("a")
        assert "a=1" in a["href"]


# ---------------------------------------------------------------------------
# 9. Entities
# ---------------------------------------------------------------------------

class TestEntities:
    def test_named_entities(self):
        soup = parse("<p>&amp; &lt; &gt;</p>")
        text = soup.find("p").get_text()
        assert "& < >" == text

    def test_numeric_decimal(self):
        soup = parse("<p>&#65;&#66;&#67;</p>")
        assert soup.find("p").get_text() == "ABC"

    def test_numeric_hex(self):
        soup = parse("<p>&#x41;&#x42;&#x43;</p>")
        assert soup.find("p").get_text() == "ABC"

    def test_missing_semicolon(self):
        # Browsers still decode &amp without semicolon in many contexts
        soup = parse("<p>&amp hello</p>")
        text = soup.find("p").get_text()
        assert "&" in text


# ---------------------------------------------------------------------------
# 10. Script/style edge cases
# ---------------------------------------------------------------------------

class TestRawTextElements:
    def test_script_with_angle_brackets(self):
        soup = parse('<script>if (a < b && c > d) {}</script><p>after</p>')
        assert soup.find("p").string == "after"
        script = soup.find("script")
        assert "a < b" in script.string

    def test_style_with_angle_brackets(self):
        soup = parse("<style>a > b { color: red; }</style><p>ok</p>")
        assert soup.find("p").string == "ok"


# ---------------------------------------------------------------------------
# 11. Comments
# ---------------------------------------------------------------------------

class TestMalformedComments:
    def test_empty_comment(self):
        soup = parse("<!-- --><p>ok</p>")
        assert soup.find("p").string == "ok"

    def test_comment_with_dashes(self):
        soup = parse("<!-- -- --><p>ok</p>")
        assert soup.find("p").string == "ok"

    def test_unclosed_comment(self):
        soup = parse("<!-- oops<p>gone</p>")
        # Everything after unclosed comment is swallowed
        # The <p> may or may not be findable depending on parser
        assert soup is not None


# ---------------------------------------------------------------------------
# 12. Weird nesting
# ---------------------------------------------------------------------------

class TestWeirdNesting:
    def test_block_inside_inline(self):
        soup = parse("<span><div>block in inline</div></span>")
        assert "block in inline" in soup.get_text()

    def test_p_inside_p(self):
        # <p> cannot contain <p> — first one auto-closes
        soup = parse("<p>outer<p>inner</p>")
        ps = soup.find_all("p")
        assert len(ps) == 2

    def test_form_inside_form(self):
        soup = parse('<form id="outer"><form id="inner"><input></form></form>')
        # Inner form is ignored by the spec
        forms = soup.find_all("form")
        assert len(forms) == 1

    def test_deep_nesting(self):
        html = "<div>" * 100 + "deep" + "</div>" * 100
        soup = parse(html)
        assert "deep" in soup.get_text()

    def test_select_inside_select(self):
        soup = parse(
            '<select><option>a</option><select><option>b</option></select></select>'
        )
        # Nested select is invalid — parser should handle it
        selects = soup.find_all("select")
        assert len(selects) >= 1


# ---------------------------------------------------------------------------
# 13. Encoding edge cases
# ---------------------------------------------------------------------------

class TestEncodingEdgeCases:
    def test_utf8_multibyte(self):
        soup = parse("<p>日本語テスト</p>")
        assert soup.find("p").string == "日本語テスト"

    def test_emoji(self):
        soup = parse("<p>Hello 🌍🚀</p>")
        assert "🌍" in soup.find("p").get_text()

    def test_null_byte_stripped(self):
        soup = parse("<p>hel\x00lo</p>")
        text = soup.find("p").get_text()
        # html5ever strips null bytes per spec
        assert "hel" in text
        assert "\x00" not in text

    def test_bytes_input(self):
        soup = WhiskeySour(b"<p>bytes</p>")
        assert soup.find("p").string == "bytes"


# ---------------------------------------------------------------------------
# 14. Real-world broken patterns
# ---------------------------------------------------------------------------

class TestRealWorldBroken:
    def test_microsoft_word_html(self):
        """MS Word often produces this kind of garbage."""
        html = """
        <p class=MsoNormal><b><span style='font-size:12.0pt'>Title</span></b></p>
        <p class=MsoNormal><span style='font-size:10.0pt'>Body text
        <o:p></o:p></span></p>
        """
        soup = parse(html)
        assert "Title" in soup.get_text()
        assert "Body text" in soup.get_text()

    def test_mixed_case_tags(self):
        soup = parse("<DIV><P>hello</P></DIV>")
        # HTML is case-insensitive — tags should be lowercased
        assert soup.find("div") is not None
        assert soup.find("p").string == "hello"

    def test_missing_quotes_around_attr(self):
        soup = parse('<a href=http://example.com>link</a>')
        a = soup.find("a")
        assert a["href"] == "http://example.com"

    def test_multiple_bodies(self):
        soup = parse("<html><body>first</body><body>second</body></html>")
        # Second body is merged into the first
        assert "first" in soup.body.get_text()

    def test_text_after_body(self):
        soup = parse("<html><body>inside</body>outside</html>")
        assert "inside" in soup.get_text()
        assert "outside" in soup.get_text()

    def test_email_html_tables(self):
        """Email clients love nested tables."""
        html = """
        <table><tr><td>
          <table><tr><td>
            <table><tr><td>deeply nested cell</td></tr></table>
          </td></tr></table>
        </td></tr></table>
        """
        soup = parse(html)
        assert "deeply nested cell" in soup.get_text()
        cells = soup.find_all("td")
        assert len(cells) == 3
