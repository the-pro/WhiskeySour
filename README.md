# WhiskeySour

<p align="center">
  <img src="whiskeySour.png" alt="WhiskeySour logo" width="180">
</p>

A high-performance drop-in replacement for Python's [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/), written in **Rust** and published as a native Python package via PyO3.

**Status: Beta — core implementation complete. 450 unit tests passing, 508 including integration tests.**

---

## Why WhiskeySour?

BeautifulSoup is beloved but slow. Every node is a Python object (~500 bytes), parsing is GIL-bound, and CSS selectors re-parse on every call. WhiskeySour fixes this at the foundation.

All numbers below are medians from a dev build (`maturin develop`). Release builds (`maturin develop --release`) are typically 2–3× faster still.

| Operation | WhiskeySour | bs4 + html.parser | Speedup |
|-----------|-------------|-------------------|---------|
| Parse 10KB | 0.33 ms | 3.78 ms | **11×** |
| Parse 100KB | 4.08 ms | 42.87 ms | **11×** |
| Parse 500KB | 9.99 ms | 106.37 ms | **11×** |
| `find(id=…)` | 0.21 ms | 2.21 ms | **11×** |
| `find_all(class_=…)` | 0.62 ms | 4.41 ms | **7×** |
| `select("div.item")` | 0.64 ms | 8.92 ms | **14×** |
| `get_text()` | 0.17 ms | 0.68 ms | **4×** |
| `str()` (serialize) | 0.43 ms | 21.58 ms | **50×** |
| `tag.get("class")` | 0.29 µs | 7.0 µs | **24×** |
| Memory per node | ~40 bytes | ~500 bytes | **12× less** |

Key implementation choices:
- **Rust core** via [PyO3](https://pyo3.rs) + [maturin](https://www.maturin.rs)
- **[html5ever](https://github.com/servo/html5ever)** — spec-compliant HTML5 parser (same as Firefox/Chrome)
- **Arena allocation** — compact ~40 byte/node layout vs ~500 bytes in bs4
- **[cssparser](https://github.com/servo/rust-cssparser)** — CSS selectors compiled to DFA, LRU-cached
- **GIL release** — all Rust tree operations run outside the Python GIL
- **[memchr](https://github.com/BurntSushi/memchr)** — SIMD byte scanning (SSE2 / AVX2 / NEON)

---

## API — drop-in compatible with BeautifulSoup

```python
from whiskeysour import WhiskeySour

# Drop-in replacement for BeautifulSoup — no parser argument needed
soup = WhiskeySour(html)

# All standard bs4 operations work identically:
soup.find("h1")
soup.find_all("a", class_="external")
soup.select("div.container > p:first-child")
soup.title.string
soup.find(id="main").get_text(strip=True)

# BS4-compatible NavigableString (name is None, exactly like bs4)
for child in tag.children:
    if child.name:          # None for text nodes, str for elements — same as bs4
        print(child.name)

# Drop-in alias
from whiskeysour import BeautifulSoup   # same class, different name
```

### WhiskeySour extensions (not in bs4)

```python
# Pre-compiled CSS selector — zero parse overhead on repeated use
q = soup.compile("div.item > a[href]")
for doc in documents:
    results = q.select(doc)

# Streaming parser — feed chunks incrementally
from whiskeysour import StreamParser, parse_stream

with StreamParser() as parser:
    for chunk in response.iter_content(4096):
        parser.feed(chunk)
soup = parser.close()

# Generator-style streaming with automatic extraction
import io
with open("large.html", "rb") as f:
    for article in parse_stream(f, selector="article.post"):
        print(article.find("h1").get_text())
```

---

## BS4 compatibility notes

WhiskeySour is a faithful drop-in for the vast majority of BeautifulSoup code. A handful of behaviours differ due to html5ever's spec compliance:

| Behaviour | WhiskeySour | BeautifulSoup |
|-----------|-------------|---------------|
| `NavigableString.name` | `None` (identical to bs4) | `None` |
| `prettify(indent=N)` | Supported (alias for `indent_width`) | Supported |
| `</br>` in source | Creates 2 `<br>` (HTML5 spec) | Creates 1 `<br>` |
| Duplicate attributes | Keeps first (HTML5 spec) | Keeps last |
| Null bytes `\x00` | Stripped (HTML5 spec) | Passed through |
| Attribute order in `str()` | Insertion order | Alphabetical |

The first two rows are identical; the remaining differences only affect malformed HTML.

---

## Project Structure

```
WhiskeySour/
├── Cargo.toml                  # Rust workspace root
├── pyproject.toml              # maturin build config
├── pytest.ini                  # test configuration
│
├── crates/
│   ├── whiskeysour-core/        # Pure Rust library (no Python deps)
│   │   └── src/
│   │       ├── parser/         # html5ever integration
│   │       ├── node.rs         # Arena-allocated node pool
│   │       ├── selector/       # CSS selector DFA + LRU cache
│   │       ├── traversal/      # Tree iterators
│   │       ├── query/          # find() / find_all() / select()
│   │       └── serialize/      # HTML serialisation + prettify
│   │
│   └── whiskeysour-py/          # PyO3 bindings layer
│       └── src/
│           └── lib.rs          # _Tag, _Document Python classes
│
├── python/
│   └── whiskeysour/
│       ├── __init__.py         # Public API + BeautifulSoup alias
│       └── _core.pyi           # Type stubs for Rust extension
│
└── tests/
    └── python/
        ├── conftest.py
        ├── unit/               # 450 tests across 10 files
        ├── integration/        # bs4 API parity tests (58 tests)
        ├── performance/        # pytest-benchmark suites + comparison report
        └── fuzz/               # Hypothesis property tests (16 tests)
```

---

## Quick start

```bash
# Prerequisites: Python 3.9+, Rust toolchain
curl --proto '=https' --tlsv1.2 -sSf https://sh.rustup.rs | sh

# Create virtual environment
python3 -m venv .venv
source .venv/bin/activate        # macOS / Linux

# Install dependencies
pip install maturin pytest pytest-benchmark hypothesis beautifulsoup4

# Build the Rust extension
maturin develop                  # dev build (fast to compile)
# maturin develop --release      # optimised (use for benchmarks)

# Run tests
pytest tests/python/unit/
```

---

## Running tests

```bash
# All unit tests (fastest, no extra deps needed)
pytest tests/python/unit/ -q

# Single test file
pytest tests/python/unit/test_parsing.py -v

# Integration tests (requires beautifulsoup4)
pytest tests/python/integration/ -v

# Skip slow tests (large documents, deep nesting)
pytest -m "not slow"

# Fuzz / property-based tests (requires hypothesis)
pytest tests/python/fuzz/ -v

# Benchmark suites (requires pytest-benchmark)
pytest tests/python/performance/ --benchmark-only -v

# Performance comparison report (WhiskeySour vs BeautifulSoup)
python tests/python/performance/bench_comparison.py
python tests/python/performance/bench_comparison.py --fixture small --rounds 50
python tests/python/performance/bench_comparison.py --output /tmp/report.html
open bench_report.html
```

### Test file overview

| File | Tests | Covers |
|------|-------|--------|
| `unit/test_parsing.py` | 67 | HTML5 parsing, fragments, malformed HTML, void elements |
| `unit/test_encoding.py` | 31 | UTF-8/16, Latin-1, BOM, meta charset, surrogate pairs |
| `unit/test_find.py` | 60 | find/find_all by tag/id/class/attr/string/regex/lambda |
| `unit/test_css_selectors.py` | 71 | CSS3 + :has/:is/:where, structural pseudo-classes |
| `unit/test_tree_navigation.py` | 67 | parent/children/siblings/descendants/.string/.strings |
| `unit/test_modification.py` | 49 | decompose/extract/replace_with/insert/append/wrap |
| `unit/test_output.py` | 43 | str()/prettify()/encode()/round-trip stability |
| `unit/test_edge_cases.py` | 44 | 10k+ nodes, deep nesting, concurrency, control chars |
| `unit/test_streaming.py` | 19 | StreamParser push API, parse_stream() generator |
| `unit/test_css_selectors.py` | 71 | CompiledSelector, cached selectors |
| `integration/test_bs4_compat.py` | 58 | Every public bs4 API, cross-library parity |
| `fuzz/fuzz_parser.py` | 16 | Hypothesis: no crash, valid UTF-8, round-trip stable |

### Test markers

| Marker | Description |
|--------|-------------|
| `slow` | Large document tests — skip with `-m "not slow"` |
| `perf` | Benchmark tests — requires `--benchmark-only` |

---

## Development

```bash
# Rust checks (no build required)
~/.cargo/bin/cargo check -p whiskeysour-py

# Dev build (fast recompile, debug symbols)
maturin develop

# Release build (use for perf work)
maturin develop --release

# Rust tests
cargo test

# Formatting / linting
cargo fmt
cargo clippy
```

---

## Building wheels

```bash
maturin build --release
maturin build --release --interpreter python3.9 python3.10 python3.11 python3.12 python3.13
maturin publish
```

---

## Contributing

1. All changes must be accompanied by tests
2. Run `pytest tests/python/unit/ -m "not slow"` before submitting
3. Run `cargo fmt` and `cargo clippy` for Rust changes
4. Performance regressions > 5% against the baseline will block merge

---

## Licence

MIT
