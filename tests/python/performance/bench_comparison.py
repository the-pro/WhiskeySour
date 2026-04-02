"""
bench_comparison.py — Head-to-head performance comparison: WhiskeySour vs BeautifulSoup.

Generates a self-contained HTML report at bench_report.html.

Usage:
    python tests/python/performance/bench_comparison.py
    python tests/python/performance/bench_comparison.py --output /tmp/report.html
    python tests/python/performance/bench_comparison.py --rounds 200
"""

from __future__ import annotations

import argparse
import json
import math
import statistics
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

# ---------------------------------------------------------------------------
# Fixture HTML generators
# ---------------------------------------------------------------------------

def _make_small() -> str:
    """~5KB: typical blog post / article page."""
    parts = ["<!DOCTYPE html><html lang='en'><head><meta charset='UTF-8'>"
             "<title>Article</title></head><body>"]
    for i in range(80):
        parts.append(
            f'<div class="item" id="item-{i}" data-index="{i}">'
            f'<h3 class="title">Heading {i}</h3>'
            f'<p class="body">Some body text for item number {i}.</p>'
            f'<a href="/item/{i}" class="link">Read more</a>'
            f'</div>'
        )
    parts.append("</body></html>")
    return "".join(parts)


def _make_medium() -> str:
    """~50KB: large listing page."""
    parts = ["<!DOCTYPE html><html><head><title>Listing</title></head><body>"
             '<table id="main-table" class="data-table"><tbody>']
    for i in range(500):
        cls = "even" if i % 2 == 0 else "odd"
        parts.append(
            f'<tr class="{cls}" id="row-{i}">'
            f'<td class="col-id">{i}</td>'
            f'<td class="col-name">Product {i}</td>'
            f'<td class="col-price">${i * 1.5:.2f}</td>'
            f'<td class="col-tag"><span class="badge">'
            f'{"active" if i % 3 == 0 else "inactive"}</span></td>'
            f'</tr>'
        )
    parts.append("</tbody></table></body></html>")
    return "".join(parts)


def _make_large() -> str:
    """~300KB: very large DOM."""
    parts = ["<!DOCTYPE html><html><head><title>Large</title></head><body>"]
    for section in range(20):
        parts.append(f'<section id="s{section}" class="section">')
        for i in range(150):
            parts.append(
                f'<article id="a{section}-{i}" class="card {"featured" if i % 10 == 0 else "normal"}">'
                f'<h2 class="title">Section {section} Item {i}</h2>'
                f'<p class="summary">Summary text for article {i} in section {section}.'
                f' More content here to pad the size.</p>'
                f'<footer class="meta"><span class="author">Author {i % 5}</span>'
                f'<time datetime="2024-0{(i%9)+1}-01">Jan {i%28+1}</time></footer>'
                f'</article>'
            )
        parts.append("</section>")
    parts.append("</body></html>")
    return "".join(parts)


def _make_deeply_nested() -> str:
    """Deep nesting stress test."""
    depth = 50
    parts = ["<!DOCTYPE html><html><body>"]
    for d in range(depth):
        parts.append(f'<div class="level-{d}" id="l{d}">')
    parts.append('<span class="deep-leaf">leaf text</span>')
    for _ in range(depth):
        parts.append("</div>")
    parts.append("</body></html>")
    return "".join(parts)


def _make_attributes() -> str:
    """Many attributes per element."""
    parts = ["<!DOCTYPE html><html><body>"]
    for i in range(300):
        parts.append(
            f'<div id="el{i}" class="a b c d e" data-x="{i}" data-y="{i*2}"'
            f' data-name="item-{i}" aria-label="Item {i}" role="listitem"'
            f' tabindex="{i}" data-value="{i * 3.14:.4f}">text {i}</div>'
        )
    parts.append("</body></html>")
    return "".join(parts)


FIXTURES: dict[str, tuple[str, str]] = {
    "small (~5KB)":        ("small",   _make_small()),
    "medium (~50KB)":      ("medium",  _make_medium()),
    "large (~300KB)":      ("large",   _make_large()),
    "deep nesting":        ("deep",    _make_deeply_nested()),
    "many attributes":     ("attrs",   _make_attributes()),
}

# ---------------------------------------------------------------------------
# Benchmark runner
# ---------------------------------------------------------------------------

def measure(fn: Callable, rounds: int, warmup: int = 3) -> dict[str, float]:
    """Run fn `rounds` times, return stats dict (seconds)."""
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(rounds):
        t0 = time.perf_counter()
        fn()
        times.append(time.perf_counter() - t0)
    return {
        "mean":   statistics.mean(times),
        "median": statistics.median(times),
        "stdev":  statistics.stdev(times) if len(times) > 1 else 0.0,
        "min":    min(times),
        "max":    max(times),
        "rounds": rounds,
    }


# ---------------------------------------------------------------------------
# Operation suites
# ---------------------------------------------------------------------------

def run_suite(html: str, rounds: int) -> dict[str, dict[str, dict]]:
    """
    Returns { operation_name: { "ws": stats, "bs4": stats } }
    """
    import whiskysour as ws
    from bs4 import BeautifulSoup

    results: dict[str, dict[str, dict]] = {}

    # --- parse ---
    results["parse"] = {
        "ws":  measure(lambda: ws.WhiskeySour(html, "html.parser"), rounds),
        "bs4": measure(lambda: BeautifulSoup(html, "html.parser"), rounds),
    }

    # Pre-parse once for query benchmarks
    ws_doc  = ws.WhiskeySour(html, "html.parser")
    bs4_doc = BeautifulSoup(html, "html.parser")

    # Pick a tag that actually exists in this fixture for meaningful find benchmarks.
    # All fixtures guarantee at least one element with these tags.
    _all_tags = set(t.name for t in ws_doc.find_all(True) if t.name)
    _bench_tag = next((t for t in ("div", "tr", "li", "article", "span", "p") if t in _all_tags), "html")
    _all_ids   = [t.get("id") for t in ws_doc.find_all(True) if t.get("id")]
    _bench_id  = _all_ids[len(_all_ids) // 2] if _all_ids else None
    _all_cls   = sorted({cls for t in ws_doc.find_all(True) for cls in (t.get("class") or [])}, key=lambda c: c)
    _bench_cls = _all_cls[len(_all_cls) // 2] if _all_cls else None

    # --- find (first match) ---
    results["find (first tag)"] = {
        "ws":  measure(lambda: ws_doc.find(_bench_tag), rounds),
        "bs4": measure(lambda: bs4_doc.find(_bench_tag), rounds),
    }

    # --- find_all ---
    results["find_all"] = {
        "ws":  measure(lambda: ws_doc.find_all(_bench_tag), rounds),
        "bs4": measure(lambda: bs4_doc.find_all(_bench_tag), rounds),
    }

    # --- find by id ---
    if _bench_id:
        results["find by id"] = {
            "ws":  measure(lambda: ws_doc.find(id=_bench_id), rounds),
            "bs4": measure(lambda: bs4_doc.find(id=_bench_id), rounds),
        }

    # --- find by class ---
    if _bench_cls:
        results["find_all by class"] = {
            "ws":  measure(lambda: ws_doc.find_all(class_=_bench_cls), rounds),
            "bs4": measure(lambda: bs4_doc.find_all(class_=_bench_cls), rounds),
        }

    # --- CSS select ---
    results["CSS select"] = {
        "ws":  measure(lambda: ws_doc.select(_bench_tag), rounds),
        "bs4": measure(lambda: bs4_doc.select(_bench_tag), rounds),
    }

    # --- CSS select_one ---
    results["CSS select_one"] = {
        "ws":  measure(lambda: ws_doc.select_one(_bench_tag), rounds),
        "bs4": measure(lambda: bs4_doc.select_one(_bench_tag), rounds),
    }

    # --- get_text ---
    results["get_text"] = {
        "ws":  measure(lambda: ws_doc.get_text(), rounds),
        "bs4": measure(lambda: bs4_doc.get_text(), rounds),
    }

    # --- str (serialize) ---
    results["serialize (str)"] = {
        "ws":  measure(lambda: str(ws_doc), rounds),
        "bs4": measure(lambda: str(bs4_doc), rounds),
    }

    return results


# ---------------------------------------------------------------------------
# HTML report generator
# ---------------------------------------------------------------------------

_REPORT_TEMPLATE = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>WhiskeySour vs BeautifulSoup — Performance Report</title>
<script src="https://cdn.jsdelivr.net/npm/chart.js@4.4.0/dist/chart.umd.min.js"></script>
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}

  body {{
    background: #f5f5f5;
    color: #1a1a1a;
    font-family: system-ui, -apple-system, sans-serif;
    font-size: 15px;
    line-height: 1.6;
    padding: 1rem;
  }}

  .page {{ max-width: 900px; margin: 0 auto; }}

  header {{ padding: 1.25rem 0 1rem; border-bottom: 2px solid #d0d0d0; margin-bottom: 1.5rem; }}
  h1 {{ font-size: 1.3rem; font-weight: 700; margin-bottom: .2rem; }}
  .meta {{ color: #666; font-size: .82rem; }}

  .legend {{
    display: flex; flex-wrap: wrap; gap: .75rem 1.5rem;
    margin-bottom: 1.5rem; font-size: .88rem;
  }}
  .legend-item {{ display: flex; align-items: center; gap: .4rem; }}
  .swatch {{ width: 12px; height: 12px; border-radius: 2px; flex-shrink: 0; }}
  .swatch-ws  {{ background: #c05c00; }}
  .swatch-bs4 {{ background: #1a56a0; }}

  /* Summary row */
  .summary {{
    display: grid;
    grid-template-columns: repeat(2, 1fr);
    gap: .75rem;
    margin-bottom: 2rem;
  }}
  @media (min-width: 480px) {{ .summary {{ grid-template-columns: repeat(4, 1fr); }} }}
  .stat {{
    background: #fff;
    border: 1px solid #d8d8d8;
    border-radius: 6px;
    padding: .85rem .75rem;
    text-align: center;
  }}
  .stat-num   {{ font-size: 1.6rem; font-weight: 700; line-height: 1.2; color: #1d7a3a; }}
  .stat-label {{ font-size: .75rem; color: #666; margin-top: .2rem; }}

  /* Fixture sections */
  .fixture-section {{ margin-bottom: 2.5rem; }}
  h2 {{
    font-size: 1rem;
    font-weight: 600;
    margin-bottom: .75rem;
    padding-bottom: .35rem;
    border-bottom: 1px solid #d0d0d0;
  }}

  /* Charts */
  .charts-grid {{
    display: grid;
    grid-template-columns: 1fr;
    gap: .75rem;
    margin-bottom: 1rem;
  }}
  @media (min-width: 540px)  {{ .charts-grid {{ grid-template-columns: repeat(2, 1fr); }} }}
  @media (min-width: 800px)  {{ .charts-grid {{ grid-template-columns: repeat(3, 1fr); }} }}

  .chart-card {{
    background: #fff;
    border: 1px solid #d8d8d8;
    border-radius: 6px;
    padding: .85rem;
  }}
  .chart-label {{ font-size: .78rem; color: #555; margin-bottom: .5rem; font-weight: 500; }}
  .chart-wrap  {{ position: relative; height: 130px; }}

  /* Table */
  .table-wrap {{ overflow-x: auto; -webkit-overflow-scrolling: touch; }}
  table {{
    width: 100%;
    min-width: 520px;
    border-collapse: collapse;
    font-size: .8rem;
    background: #fff;
    border: 1px solid #d8d8d8;
    border-radius: 6px;
    overflow: hidden;
  }}
  thead tr {{ background: #efefef; }}
  th {{
    text-align: left;
    padding: .5rem .7rem;
    font-weight: 600;
    color: #444;
    border-bottom: 1px solid #d0d0d0;
    white-space: nowrap;
  }}
  td {{ padding: .45rem .7rem; border-bottom: 1px solid #ebebeb; vertical-align: middle; }}
  tr:last-child td {{ border-bottom: none; }}
  tr:nth-child(even) {{ background: #fafafa; }}

  .ws-val  {{ color: #c05c00; font-family: monospace; white-space: nowrap; }}
  .bs4-val {{ color: #1a56a0; font-family: monospace; white-space: nowrap; }}
  .op      {{ font-weight: 500; white-space: nowrap; }}
  .faster  {{ color: #1d7a3a; font-weight: 700; font-family: monospace; white-space: nowrap; }}
  .slower  {{ color: #b91c1c; font-weight: 700; font-family: monospace; white-space: nowrap; }}
</style>
</head>
<body>
<div class="page">

<header>
  <h1>WhiskeySour vs BeautifulSoup — Performance Report</h1>
  <p class="meta">Generated {timestamp} &nbsp;·&nbsp; {rounds} rounds per benchmark &nbsp;·&nbsp; Python {pyver}</p>
</header>

<div class="legend">
  <span class="legend-item"><span class="swatch swatch-ws"></span><strong>WhiskeySour</strong> (Rust/PyO3)</span>
  <span class="legend-item"><span class="swatch swatch-bs4"></span><strong>BeautifulSoup 4</strong> (html.parser)</span>
</div>

{summary_cards}

{fixture_sections}

</div>
<script>
const data = {chart_data_json};
const WS_COLOR  = '#c05c00';
const BS4_COLOR = '#1a56a0';

function makeBarChart(canvasId, wsMs, bs4Ms) {{
  const ctx = document.getElementById(canvasId);
  if (!ctx) return;
  new Chart(ctx, {{
    type: 'bar',
    data: {{
      labels: ['WS', 'BS4'],
      datasets: [{{
        data: [wsMs, bs4Ms],
        backgroundColor: [WS_COLOR, BS4_COLOR],
        borderRadius: 3,
        barPercentage: 0.55,
      }}]
    }},
    options: {{
      responsive: true,
      maintainAspectRatio: false,
      plugins: {{
        legend: {{ display: false }},
        tooltip: {{ callbacks: {{ label: c => ` ${{c.raw.toFixed(4)}} ms` }} }}
      }},
      scales: {{
        y: {{
          beginAtZero: true,
          ticks: {{ color: '#666', font: {{ size: 10 }}, callback: v => v + ' ms' }},
          grid: {{ color: '#e8e8e8' }}
        }},
        x: {{
          ticks: {{ color: '#444', font: {{ size: 11, weight: '600' }} }},
          grid: {{ display: false }}
        }}
      }}
    }}
  }});
}}

Object.entries(data).forEach(([, ops]) => {{
  Object.entries(ops).forEach(([, vals]) => {{
    makeBarChart(vals.canvas_id, vals.ws_ms, vals.bs4_ms);
  }});
}});
</script>
</body>
</html>
"""


def _fmt_ms(s: float) -> str:
    ms = s * 1000
    if ms < 0.001:
        return f"{ms*1000:.3f} µs"
    return f"{ms:.4f} ms"


def _speedup_html(ws_mean: float, bs4_mean: float) -> str:
    if ws_mean <= 0:
        return "—"
    ratio = bs4_mean / ws_mean
    cls = "faster" if ratio >= 1 else "slower"
    if ratio >= 1:
        label = f"{ratio:.1f}× faster"
    else:
        label = f"{1/ratio:.1f}× slower"
    return f'<span class="{cls}">{label}</span>'


def generate_html_report(
    all_results: dict[str, dict[str, dict[str, dict]]],
    rounds: int,
) -> str:
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    pyver = f"{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"

    # Build chart data dict for JS
    chart_data: dict[str, dict] = {}
    canvas_counter = 0

    # Summary stats
    total_ops = 0
    ws_wins = 0
    speedups: list[float] = []

    fixture_sections_html = []

    for fixture_label, ops in all_results.items():
        fixture_key, _ = [v for k, v in FIXTURES.items() if k == fixture_label][0] if fixture_label in FIXTURES else (fixture_label, "")
        # actually we stored the key differently; let's just slugify
        fixture_slug = fixture_label.replace(" ", "_").replace("(", "").replace(")", "").replace("~", "").replace("/", "_")
        chart_data[fixture_slug] = {}

        size_kb = sum(len(v.encode()) for v in []) / 1024  # placeholder

        rows_html = []
        charts_html = []

        for op_name, lib_stats in ops.items():
            ws_stats  = lib_stats["ws"]
            bs4_stats = lib_stats["bs4"]
            ws_mean   = ws_stats["mean"]
            bs4_mean  = bs4_stats["mean"]
            ratio     = bs4_mean / ws_mean if ws_mean > 0 else 1

            total_ops += 1
            if ratio >= 1:
                ws_wins += 1
            speedups.append(ratio)

            canvas_id = f"chart_{canvas_counter}"
            canvas_counter += 1

            chart_data[fixture_slug][op_name] = {
                "canvas_id": canvas_id,
                "ws_ms":     ws_mean * 1000,
                "bs4_ms":    bs4_mean * 1000,
            }

            rows_html.append(f"""
              <tr>
                <td class="op">{op_name}</td>
                <td class="ws-val">{_fmt_ms(ws_mean)}</td>
                <td class="ws-val">{_fmt_ms(ws_stats["min"])} – {_fmt_ms(ws_stats["max"])}</td>
                <td class="bs4-val">{_fmt_ms(bs4_mean)}</td>
                <td class="bs4-val">{_fmt_ms(bs4_stats["min"])} – {_fmt_ms(bs4_stats["max"])}</td>
                <td>{_speedup_html(ws_mean, bs4_mean)}</td>
              </tr>""")

            charts_html.append(f"""
              <div class="chart-card">
                <div class="chart-label">{op_name}</div>
                <div class="chart-wrap"><canvas id="{canvas_id}"></canvas></div>
              </div>""")

        table = f"""
          <div class="table-wrap">
          <table>
            <thead>
              <tr>
                <th>Operation</th>
                <th>WS mean</th>
                <th>WS min – max</th>
                <th>BS4 mean</th>
                <th>BS4 min – max</th>
                <th>Speedup</th>
              </tr>
            </thead>
            <tbody>{"".join(rows_html)}</tbody>
          </table>
          </div>"""

        charts_grid = f'<div class="charts-grid">{"".join(charts_html)}</div>'

        fixture_sections_html.append(f"""
          <div class="fixture-section">
            <h2>{fixture_label}</h2>
            {charts_grid}
            {table}
          </div>""")

    # Summary cards
    avg_speedup = statistics.mean(speedups) if speedups else 1.0
    max_speedup = max(speedups) if speedups else 1.0
    win_pct     = 100 * ws_wins / total_ops if total_ops else 0
    summary_cards = f"""
      <div class="summary">
        <div class="stat">
          <div class="stat-num">{avg_speedup:.1f}×</div>
          <div class="stat-label">Avg speedup (WS vs BS4)</div>
        </div>
        <div class="stat">
          <div class="stat-num">{max_speedup:.1f}×</div>
          <div class="stat-label">Peak speedup</div>
        </div>
        <div class="stat">
          <div class="stat-num">{win_pct:.0f}%</div>
          <div class="stat-label">Operations WS wins</div>
        </div>
        <div class="stat">
          <div class="stat-num">{total_ops}</div>
          <div class="stat-label">Total benchmarks</div>
        </div>
      </div>"""

    return _REPORT_TEMPLATE.format(
        timestamp=timestamp,
        rounds=rounds,
        pyver=pyver,
        summary_cards=summary_cards,
        fixture_sections="".join(fixture_sections_html),
        chart_data_json=json.dumps(chart_data),
    )


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="WhiskeySour vs BeautifulSoup benchmark")
    parser.add_argument("--output", default="bench_report.html",
                        help="Output HTML file path (default: bench_report.html)")
    parser.add_argument("--rounds", type=int, default=100,
                        help="Benchmark rounds per operation (default: 100)")
    parser.add_argument("--fixture", choices=["small", "medium", "large", "deep", "attrs", "all"],
                        default="all", help="Which fixture to run (default: all)")
    args = parser.parse_args()

    try:
        import whiskysour  # noqa
    except ImportError:
        sys.exit("ERROR: whiskysour not installed. Run: maturin develop")
    try:
        import bs4  # noqa
    except ImportError:
        sys.exit("ERROR: beautifulsoup4 not installed. Run: pip install beautifulsoup4")

    fixture_filter = args.fixture
    selected = {
        label: (key, html)
        for label, (key, html) in FIXTURES.items()
        if fixture_filter == "all" or key == fixture_filter
    }

    all_results: dict[str, dict[str, dict[str, dict]]] = {}
    for label, (key, html) in selected.items():
        size_kb = len(html.encode()) / 1024
        print(f"  Benchmarking: {label}  ({size_kb:.0f} KB, {args.rounds} rounds) ...", flush=True)
        all_results[label] = run_suite(html, args.rounds)

    print("\nGenerating report...")
    report_html = generate_html_report(all_results, args.rounds)

    out_path = Path(args.output)
    out_path.write_text(report_html, encoding="utf-8")
    print(f"Report written to: {out_path.resolve()}")
    print("\nOpen it with:")
    print(f"  open {out_path.resolve()}")


if __name__ == "__main__":
    main()
