"""
bench_find.py — find() / find_all() latency benchmarks.

Targets (from project_plan.md Phase 4):
  find() simple    → WhiskeySour < 0.005ms
  find_all() 1000n → WhiskeySour < 0.5ms
"""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.perf

BENCH_HTML = ("<!DOCTYPE html><html><body>"
              + "".join(
                  f'<div id="d{i}" class="item" data-group="{i%10}">'
                  f'<p class="text">Para {i}</p>'
                  f'<a href="/link/{i}" class="link" data-i="{i}">Link {i}</a>'
                  f'</div>'
                  for i in range(1000)
              )
              + "</body></html>")


@pytest.fixture
def bench_soup(parse):
    return parse(BENCH_HTML)


@pytest.fixture(scope="session")
def bs4_bench_soup():
    bs4 = pytest.importorskip("bs4", reason="bs4 not installed")
    return bs4.BeautifulSoup(BENCH_HTML, "html.parser")


class TestFindBenchmark:
    @pytest.mark.benchmark(group="find-simple")
    def test_ws_find_by_tag(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.find("div"), rounds=1000, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.000025  # 25μs

    @pytest.mark.benchmark(group="find-simple")
    def test_ws_find_by_id(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.find(id="d500"), rounds=1000, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.0005  # 500μs

    @pytest.mark.benchmark(group="find-all")
    def test_ws_find_all_by_tag(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.find_all("div"), rounds=200, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.0005  # 0.5ms

    @pytest.mark.benchmark(group="find-all")
    def test_ws_find_all_by_class(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.find_all(class_="link"), rounds=200, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.0008

    @pytest.mark.benchmark(group="find-all")
    def test_ws_find_all_with_limit(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.find_all("div", limit=10), rounds=500, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.0001

    @pytest.mark.benchmark(group="find-simple")
    def test_bs4_find_by_tag(self, bs4_bench_soup, benchmark):
        benchmark.pedantic(lambda: bs4_bench_soup.find("div"), rounds=500, warmup_rounds=5)

    @pytest.mark.benchmark(group="find-all")
    def test_bs4_find_all_by_tag(self, bs4_bench_soup, benchmark):
        benchmark.pedantic(lambda: bs4_bench_soup.find_all("div"), rounds=50, warmup_rounds=3)


class TestSelectorBenchmark:
    @pytest.mark.benchmark(group="select")
    def test_ws_select_simple(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.select("div"), rounds=200, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.001

    @pytest.mark.benchmark(group="select")
    def test_ws_select_class(self, bench_soup, benchmark):
        benchmark.pedantic(lambda: bench_soup.select(".link"), rounds=200, warmup_rounds=10)
        assert benchmark.stats["mean"] < 0.001

    @pytest.mark.benchmark(group="select")
    def test_ws_select_complex(self, bench_soup, benchmark):
        benchmark.pedantic(
            lambda: bench_soup.select("div.item > p.text + a.link[data-i]"),
            rounds=100, warmup_rounds=5
        )
        assert benchmark.stats["mean"] < 0.005

    @pytest.mark.benchmark(group="select-cached")
    def test_ws_select_cached_second_call(self, bench_soup, benchmark):
        """Second call with the same selector should hit the LRU cache."""
        selector = ".link[data-i]"
        bench_soup.select(selector)  # warm up cache
        benchmark.pedantic(lambda: bench_soup.select(selector), rounds=500, warmup_rounds=20)
        assert benchmark.stats["mean"] < 0.001  # <1ms (dev build; cached avoids re-parse)
