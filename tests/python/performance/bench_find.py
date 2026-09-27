"""
bench_find.py — find() / find_all() latency benchmarks.

Targets (from project_plan.md Phase 4):
  find() simple    → WhiskeySour < 0.005ms
  find_all() 1000n → WhiskeySour < 0.5ms

Thresholds are loose enough for a dev build; quote numbers only from a
release build (`maturin develop --release`).
"""

from __future__ import annotations

import os
import threading
import time

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
        """Second call with the same selector reuses the cached parsed selector."""
        selector = ".link[data-i]"
        bench_soup.select(selector)  # warm up cache
        benchmark.pedantic(lambda: bench_soup.select(selector), rounds=500, warmup_rounds=20)
        assert benchmark.stats["mean"] < 0.001  # <1ms (dev build; cached avoids re-parse)


class TestOptimisedPathsBenchmark:
    """Query paths moved into Rust / made linear. Thresholds catch a regression
    to the old behaviour (Python-side string search, quadratic nth-child,
    unbounded select with limit) rather than tracking exact numbers."""

    @pytest.mark.benchmark(group="find-string")
    def test_ws_find_all_string(self, bench_soup, benchmark):
        result = benchmark.pedantic(
            lambda: bench_soup.find_all(string="Para 500"), rounds=200, warmup_rounds=10
        )
        assert len(result) == 1
        assert benchmark.stats["mean"] < 0.001  # 1ms (was ~6ms walking every node in Python)

    @pytest.mark.benchmark(group="find-string")
    def test_bs4_find_all_string(self, bs4_bench_soup, benchmark):
        benchmark.pedantic(
            lambda: bs4_bench_soup.find_all(string="Para 500"), rounds=50, warmup_rounds=3
        )

    @pytest.mark.benchmark(group="select-nth")
    def test_ws_select_nth_child(self, bench_soup, benchmark):
        # 1000 sibling <div>s: quadratic sibling counting would take several ms.
        result = benchmark.pedantic(
            lambda: bench_soup.select("div:nth-child(2n)"), rounds=100, warmup_rounds=5
        )
        assert len(result) == 500
        assert benchmark.stats["mean"] < 0.002

    @pytest.mark.benchmark(group="select-nth")
    def test_ws_select_nth_last_child(self, bench_soup, benchmark):
        result = benchmark.pedantic(
            lambda: bench_soup.select("div:nth-last-child(odd)"), rounds=100, warmup_rounds=5
        )
        assert len(result) == 500
        assert benchmark.stats["mean"] < 0.002

    @pytest.mark.benchmark(group="select-nth")
    def test_bs4_select_nth_child(self, bs4_bench_soup, benchmark):
        benchmark.pedantic(
            lambda: bs4_bench_soup.select("div:nth-child(2n)"), rounds=10, warmup_rounds=1
        )

    @pytest.mark.benchmark(group="select-limit")
    def test_ws_select_with_limit(self, bench_soup, benchmark):
        result = benchmark.pedantic(
            lambda: bench_soup.select("a.link", limit=5), rounds=1000, warmup_rounds=10
        )
        assert len(result) == 5
        assert benchmark.stats["mean"] < 0.00005  # 50µs: stops after 5 matches

    @pytest.mark.benchmark(group="select-per-element")
    def test_ws_select_one_per_element(self, bench_soup, benchmark):
        """Typical scraping loop: one small subtree query per item."""
        items = bench_soup.find_all("div", class_="item")
        result = benchmark.pedantic(
            lambda: [d.select_one("a.link") for d in items], rounds=50, warmup_rounds=3
        )
        assert len(result) == 1000
        assert benchmark.stats["mean"] < 0.005

    @pytest.mark.benchmark(group="select-per-element")
    def test_bs4_select_one_per_element(self, bs4_bench_soup, benchmark):
        items = bs4_bench_soup.find_all("div", class_="item")
        benchmark.pedantic(
            lambda: [d.select_one("a.link") for d in items], rounds=5, warmup_rounds=1
        )

    @pytest.mark.benchmark(group="text")
    def test_ws_get_text_strip(self, bench_soup, benchmark):
        benchmark.pedantic(
            lambda: bench_soup.get_text(" ", strip=True), rounds=200, warmup_rounds=10
        )
        assert benchmark.stats["mean"] < 0.002

    @pytest.mark.benchmark(group="text")
    def test_bs4_get_text_strip(self, bs4_bench_soup, benchmark):
        benchmark.pedantic(
            lambda: bs4_bench_soup.get_text(" ", strip=True), rounds=50, warmup_rounds=3
        )


class TestNavigationBenchmark:
    """Per-item lookups and tree iteration. These are dominated by Python-object
    creation, so they guard the `find()` fast path and the pre-classified
    `*_items` wrapping (one Rust call per list, no per-node `node_type` /
    `text_content` round trips)."""

    @pytest.mark.benchmark(group="find-per-item")
    def test_ws_find_per_item(self, bench_soup, benchmark):
        items = bench_soup.find_all("div", class_="item")
        result = benchmark.pedantic(
            lambda: [d.find("a") for d in items], rounds=50, warmup_rounds=3
        )
        assert len(result) == 1000
        assert benchmark.stats["mean"] < 0.002  # ~0.35ms release; >1.5ms means the fast path is gone

    @pytest.mark.benchmark(group="find-per-item")
    def test_bs4_find_per_item(self, bs4_bench_soup, benchmark):
        items = bs4_bench_soup.find_all("div", class_="item")
        benchmark.pedantic(lambda: [d.find("a") for d in items], rounds=10, warmup_rounds=1)

    @pytest.mark.benchmark(group="iterate-descendants")
    def test_ws_iterate_descendants(self, bench_soup, benchmark):
        body = bench_soup.find("body")
        count = benchmark.pedantic(
            lambda: sum(1 for _ in body.descendants), rounds=30, warmup_rounds=3
        )
        assert count == 5000  # div, p, text, a, text per item
        assert benchmark.stats["mean"] < 0.004

    @pytest.mark.benchmark(group="iterate-descendants")
    def test_bs4_iterate_descendants(self, bs4_bench_soup, benchmark):
        body = bs4_bench_soup.find("body")
        benchmark.pedantic(lambda: sum(1 for _ in body.descendants), rounds=10, warmup_rounds=1)

    @pytest.mark.benchmark(group="iterate-strings")
    def test_ws_strings(self, bench_soup, benchmark):
        body = bench_soup.find("body")
        result = benchmark.pedantic(lambda: list(body.strings), rounds=30, warmup_rounds=3)
        assert len(result) == 2000
        assert result[0].parent is not None
        assert benchmark.stats["mean"] < 0.003

    @pytest.mark.benchmark(group="iterate-contents")
    def test_ws_contents_per_item(self, bench_soup, benchmark):
        items = bench_soup.find_all("div", class_="item")
        benchmark.pedantic(lambda: [d.contents for d in items], rounds=30, warmup_rounds=3)
        assert benchmark.stats["mean"] < 0.003

    @pytest.mark.benchmark(group="string-per-item")
    def test_ws_string_per_item(self, bench_soup, benchmark):
        paras = bench_soup.find_all("p")
        result = benchmark.pedantic(lambda: [p.string for p in paras], rounds=30, warmup_rounds=3)
        assert result[0] == "Para 0"
        assert benchmark.stats["mean"] < 0.002


class TestThreadScaling:
    """Queries over large subtrees release the GIL, so threads sharing one
    document overlap their Rust work."""

    @pytest.mark.skipif((os.cpu_count() or 1) < 4, reason="needs 4+ cores")
    def test_queries_scale_across_threads(self, parse):
        # One ~60k-node document, well above the GIL-release threshold, with
        # enough Rust work per call that thread start-up doesn't dominate.
        soup = parse(BENCH_HTML.replace("</body></html>", "") + "".join(
            f'<div class="item"><p class="text">More {i}</p><a class="link" data-i="{i}">x</a></div>'
            for i in range(15_000)
        ) + "</body></html>")

        def work():
            for _ in range(15):
                soup.select("div.item > p.text + a.link[data-i]")
                soup.find_all("a", class_="link", limit=1)
                soup.get_text()

        def run(n):
            threads = [threading.Thread(target=work) for _ in range(n)]
            t0 = time.perf_counter()
            for t in threads:
                t.start()
            for t in threads:
                t.join()
            return time.perf_counter() - t0

        run(1)
        one = min(run(1) for _ in range(3))
        four = min(run(4) for _ in range(3))
        speedup = 4 * one / four
        print(f"\n1 thread {one*1e3:.0f}ms | 4 threads (4x work) {four*1e3:.0f}ms | speedup {speedup:.2f}x")
        # Without GIL release this is ~1.0x; object creation still holds the GIL.
        assert speedup > 1.1, f"threads did not overlap: {speedup:.2f}x"
