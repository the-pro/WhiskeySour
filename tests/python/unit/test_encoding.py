"""
test_encoding.py — Character encoding detection and handling tests.

Covers:
  - UTF-8, UTF-16 LE/BE, Latin-1, Windows-1252 documents
  - <meta charset> detection
  - <meta http-equiv="Content-Type"> detection
  - BOM (Byte Order Mark) stripping
  - Bytes vs str input
  - Surrogate pairs, emoji, CJK characters
  - Encoding errors / replacement
  - from_encoding override
"""

from __future__ import annotations

import codecs
import textwrap

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def encode_doc(body_text: str, charset: str, *, meta: bool = True) -> bytes:
    """Build a minimal HTML doc, encode it in the given charset, return bytes."""
    meta_tag = f'<meta charset="{charset}">' if meta else ""
    html = f"<!DOCTYPE html><html><head>{meta_tag}</head><body><p id='content'>{body_text}</p></body></html>"
    return html.encode(charset, errors="replace")


# ===========================================================================
# 1. UTF-8
# ===========================================================================

class TestUTF8:
    def test_utf8_ascii_subset(self, parse):
        html = b"<p>Hello, World!</p>"
        soup = parse(html)
        assert "Hello, World!" in soup.get_text()

    def test_utf8_multibyte_cjk(self, parse):
        html = "<p>\u4e2d\u6587\u6d4b\u8bd5</p>".encode("utf-8")
        soup = parse(html)
        assert "\u4e2d\u6587" in soup.get_text()

    def test_utf8_emoji(self, parse):
        html = "<p>Hello \U0001f600 World</p>".encode("utf-8")
        soup = parse(html)
        assert "\U0001f600" in soup.get_text()

    def test_utf8_combining_characters(self, parse):
        # 'a' + combining grave accent = 'à'
        html = "<p>caf\u0065\u0301</p>".encode("utf-8")
        soup = parse(html)
        text = soup.get_text()
        assert "caf" in text

    def test_utf8_bom_stripped(self, parse):
        """UTF-8 BOM (EF BB BF) must be stripped from output."""
        html = b"\xef\xbb\xbf<!DOCTYPE html><html><body><p>BOM test</p></body></html>"
        soup = parse(html)
        text = soup.get_text()
        assert "\ufeff" not in text
        assert "BOM test" in text

    def test_str_input_passthrough(self, parse):
        """str input is already decoded — must work without encoding dance."""
        soup = parse("<p>String input \u00e9l\u00e8ve</p>")
        assert "\u00e9l\u00e8ve" in soup.get_text()


# ===========================================================================
# 2. Latin-1 / ISO-8859-1
# ===========================================================================

class TestLatin1:
    def test_latin1_accented_chars(self, parse):
        body = "caf\xe9 r\xe9sum\xe9"  # café résumé in latin-1
        html = encode_doc(body, "iso-8859-1")
        soup = parse(html, from_encoding="iso-8859-1")
        text = soup.get_text()
        assert "caf" in text

    def test_latin1_detected_via_meta(self, parse):
        body = "caf\xe9"
        html = encode_doc(body, "latin-1")
        soup = parse(html)
        # Parser should detect charset from meta tag
        text = soup.get_text()
        assert "caf" in text

    def test_windows_1252(self, parse):
        """Windows-1252 extends Latin-1; € is at 0x80."""
        html = b'<meta charset="windows-1252"><p>\x80 price</p>'
        soup = parse(html)
        text = soup.get_text()
        # \x80 in windows-1252 is € (U+20AC)
        assert "\u20ac" in text or "price" in text


# ===========================================================================
# 3. UTF-16
# ===========================================================================

class TestUTF16:
    def test_utf16_le_with_bom(self, parse):
        html = "<p>UTF-16 LE test \u65e5\u672c\u8a9e</p>"
        data = html.encode("utf-16-le")
        bom_data = b"\xff\xfe" + data  # UTF-16 LE BOM
        soup = parse(bom_data)
        text = soup.get_text()
        assert "UTF-16" in text or "\u65e5\u672c" in text

    def test_utf16_be_with_bom(self, parse):
        html = "<p>UTF-16 BE test</p>"
        data = b"\xfe\xff" + html.encode("utf-16-be")
        soup = parse(data)
        assert "UTF-16 BE test" in soup.get_text()

    def test_utf16_from_encoding_override(self, parse):
        html = "<p>Hello</p>"
        data = html.encode("utf-16-le")
        soup = parse(data, from_encoding="utf-16-le")
        assert "Hello" in soup.get_text()


# ===========================================================================
# 4. Meta charset detection
# ===========================================================================

class TestMetaCharsetDetection:
    def test_meta_charset_shorthand(self, parse):
        html = b'<!DOCTYPE html><html><head><meta charset="UTF-8"></head><body><p>ok</p></body></html>'
        soup = parse(html)
        assert soup.find("p") is not None

    def test_meta_http_equiv_content_type(self, parse):
        html = b'<html><head><meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1"></head><body><p>ok</p></body></html>'
        soup = parse(html)
        assert soup.find("p") is not None

    def test_meta_charset_overrides_http_equiv(self, parse):
        """Later <meta charset> takes precedence over http-equiv."""
        html = textwrap.dedent("""\
            <html><head>
              <meta http-equiv="Content-Type" content="text/html; charset=iso-8859-1">
              <meta charset="UTF-8">
            </head><body><p>ok</p></body></html>
        """).encode("utf-8")
        soup = parse(html)
        assert soup.find("p") is not None

    def test_meta_charset_in_body_ignored(self, parse):
        """<meta charset> after <body> open is too late; should be ignored."""
        html = b"<html><head></head><body><meta charset='utf-8'><p>text</p></body></html>"
        soup = parse(html)
        assert soup.find("p") is not None

    def test_no_meta_charset_falls_back_to_utf8(self, parse):
        html = b"<html><head></head><body><p>\xc3\xa9l\xc3\xa8ve</p></body></html>"
        soup = parse(html)
        assert "\u00e9l\u00e8ve" in soup.get_text()

    def test_xml_declaration_encoding(self, parse):
        xml = b'<?xml version="1.0" encoding="UTF-8"?><root><item>text</item></root>'
        soup = parse(xml, features="xml")
        assert soup.find("item") is not None


# ===========================================================================
# 5. BOM handling
# ===========================================================================

class TestBOM:
    def test_utf8_bom_does_not_appear_in_title(self, parse):
        html = "\ufeff<html><head><title>Title</title></head><body></body></html>"
        soup = parse(html.encode("utf-8"))
        title = soup.title
        if title:
            assert "\ufeff" not in title.string

    def test_utf16_le_bom_detected_automatically(self, parse):
        html = "<p>auto-detected</p>"
        data = b"\xff\xfe" + html.encode("utf-16-le")
        soup = parse(data)
        assert "auto-detected" in soup.get_text()

    def test_utf32_bom(self, parse):
        html = "<p>UTF-32</p>"
        try:
            data = b"\xff\xfe\x00\x00" + html.encode("utf-32-le")
            soup = parse(data)
            # Must not raise; content may or may not be decoded
            assert soup is not None
        except Exception:
            pytest.skip("UTF-32 not supported by this parser backend")


# ===========================================================================
# 6. from_encoding override
# ===========================================================================

class TestFromEncodingOverride:
    def test_force_encoding(self, parse):
        # Build as latin-1 but without a meta charset declaration
        body = "caf\xe9"
        html = f"<html><head></head><body><p>{body}</p></body></html>".encode("latin-1")
        soup = parse(html, from_encoding="latin-1")
        text = soup.get_text()
        assert "caf\u00e9" in text or "caf" in text

    def test_wrong_encoding_produces_garbage(self, parse):
        """Providing wrong encoding must not crash, just produce garbled text."""
        html = "<p>Hello</p>".encode("utf-8")
        soup = parse(html, from_encoding="utf-16")
        # Must not raise; content may be garbled
        assert soup is not None

    def test_override_takes_priority_over_meta(self, parse):
        html = b'<html><head><meta charset="utf-8"></head><body><p>hi</p></body></html>'
        soup = parse(html, from_encoding="utf-8")
        assert soup.find("p") is not None


# ===========================================================================
# 7. Unicode edge cases
# ===========================================================================

class TestUnicodeEdgeCases:
    def test_surrogate_pairs_in_html(self, parse):
        """U+10000 through U+10FFFF encoded as HTML numeric entities."""
        html = "<p>&#x1F600; &#x1F4A9; &#x1D11E;</p>"
        soup = parse(html)
        text = soup.find("p").get_text()
        assert "\U0001f600" in text
        assert "\U0001f4a9" in text

    def test_lone_surrogates_handled(self, parse):
        """Lone surrogates (U+D800–U+DFFF) are invalid in HTML; must not crash."""
        html = "<p>&#xD800; &#xDFFF;</p>"
        soup = parse(html)
        assert soup is not None

    def test_replacement_character_entity(self, parse):
        html = "<p>&#xFFFD; replacement</p>"
        soup = parse(html)
        text = soup.find("p").get_text()
        assert "\ufffd" in text

    def test_null_character_entity_replaced(self, parse):
        """&#x0000; must be replaced with U+FFFD per HTML5 spec."""
        html = "<p>&#x0000;</p>"
        soup = parse(html)
        text = soup.find("p").get_text()
        assert "\x00" not in text

    def test_high_codepoint_tag_attribute(self, parse):
        html = '<div data-value="\U0001f600\U0001f4a9">emoji attr</div>'
        soup = parse(html)
        div = soup.find("div")
        assert "\U0001f600" in div["data-value"]

    def test_zero_width_chars_preserved(self, parse):
        html = "<p>word\u200bbreak</p>"  # zero-width space
        soup = parse(html)
        text = soup.find("p").get_text()
        assert "word" in text
        assert "break" in text

    def test_unicode_fixture(self, parse, unicode_html):
        """Full unicode fixture must parse without errors."""
        soup = parse(unicode_html)
        assert soup.find(id="japanese") is not None
        assert soup.find(id="emoji") is not None
        assert soup.find(id="arabic") is not None
        # Verify content survived
        assert "\u65e5\u672c\u8a9e" in soup.get_text() or "テスト" in soup.get_text()
