"""
bench_memory.py — Memory usage benchmarks.

Measures the peak resident set size (RSS) a parse adds to a fresh Python
process. `tracemalloc` is not used: it only sees Python's allocator, so it
misses the Rust arena entirely and makes WhiskeySour look almost free.

Measured with a release build (macOS arm64, 60k–150k nodes), including
attribute and text strings: WhiskeySour ≈ 210–250 bytes/node,
bs4 + html.parser ≈ 650 bytes/node (~3× less). Each Rust `Node` is 56 bytes
(boxed element data, 4-byte links), down from 272 bytes before the compact layout.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

pytestmark = pytest.mark.perf

resource = pytest.importorskip("resource", reason="peak RSS needs the POSIX resource module")

_SCRIPT = textwrap.dedent("""
    import resource, sys
    n, lib = int(sys.argv[1]), sys.argv[2]
    html = "<!DOCTYPE html><html><body>" + "".join(
        f'<div id="d{i}" class="item" data-i="{i}"><p>Text {i}</p></div>' for i in range(n)
    ) + "</body></html>"
    if lib == "ws":
        from whiskeysour import WhiskeySour
        doc = WhiskeySour(html)
    elif lib == "bs4":
        from bs4 import BeautifulSoup
        doc = BeautifulSoup(html, "html.parser")
    else:  # baseline: same interpreter + html string, no parse
        import whiskeysour  # noqa: F401
    rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    # ru_maxrss is bytes on macOS, kilobytes on Linux.
    print(rss if sys.platform == "darwin" else rss * 1024)
""")


def peak_rss_added_mb(num_divs: int, lib: str) -> float:
    """Peak RSS (MB) a parse of `num_divs` rows adds over an identical process that doesn't parse."""
    def run(which: str) -> int:
        out = subprocess.run(
            [sys.executable, "-c", _SCRIPT, str(num_divs), which],
            check=True, capture_output=True, text=True,
        )
        return int(out.stdout.strip())

    return (run(lib) - run("baseline")) / (1024 * 1024)


def html_size_mb(num_divs: int) -> float:
    row = len('<div id="d0" class="item" data-i="0"><p>Text 0</p></div>') + 6
    return num_divs * row / (1024 * 1024)


class TestMemoryUsage:
    @pytest.mark.slow
    def test_ws_1mb_doc_memory(self):
        n = 20_000  # ~1.2MB of HTML, ~60k nodes
        peak = peak_rss_added_mb(n, "ws")
        print(f"\nDoc size: {html_size_mb(n):.1f}MB | WhiskeySour peak RSS added: {peak:.1f}MB")
        assert peak < 40, f"WhiskeySour added {peak:.1f}MB for a ~1MB doc (target <40MB)"

    @pytest.mark.slow
    def test_ws_vs_bs4_memory_ratio(self):
        pytest.importorskip("bs4", reason="bs4 not installed")
        n = 20_000
        ws_peak = peak_rss_added_mb(n, "ws")
        bs4_peak = peak_rss_added_mb(n, "bs4")
        ratio = bs4_peak / ws_peak if ws_peak > 0 else float("inf")
        print(f"\nbs4: {bs4_peak:.1f}MB | WhiskeySour: {ws_peak:.1f}MB | Ratio: {ratio:.1f}x")
        assert ratio >= 2.2, (
            f"WhiskeySour should use clearly less memory than bs4. "
            f"Got {ratio:.1f}x (bs4={bs4_peak:.1f}MB, ws={ws_peak:.1f}MB)"
        )

    @pytest.mark.slow
    def test_node_count_vs_memory_linear(self):
        """Memory growth should be roughly linear with node count."""
        small = peak_rss_added_mb(5_000, "ws")
        large = peak_rss_added_mb(50_000, "ws")
        # 10x more nodes should not use more than 20x memory (allow 2x overhead)
        assert large < max(small, 1.0) * 20, (
            f"Memory scaling not linear: {small:.1f}MB → {large:.1f}MB for 10x more nodes"
        )
