# WhiskeySour

<p align="center">
  <img src="whiskeySour.png" alt="WhiskeySour logo" width="180">
</p>

A high-performance drop-in replacement for Python's [BeautifulSoup](https://www.crummy.com/software/BeautifulSoup/), written in **Rust** and published as a native Python package via PyO3.

**Status:** stable — 561 unit, 58 BS4-parity integration and 16 property-based fuzz tests passing.

---

## Why WhiskeySour?

BeautifulSoup is beloved but slow: every node is a Python object and all parsing and matching runs in Python. WhiskeySour keeps the same API and moves the tree, the parser and the query engine into Rust.

Measured with a release build (`maturin develop --release`) against `bs4` 4.15 + `html.parser`, Python 3.14, Apple Silicon. Query rows use the 100 KB document; each figure is the best of three medians.

| Operation | WhiskeySour | bs4 + html.parser | Speedup |
|-----------|-------------|-------------------|---------|
| Parse 10 KB | 105 µs | 1.42 ms | **14×** |
| Parse 100 KB | 1.04 ms | 14.49 ms | **14×** |
| Parse 500 KB | 5.27 ms | 73.07 ms | **14×** |
| `find(id=…)` | 8.29 µs | 636 µs | **77×** |
| `find_all(class_=…)` | 34 µs | 1.31 ms | **39×** |
| `find_all(string=…)` | 14 µs | 533 µs | **38×** |
| `select("div.item")` | 49 µs | 1.43 ms | **29×** |
| `select("div:nth-child(2n)")` | 38 µs | 2.18 ms | **58×** |
| `select_one(…)` on 100 items | 16 µs | 516 µs | **33×** |
| `find(…)` on 100 items | 37 µs | 241 µs | **6.6×** |
| `get_text()` | 14 µs | 195 µs | **14×** |
| `str()` (serialize) | 69 µs | 7.74 ms | **113×** |
| `prettify()` | 75 µs | 8.88 ms | **119×** |
| iterate `.descendants` | 491 µs | 111 µs | bs4 ~4.4× faster¹ |
| `list(.strings)` | 228 µs | 186 µs | bs4 ~1.2× faster¹ |
| `tag.get("class")` | 0.13 µs | 0.04 µs | bs4 ~3.1× faster¹ |
| Memory per node | ~210–250 bytes | ~650 bytes | **~3× less** |

¹ bs4's tree *is* a tree of Python objects, so walking it node by node or reading one attribute is a plain attribute/dict access. WhiskeySour has to cross into Rust and build a Python wrapper for each node it hands back (the Rust walk itself takes ~2.4 ns per node). When you need many nodes, let Rust do the filtering — `find_all`, `select`, `get_text()`, `find_all(string=…)` — rather than iterating `.descendants` / `.strings` in Python.

Regenerate this table with `python tests/python/performance/readme_table.py` and the memory row with `pytest tests/python/performance/bench_memory.py -s`. For an HTML report across five document shapes, run `python tests/python/performance/bench_comparison.py`.

Key implementation choices:
- **Rust core** via [PyO3](https://pyo3.rs) + [maturin](https://www.maturin.rs)
- **[html5ever](https://github.com/servo/html5ever)** — spec-compliant HTML5 parser (from the Servo project)
- **Compact arena** — the whole tree is one flat `Vec` of 56-byte nodes addressed by `u32` ids (element data is boxed, links are 4 bytes); traversal follows index links without allocating
- **Hand-written CSS selector engine** — parsed selectors are cached per thread, and `:nth-*` indices are memoised per query so long lists stay linear
- **GIL release** — parsing, and queries or serialisation over large subtrees, run outside the Python GIL, so threads can work on documents concurrently

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
# Reusable CSS selector — the parsed selector is cached, so repeated use skips re-parsing
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
│   ├── whiskysour-core/         # Pure Rust library (no Python deps)
│   │   └── src/
│   │       ├── parser/         # html5ever integration
│   │       ├── node.rs         # Arena-allocated node pool
│   │       ├── selector/       # CSS selector parser, matcher + per-thread cache
│   │       ├── traversal/      # Tree iterators
│   │       ├── query/          # find() / find_all() / select()
│   │       └── serialize/      # HTML serialisation + prettify
│   │   └── benches/            # Criterion benchmarks (parse, find, select, serialize)
│   │
│   └── whiskysour-py/           # PyO3 bindings layer
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
        ├── unit/               # 561 tests across 11 files
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

# Benchmark suites (requires pytest-benchmark; build with --release first).
# Includes per-item navigation benchmarks and a 4-thread scaling check.
pytest tests/python/performance/ --override-ini="addopts=" -q

# Memory usage vs bs4 (peak RSS in a fresh process; POSIX only)
pytest tests/python/performance/bench_memory.py --override-ini="addopts=" -q -s

# Rust-level Criterion benchmarks (parse, find_all/find_one, tree walk,
# get_text, select incl. :nth-*, serialize/prettify)
cargo bench -p whiskysour-core

# Performance comparison report (WhiskeySour vs BeautifulSoup)
python tests/python/performance/bench_comparison.py
python tests/python/performance/bench_comparison.py --fixture small --rounds 50
python tests/python/performance/bench_comparison.py --output /tmp/report.html
open bench_report.html
```

### Test file overview

| File | Tests | Covers |
|------|-------|--------|
| `unit/test_parsing.py` | 67 | HTML5 parsing, fragments, void elements |
| `unit/test_malformed_html.py` | 52 | Broken markup recovery, misnesting, stray tags |
| `unit/test_encoding.py` | 31 | UTF-8/16, Latin-1, BOM, meta charset, surrogate pairs |
| `unit/test_find.py` | 85 | find/find_all by tag/id/class/attr/string/regex/lambda, `limit` |
| `unit/test_css_selectors.py` | 83 | CSS3 + :has/:is/:where, structural pseudo-classes, `limit`, compiled selectors |
| `unit/test_tree_navigation.py` | 76 | parent/children/siblings/descendants/.string/.strings/get_text |
| `unit/test_modification.py` | 49 | decompose/extract/replace_with/insert/append/wrap |
| `unit/test_output.py` | 47 | str()/prettify()/encode()/escaping/round-trip stability |
| `unit/test_edge_cases.py` | 45 | 10k+ nodes, deep nesting, concurrency (incl. queries racing mutation), control chars |
| `unit/test_streaming.py` | 19 | StreamParser push API, parse_stream() generator |
| `unit/test_review_fixes.py` | 7 | Regression tests for reviewed bugs |
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
~/.cargo/bin/cargo check -p whiskysour-py

# Dev build (fast recompile, debug symbols)
maturin develop

# Release build (use for perf work)
maturin develop --release

# Rust tests
cargo test

# Formatting / linting (clippy warnings are errors)
cargo fmt
cargo clippy --all-targets -- -D warnings
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
3. Run `cargo fmt` and `cargo clippy --all-targets -- -D warnings` for Rust changes
4. Performance regressions > 5% against the baseline will block merge — benchmark with a release build

---

## Licence

MIT
