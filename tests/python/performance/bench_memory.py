"""
bench_memory.py — Memory usage benchmarks.

Uses tracemalloc to measure peak memory during parse.
Targets (Phase 4):
  1MB doc  → WhiskeySour < 5MB peak
  10MB doc → WhiskeySour < 40MB peak
"""

from __future__ import annotations

import gc
import tracemalloc

import pytest

pytestmark = pytest.mark.perf


def measure_peak_mb(fn) -> float:
    gc.collect()
    tracemalloc.start()
    try:
        fn()
    finally:
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
    return peak / (1024 * 1024)


def make_doc(num_nodes: int) -> str:
    parts = ["<!DOCTYPE html><html><body>"]
    for i in range(num_nodes):
        parts.append(f'<div id="d{i}" class="item" data-i="{i}"><p>Text {i}</p></div>')
    parts.append("</body></html>")
    return "".join(parts)


class TestMemoryUsage:
    @pytest.mark.slow
    def test_ws_1mb_doc_memory(self, parse):
        html = make_doc(20_000)  # ~1MB
        size_mb = len(html.encode("utf-8")) / (1024 * 1024)
        peak = measure_peak_mb(lambda: parse(html))
        print(f"\nDoc size: {size_mb:.1f}MB | WhiskeySour peak: {peak:.1f}MB")
        assert peak < 5, f"WhiskeySour used {peak:.1f}MB for 1MB doc (target <5MB)"

    @pytest.mark.slow
    def test_ws_vs_bs4_memory_ratio(self, parse):
        bs4 = pytest.importorskip("bs4", reason="bs4 not installed")
        html = make_doc(5_000)
        ws_peak = measure_peak_mb(lambda: parse(html))
        bs4_peak = measure_peak_mb(lambda: bs4.BeautifulSoup(html, "html.parser"))
        ratio = bs4_peak / ws_peak if ws_peak > 0 else float("inf")
        print(f"\nbs4: {bs4_peak:.1f}MB | WhiskeySour: {ws_peak:.1f}MB | Ratio: {ratio:.1f}x")
        assert ratio >= 5, (
            f"WhiskeySour should use at least 5x less memory than bs4. "
            f"Got {ratio:.1f}x (bs4={bs4_peak:.1f}MB, ws={ws_peak:.1f}MB)"
        )

    def test_node_count_vs_memory_linear(self, parse):
        """Memory growth should be roughly linear with node count."""
        small = make_doc(100)
        large = make_doc(10_000)
        small_peak = measure_peak_mb(lambda: parse(small))
        large_peak = measure_peak_mb(lambda: parse(large))
        # 100x more nodes should not use more than 200x memory (allow 2x overhead)
        if small_peak > 0:
            ratio = large_peak / small_peak
            assert ratio < 200, (
                f"Memory scaling not linear: {small_peak:.2f}MB → {large_peak:.2f}MB "
                f"({ratio:.0f}x for 100x more nodes)"
            )
