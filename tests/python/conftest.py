"""
Shared fixtures and helpers for WhiskeySour test suite.

All tests import from this module via pytest's conftest.py auto-discovery.
The `parse` fixture is the primary entry point — it constructs a WhiskeySour
document and must be swapped to the real implementation once the Rust core ships.
"""

from __future__ import annotations

import io
import os
import re
import textwrap
from pathlib import Path
from typing import Any

import pytest

# ---------------------------------------------------------------------------
# Stub shim — replaced by the real extension module once built.
# Tests written against this interface will pass through to the Rust core.
# ---------------------------------------------------------------------------

FIXTURES_DIR = Path(__file__).parent / "fixtures"


def _get_whiskeysour():
    """Import whiskeysour, falling back to bs4 stub for early TDD runs."""
    try:
        import whiskeysour as ws  # noqa: F401 – real package once built
        return ws
    except ImportError:
        # Stub: tests are written against this API; implementation TBD.
        raise ImportError(
            "whiskeysour is not yet installed. "
            "Run `maturin develop` inside the project root to build the Rust extension. "
            "All tests will FAIL until the extension is built — this is expected during TDD phase."
        )


# ---------------------------------------------------------------------------
# Core parse fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def ws():
    """The whiskeysour module itself."""
    return _get_whiskeysour()


@pytest.fixture
def parse(ws):
    """
    Factory fixture: parse(html) → Document.
    Mirrors the BeautifulSoup(html) constructor signature.
    """
    def _parse(markup: str | bytes, features: str = None, **kwargs):
        if features is not None:
            return ws.WhiskeySour(markup, features, **kwargs)
        return ws.WhiskeySour(markup, **kwargs)
    return _parse


@pytest.fixture
def parse_fragment(parse):
    """Parse an HTML fragment (no <html>/<body> wrapper)."""
    def _parse_fragment(markup: str | bytes):
        return parse(markup)
    return _parse_fragment


# ---------------------------------------------------------------------------
# HTML fixture helpers
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def fixture_path():
    return FIXTURES_DIR


@pytest.fixture(scope="session")
def simple_html():
    return (FIXTURES_DIR / "simple.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def malformed_html():
    return (FIXTURES_DIR / "malformed.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def large_html():
    return (FIXTURES_DIR / "large_100k_nodes.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def deeply_nested_html():
    return (FIXTURES_DIR / "deeply_nested.html").read_text(encoding="utf-8")


@pytest.fixture(scope="session")
def unicode_html():
    return (FIXTURES_DIR / "unicode_heavy.html").read_text(encoding="utf-8")


# ---------------------------------------------------------------------------
# Convenience: pre-parsed documents
# ---------------------------------------------------------------------------

@pytest.fixture
def simple_soup(parse, simple_html):
    return parse(simple_html)


@pytest.fixture
def malformed_soup(parse, malformed_html):
    return parse(malformed_html)


# ---------------------------------------------------------------------------
# Helpers exposed to tests
# ---------------------------------------------------------------------------

def make_html(body: str, head: str = "", lang: str = "en") -> str:
    """Wrap a body snippet in a minimal valid HTML5 document."""
    return textwrap.dedent(f"""\
        <!DOCTYPE html>
        <html lang="{lang}">
        <head><meta charset="UTF-8">{head}</head>
        <body>{body}</body>
        </html>
    """)


@pytest.fixture
def html_doc():
    """Factory: html_doc(body, head) → full HTML5 string."""
    return make_html


# ---------------------------------------------------------------------------
# Pytest configuration
# ---------------------------------------------------------------------------

def pytest_configure(config):
    config.addinivalue_line("markers", "slow: mark test as slow (large documents)")
    config.addinivalue_line("markers", "fuzz: mark test as fuzz/property-based")
    config.addinivalue_line("markers", "compat: mark test as bs4 API compatibility check")
    config.addinivalue_line("markers", "perf: mark test as performance/benchmark")
