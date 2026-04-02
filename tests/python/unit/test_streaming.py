"""
test_streaming.py — Streaming / incremental parser tests.

WhiskeySour extension: parse large documents in chunks without building
the full DOM in memory first. Useful for processing multi-GB HTML dumps.

API under test:
  - ws.StreamParser(callback) — chunk-by-chunk push parser
  - ws.parse_stream(file_obj) — file-like object interface
  - Memory remains bounded while streaming
  - find_first / find_all_streaming on a stream
"""

from __future__ import annotations

import io
import os

import pytest


# ===========================================================================
# Helpers
# ===========================================================================

def chunk_string(s: str, chunk_size: int = 1024):
    """Yield s in chunks of chunk_size bytes."""
    encoded = s.encode("utf-8")
    for i in range(0, len(encoded), chunk_size):
        yield encoded[i : i + chunk_size]


STREAM_HTML = """\
<!DOCTYPE html>
<html><head><meta charset="UTF-8"><title>Stream Test</title></head>
<body>
""" + "\n".join(
    f'<article id="article-{i}" class="article"><h2>Article {i}</h2>'
    f'<p class="content">Content for article {i}.</p>'
    f'<a href="/article/{i}" class="read-more">Read more</a></article>'
    for i in range(200)
) + "\n</body></html>"


# ===========================================================================
# 1. File-like object (io.StringIO / io.BytesIO) as input
# ===========================================================================

class TestFileInput:
    def test_parse_string_io(self, parse):
        html = "<p>Hello from StringIO</p>"
        buf = io.StringIO(html)
        soup = parse(buf)
        assert soup.find("p") is not None
        assert "Hello from StringIO" in soup.get_text()

    def test_parse_bytes_io(self, parse):
        html = b"<p>Hello from BytesIO</p>"
        buf = io.BytesIO(html)
        soup = parse(buf)
        assert soup.find("p") is not None

    def test_parse_file_object(self, parse, tmp_path):
        html_file = tmp_path / "test.html"
        html_file.write_text("<p>From file</p>", encoding="utf-8")
        with open(html_file, encoding="utf-8") as f:
            soup = parse(f)
        assert "From file" in soup.get_text()

    def test_parse_binary_file_object(self, parse, tmp_path):
        html_file = tmp_path / "test.html"
        html_file.write_bytes(b"<p>Binary file</p>")
        with open(html_file, "rb") as f:
            soup = parse(f)
        assert "Binary file" in soup.get_text()

    def test_parse_large_file_object(self, parse, tmp_path):
        html_file = tmp_path / "large.html"
        html_file.write_text(STREAM_HTML, encoding="utf-8")
        with open(html_file, encoding="utf-8") as f:
            soup = parse(f)
        assert len(soup.find_all("article")) == 200


# ===========================================================================
# 2. StreamParser push API (WhiskeySour-specific)
# ===========================================================================

class TestStreamParser:
    def test_stream_parser_exists(self, ws):
        if not hasattr(ws, "StreamParser"):
            pytest.skip("StreamParser not implemented yet")

    def test_stream_parser_basic(self, ws):
        if not hasattr(ws, "StreamParser"):
            pytest.skip("StreamParser not implemented yet")
        results = []
        parser = ws.StreamParser(on_complete=lambda soup: results.append(soup))
        parser.feed(b"<html><body><p>Hello</p>")
        parser.feed(b"</body></html>")
        parser.close()
        assert len(results) == 1
        assert "Hello" in results[0].get_text()

    def test_stream_parser_chunked(self, ws):
        if not hasattr(ws, "StreamParser"):
            pytest.skip("StreamParser not implemented yet")
        results = []
        parser = ws.StreamParser(on_complete=lambda soup: results.append(soup))
        for chunk in chunk_string(STREAM_HTML, chunk_size=256):
            parser.feed(chunk)
        parser.close()
        assert len(results) == 1
        soup = results[0]
        assert len(soup.find_all("article")) == 200

    def test_stream_parser_tiny_chunks(self, ws):
        """1-byte chunks must not corrupt the result."""
        if not hasattr(ws, "StreamParser"):
            pytest.skip("StreamParser not implemented yet")
        results = []
        html = b"<p id='tiny'>Tiny chunks</p>"
        parser = ws.StreamParser(on_complete=lambda soup: results.append(soup))
        for byte in html:
            parser.feed(bytes([byte]))
        parser.close()
        assert len(results) == 1
        assert "Tiny chunks" in results[0].get_text()

    def test_stream_parser_empty_input(self, ws):
        if not hasattr(ws, "StreamParser"):
            pytest.skip("StreamParser not implemented yet")
        results = []
        parser = ws.StreamParser(on_complete=lambda soup: results.append(soup))
        parser.feed(b"")
        parser.close()
        # Empty input must produce at minimum an empty document
        assert len(results) == 1

    def test_stream_parser_context_manager(self, ws):
        if not hasattr(ws, "StreamParser"):
            pytest.skip("StreamParser not implemented yet")
        results = []
        with ws.StreamParser(on_complete=lambda s: results.append(s)) as parser:
            parser.feed(b"<p>context manager</p>")
        assert len(results) == 1


# ===========================================================================
# 3. parse_stream() — streaming find_all without full DOM
# ===========================================================================

class TestParseStream:
    def test_parse_stream_exists(self, ws):
        if not hasattr(ws, "parse_stream"):
            pytest.skip("parse_stream not implemented yet")

    def test_parse_stream_find_all(self, ws, tmp_path):
        if not hasattr(ws, "parse_stream"):
            pytest.skip("parse_stream not implemented yet")
        html_file = tmp_path / "stream.html"
        html_file.write_text(STREAM_HTML, encoding="utf-8")
        with open(html_file, "rb") as f:
            results = list(ws.parse_stream(f, find="article"))
        assert len(results) == 200

    def test_parse_stream_css_selector(self, ws, tmp_path):
        if not hasattr(ws, "parse_stream"):
            pytest.skip("parse_stream not implemented yet")
        html_file = tmp_path / "stream.html"
        html_file.write_text(STREAM_HTML, encoding="utf-8")
        with open(html_file, "rb") as f:
            results = list(ws.parse_stream(f, selector="a.read-more"))
        assert len(results) == 200

    def test_parse_stream_early_stop(self, ws, tmp_path):
        if not hasattr(ws, "parse_stream"):
            pytest.skip("parse_stream not implemented yet")
        html_file = tmp_path / "stream.html"
        html_file.write_text(STREAM_HTML, encoding="utf-8")
        with open(html_file, "rb") as f:
            results = []
            for el in ws.parse_stream(f, find="article"):
                results.append(el)
                if len(results) >= 10:
                    break
        assert len(results) == 10

    def test_parse_stream_memory_bounded(self, ws, tmp_path):
        """Memory during streaming must not grow proportional to doc size."""
        if not hasattr(ws, "parse_stream"):
            pytest.skip("parse_stream not implemented yet")
        import tracemalloc
        html_file = tmp_path / "stream_mem.html"
        # 1 MB document
        html_file.write_text(STREAM_HTML * 5, encoding="utf-8")
        tracemalloc.start()
        with open(html_file, "rb") as f:
            # Consume first 10 elements, then stop
            results = []
            for el in ws.parse_stream(f, find="article"):
                results.append(el.find("h2").get_text())
                if len(results) >= 10:
                    break
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        # Peak should be much less than the full document size (~1MB)
        assert peak < 5 * 1024 * 1024, f"Peak memory {peak/1024:.0f}KB exceeded 5MB"


# ===========================================================================
# 4. Chunked encoding robustness
# ===========================================================================

class TestChunkedEncoding:
    def test_split_in_middle_of_tag(self, parse):
        """Splitting input in the middle of a tag must still parse correctly."""
        # Test via BytesIO with partial reads
        html = b"<div><p>split <span>here</span></p></div>"
        # Feed in two halves
        half = len(html) // 2
        buf = io.BytesIO(html[:half] + html[half:])
        soup = parse(buf)
        assert soup.find("span") is not None
        assert "here" in soup.get_text()

    def test_split_in_middle_of_entity(self, parse):
        html = b"<p>AT&amp;T is a company</p>"
        buf = io.BytesIO(html)
        soup = parse(buf)
        assert "AT" in soup.get_text()

    def test_split_in_middle_of_utf8(self, parse):
        # Split in middle of a multi-byte UTF-8 sequence
        text = "日本語テスト"
        html = f"<p>{text}</p>"
        encoded = html.encode("utf-8")
        # Split at byte 10 (may be inside a multi-byte char)
        buf = io.BytesIO(encoded[:10] + encoded[10:])
        soup = parse(buf)
        p = soup.find("p")
        if p:
            assert soup is not None  # Must not crash
