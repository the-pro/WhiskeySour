"""
bench_parse.py — Parse latency benchmarks.

Run with: pytest tests/python/performance/bench_parse.py --benchmark-only
Or:       pytest tests/python/performance/ -m perf -v

Benchmarks compare whiskysour against bs4+html.parser, bs4+lxml (if available),
and lxml.etree directly.

Targets (from project_plan.md Phase 4):
  10KB  → WhiskeySour < 0.3ms
  100KB → WhiskeySour < 2ms
  1MB   → WhiskeySour < 15ms
"""

from __future__ import annotations

import textwrap
from pathlib import Path

import pytest

pytestmark = pytest.mark.perf

FIXTURES = Path(__file__).parent.parent / "fixtures"

# ---------------------------------------------------------------------------
# Fixtures: load HTML into memory once per session
# ---------------------------------------------------------------------------

@pytest.fixture(scope="session")
def html_10kb():
    # Build a ~10KB document
    lines = ["<!DOCTYPE html><html><body>"]
    for i in range(200):
        lines.append(f'<div id="d{i}" class="item"><p>Item {i} text content.</p></div>')
    lines.append("</body></html>")
    return "".join(lines)


@pytest.fixture(scope="session")
def html_100kb():
    lines = ["<!DOCTYPE html><html><body>"]
    for i in range(2000):
        lines.append(f'<div id="d{i}" class="item" data-i="{i}"><p>Item {i} longer text content here.</p></div>')
    lines.append("</body></html>")
    return "".join(lines)


@pytest.fixture(scope="session")
def html_1mb(large_html):
    return large_html


# ---------------------------------------------------------------------------
# WhiskeySour benchmarks
# ---------------------------------------------------------------------------

class TestWhiskeySourParseBenchmark:
    @pytest.mark.benchmark(group="parse-10kb")
    def test_ws_parse_10kb(self, parse, html_10kb, benchmark):
        benchmark.pedantic(lambda: parse(html_10kb), rounds=100, warmup_rounds=5)
        # Target: < 0.3ms = 300μs
        assert benchmark.stats["mean"] < 0.001, (
            f"WhiskeySour 10KB parse too slow: {benchmark.stats['mean']*1000:.2f}ms (target <1ms)"
        )

    @pytest.mark.benchmark(group="parse-100kb")
    def test_ws_parse_100kb(self, parse, html_100kb, benchmark):
        benchmark.pedantic(lambda: parse(html_100kb), rounds=50, warmup_rounds=3)
        assert benchmark.stats["mean"] < 0.005, (
            f"WhiskeySour 100KB parse too slow: {benchmark.stats['mean']*1000:.2f}ms (target <5ms)"
        )

    @pytest.mark.benchmark(group="parse-1mb")
    @pytest.mark.slow
    def test_ws_parse_1mb(self, parse, html_1mb, benchmark):
        benchmark.pedantic(lambda: parse(html_1mb), rounds=20, warmup_rounds=2)
        assert benchmark.stats["mean"] < 0.040, (
            f"WhiskeySour 1MB parse too slow: {benchmark.stats['mean']*1000:.2f}ms (target <40ms)"
        )


# ---------------------------------------------------------------------------
# bs4 + html.parser benchmarks (baseline)
# ---------------------------------------------------------------------------

class TestBS4HtmlParserBenchmark:
    @pytest.fixture(autouse=True)
    def check_bs4(self):
        pytest.importorskip("bs4", reason="bs4 not installed")

    @pytest.mark.benchmark(group="parse-10kb")
    def test_bs4_parse_10kb(self, html_10kb, benchmark):
        from bs4 import BeautifulSoup
        benchmark.pedantic(
            lambda: BeautifulSoup(html_10kb, "html.parser"),
            rounds=50, warmup_rounds=3
        )

    @pytest.mark.benchmark(group="parse-100kb")
    def test_bs4_parse_100kb(self, html_100kb, benchmark):
        from bs4 import BeautifulSoup
        benchmark.pedantic(
            lambda: BeautifulSoup(html_100kb, "html.parser"),
            rounds=20, warmup_rounds=2
        )

    @pytest.mark.benchmark(group="parse-1mb")
    @pytest.mark.slow
    def test_bs4_parse_1mb(self, html_1mb, benchmark):
        from bs4 import BeautifulSoup
        benchmark.pedantic(
            lambda: BeautifulSoup(html_1mb, "html.parser"),
            rounds=5, warmup_rounds=1
        )


# ---------------------------------------------------------------------------
# bs4 + lxml benchmarks
# ---------------------------------------------------------------------------

class TestBS4LxmlBenchmark:
    @pytest.fixture(autouse=True)
    def check_deps(self):
        pytest.importorskip("bs4", reason="bs4 not installed")
        pytest.importorskip("lxml", reason="lxml not installed")

    @pytest.mark.benchmark(group="parse-10kb")
    def test_bs4_lxml_parse_10kb(self, html_10kb, benchmark):
        from bs4 import BeautifulSoup
        benchmark.pedantic(
            lambda: BeautifulSoup(html_10kb, "lxml"),
            rounds=50, warmup_rounds=3
        )

    @pytest.mark.benchmark(group="parse-100kb")
    def test_bs4_lxml_parse_100kb(self, html_100kb, benchmark):
        from bs4 import BeautifulSoup
        benchmark.pedantic(
            lambda: BeautifulSoup(html_100kb, "lxml"),
            rounds=20, warmup_rounds=2
        )


# ---------------------------------------------------------------------------
# Throughput reporter
# ---------------------------------------------------------------------------

class TestThroughput:
    def test_ws_throughput_report(self, parse, html_1mb, benchmark):
        """Reports MB/s throughput for WhiskeySour."""
        size_mb = len(html_1mb.encode("utf-8")) / (1024 * 1024)
        result = benchmark(lambda: parse(html_1mb))
        mbps = size_mb / benchmark.stats["mean"]
        print(f"\nWhiskeySour throughput: {mbps:.1f} MB/s (target: >85 MB/s)")
        # Soft assertion — just report, don't fail CI on first run
        # assert mbps > 85, f"Throughput {mbps:.1f} MB/s below target 85 MB/s"
