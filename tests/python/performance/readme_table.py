"""
readme_table.py — Regenerates the performance table in README.md.

Compares WhiskeySour with bs4 + html.parser on synthetic 10/100/500 KB
listing pages; query rows run on the 100 KB document. Prints the table as
Markdown. Each figure is the best of three medians, to damp machine noise.

Build with `maturin develop --release` first — dev-build numbers are 2–3×
slower and must not be quoted.

Usage:
    python tests/python/performance/readme_table.py
"""

from __future__ import annotations

import platform
import statistics
import time
from typing import Callable

import bs4
from bs4 import BeautifulSoup

import whiskeysour as ws


def make_doc(rows: int) -> str:
    """A listing page with `rows` items (~175 bytes each)."""
    items = "".join(
        f'<div id="d{i}" class="item {"odd" if i % 2 else "even"}" data-i="{i}">'
        f'<h3 class="title">Item {i}</h3><p class="text">Some body text for item {i}.</p>'
        f'<a href="/item/{i}" class="link">Read more</a></div>'
        for i in range(rows)
    )
    return f"<!DOCTYPE html><html><body>{items}</body></html>"


def median_time(fn: Callable[[], object], budget: float = 0.3) -> float:
    """Median seconds per call, timing batches of ~2 ms for ~`budget` seconds."""
    fn()
    calls, start = 0, time.perf_counter()
    while time.perf_counter() - start < 0.03:
        fn()
        calls += 1
    batch = max(1, int(0.002 / ((time.perf_counter() - start) / calls)))

    samples: list[float] = []
    deadline = time.perf_counter() + budget
    while time.perf_counter() < deadline or len(samples) < 15:
        t0 = time.perf_counter()
        for _ in range(batch):
            fn()
        samples.append((time.perf_counter() - t0) / batch)
    return statistics.median(samples)


def fmt(seconds: float) -> str:
    if seconds >= 1e-3:
        return f"{seconds * 1e3:.2f} ms"
    if seconds >= 1e-5:
        return f"{seconds * 1e6:.0f} µs"
    return f"{seconds * 1e6:.2f} µs"


def fmt_ratio(r: float) -> str:
    return f"{r:.0f}×" if r >= 10 else f"{r:.1f}×"


def main() -> None:
    sizes = {"10 KB": make_doc(55), "100 KB": make_doc(560), "500 KB": make_doc(2800)}
    html = sizes["100 KB"]
    w, b = ws.WhiskeySour(html), BeautifulSoup(html, "html.parser")
    w_tag, b_tag = w.find("a"), b.find("a")
    w_items, b_items = w.find_all("div")[:100], b.find_all("div")[:100]
    w_body, b_body = w.find("body"), b.find("body")

    rows: list[tuple[str, Callable[[], object], Callable[[], object]]] = [
        (f"Parse {label}", lambda h=h: ws.WhiskeySour(h), lambda h=h: BeautifulSoup(h, "html.parser"))
        for label, h in sizes.items()
    ]
    rows += [
        ("`find(id=…)`", lambda: w.find(id="d280"), lambda: b.find(id="d280")),
        ("`find_all(class_=…)`", lambda: w.find_all(class_="odd"), lambda: b.find_all(class_="odd")),
        ("`find_all(string=…)`", lambda: w.find_all(string="Item 280"), lambda: b.find_all(string="Item 280")),
        ('`select("div.item")`', lambda: w.select("div.item"), lambda: b.select("div.item")),
        ('`select("div:nth-child(2n)")`', lambda: w.select("div:nth-child(2n)"), lambda: b.select("div:nth-child(2n)")),
        ("`select_one(…)` on 100 items",
         lambda: [d.select_one("a.link") for d in w_items],
         lambda: [d.select_one("a.link") for d in b_items]),
        ("`find(…)` on 100 items",
         lambda: [d.find("a") for d in w_items],
         lambda: [d.find("a") for d in b_items]),
        ("iterate `.descendants`",
         lambda: sum(1 for _ in w_body.descendants),
         lambda: sum(1 for _ in b_body.descendants)),
        ("`list(.strings)`", lambda: list(w_body.strings), lambda: list(b_body.strings)),
        ("`get_text()`", lambda: w.get_text(), lambda: b.get_text()),
        ("`str()` (serialize)", lambda: str(w), lambda: str(b)),
        ("`prettify()`", lambda: w.prettify(), lambda: b.prettify()),
        ('`tag.get("class")`', lambda: w_tag.get("class"), lambda: b_tag.get("class")),
    ]

    best: dict[str, tuple[float, float]] = {}
    for _ in range(3):
        for name, ws_fn, bs4_fn in rows:
            ws_t, bs4_t = median_time(ws_fn), median_time(bs4_fn)
            prev_ws, prev_bs4 = best.get(name, (float("inf"), float("inf")))
            best[name] = (min(ws_t, prev_ws), min(bs4_t, prev_bs4))

    print(f"<!-- {platform.platform()}, Python {platform.python_version()}, bs4 {bs4.__version__} -->")
    print("| Operation | WhiskeySour | bs4 + html.parser | Speedup |")
    print("|-----------|-------------|-------------------|---------|")
    for name, _, _ in rows:
        ws_t, bs4_t = best[name]
        ratio = bs4_t / ws_t
        speedup = f"**{fmt_ratio(ratio)}**" if ratio >= 1 else f"bs4 ~{fmt_ratio(1 / ratio)} faster"
        print(f"| {name} | {fmt(ws_t)} | {fmt(bs4_t)} | {speedup} |")


if __name__ == "__main__":
    main()
