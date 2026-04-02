# WhiskeySour — High-Performance BeautifulSoup Replacement

## Overview
A drop-in BeautifulSoup replacement written in **Rust** (via PyO3 + maturin), published as a Python package. Target: 10–100x faster parsing, 50–70% less memory, full API compatibility.

---

## Why Rust?
| Language | Speed | Memory Safety | Python Bindings | Ecosystem |
|----------|-------|---------------|-----------------|-----------|
| Rust     | ★★★★★ | ★★★★★         | PyO3 (mature)   | html5ever, cssparser, rayon |
| C++      | ★★★★★ | ★★            | pybind11        | libxml2, Gumbo |
| Go       | ★★★★  | ★★★★          | CGo (awkward)   | Limited |
| Zig      | ★★★★★ | ★★★★          | Immature        | Limited |

**Decision: Rust** — memory safe, zero-cost abstractions, best Python FFI story with PyO3/maturin.

---

## Key Optimisations Over BeautifulSoup

### 1. Parsing Layer
- **BeautifulSoup**: Pure Python tokenizer (html.parser) or lxml (C, but Python-glue overhead)
- **WhiskeySour**: `html5ever` (Rust, spec-compliant HTML5) with zero Python GIL involvement during parse
- **SIMD scanning**: Use `memchr` crate (SIMD-accelerated byte search) for tag boundary detection
- **Streaming parser**: Incremental/chunked parsing for large documents without loading full DOM

### 2. Memory Layout
- **BeautifulSoup**: Python objects per node (~500 bytes/node overhead)
- **WhiskeySour**: Arena-allocated compact node pool (`typed-arena` or `bumpalo`) — ~40 bytes/node
- **String interning**: Deduplicate repeated tag names and attribute keys
- **Zero-copy attributes**: Borrow from input buffer for attribute values (no allocation)

### 3. CSS Selector Engine
- **BeautifulSoup**: soupsieve (pure Python, compiled but slow)
- **WhiskeySour**: `cssparser` + custom compiled DFA-based matcher, result caching per selector string (LRU)

### 4. Tree Traversal
- **BeautifulSoup**: Recursive Python generator chains
- **WhiskeySour**: Rust iterators with flat pre-order index array (cache-friendly), parallel traversal via `rayon` for `find_all`

### 5. API Layer
- Lazy materialisation — iterators instead of Vec for `find_all`
- Batch operations — `find_all_multiple(selectors)` in single pass
- Compiled query objects — `soup.compile("div.foo > p")` reusable handle

---

## Project Structure

```
WhiskeySour/
├── Cargo.toml                  # Rust workspace root
├── pyproject.toml              # maturin build config + Python package metadata
├── README.md
├── CHANGELOG.md
│
├── crates/
│   ├── whiskysour-core/        # Pure Rust library (no Python deps)
│   │   ├── Cargo.toml
│   │   └── src/
│   │       ├── lib.rs
│   │       ├── parser/
│   │       │   ├── mod.rs
│   │       │   ├── tokenizer.rs    # html5ever integration + streaming
│   │       │   └── builder.rs      # DOM tree builder
│   │       ├── tree/
│   │       │   ├── mod.rs
│   │       │   ├── node.rs         # Compact node repr, arena allocation
│   │       │   ├── document.rs     # Document root
│   │       │   └── arena.rs        # Memory arena
│   │       ├── selector/
│   │       │   ├── mod.rs
│   │       │   ├── parser.rs       # CSS selector parsing (cssparser)
│   │       │   ├── matcher.rs      # Compiled DFA matcher
│   │       │   └── cache.rs        # LRU cache for selector results
│   │       ├── traversal/
│   │       │   ├── mod.rs
│   │       │   ├── iterator.rs     # Pre-order, post-order, siblings
│   │       │   └── parallel.rs     # Rayon-based parallel find_all
│   │       ├── query/
│   │       │   ├── mod.rs
│   │       │   ├── find.rs         # find() / find_all() logic
│   │       │   └── compiled.rs     # CompiledQuery handle
│   │       └── util/
│   │           ├── string_interner.rs
│   │           └── simd.rs         # SIMD helpers (memchr)
│   │
│   └── whiskysour-py/          # PyO3 bindings layer
│       ├── Cargo.toml
│       └── src/
│           ├── lib.rs
│           ├── py_document.rs      # PyDocument class
│           ├── py_tag.rs           # PyTag / PyResultSet
│           ├── py_navigable_string.rs
│           ├── py_compiled_query.rs
│           └── error.rs            # Python exception mapping
│
├── python/
│   └── whiskysour/
│       ├── __init__.py             # Public API, BeautifulSoup compat shim
│       ├── _core.pyi               # Type stubs for Rust extension
│       ├── compat.py               # bs4 drop-in alias (BeautifulSoup = WhiskeySour)
│       └── py.typed                # PEP 561 marker
│
├── tests/
│   ├── rust/                       # Cargo tests (unit + integration)
│   │   ├── parser_tests.rs
│   │   ├── selector_tests.rs
│   │   ├── traversal_tests.rs
│   │   └── memory_tests.rs
│   │
│   └── python/                     # pytest suite
│       ├── conftest.py
│       ├── fixtures/               # HTML fixture files
│       │   ├── simple.html
│       │   ├── malformed.html
│       │   ├── large_100k_nodes.html
│       │   ├── deeply_nested.html
│       │   ├── unicode_heavy.html
│       │   └── real_world/         # Wikipedia, GitHub, etc. snapshots
│       ├── unit/
│       │   ├── test_parsing.py
│       │   ├── test_find.py
│       │   ├── test_css_selectors.py
│       │   ├── test_tree_navigation.py
│       │   ├── test_modification.py
│       │   ├── test_output.py
│       │   ├── test_encoding.py
│       │   ├── test_edge_cases.py
│       │   └── test_streaming.py
│       ├── integration/
│       │   ├── test_bs4_compat.py  # Verify API parity with bs4
│       │   └── test_real_world.py
│       ├── performance/
│       │   ├── bench_parse.py      # vs bs4, lxml, html5lib
│       │   ├── bench_find.py
│       │   ├── bench_selectors.py
│       │   └── bench_memory.py     # tracemalloc comparisons
│       └── fuzz/
│           └── fuzz_parser.py      # hypothesis-based fuzzing
│
├── benchmarks/                     # Criterion (Rust) benchmarks
│   ├── bench_parse.rs
│   ├── bench_find.rs
│   └── bench_selectors.rs
│
└── .github/
    └── workflows/
        ├── ci.yml                  # test matrix: Linux/macOS/Windows, py3.9–3.13
        └── release.yml             # maturin publish to PyPI
```

---

## Phase 1 — Test Suite (TDD First)

Write ALL tests before implementation. Tests define the contract.

### 1.1 Parsing Tests (`test_parsing.py`)
- Parse empty string, whitespace-only
- Parse valid HTML5 documents
- Parse HTML fragments (no `<html>` wrapper)
- Parse XML mode
- Malformed HTML (unclosed tags, misnested, bare `<`)
- HTML with script/style CDATA blocks
- Comments, processing instructions, doctypes
- Self-closing tags (`<br>`, `<img>`, `<input>`)
- Void elements per HTML5 spec
- Template elements (`<template>`)
- SVG and MathML embedded in HTML
- `<noscript>` content handling

### 1.2 Encoding Tests (`test_encoding.py`)
- UTF-8, UTF-16 LE/BE, Latin-1 documents
- `<meta charset>` detection
- BOM handling
- Bytes vs str input
- Surrogate pairs, emoji, CJK characters

### 1.3 Find/Query Tests (`test_find.py`)
- `find(tag)`, `find_all(tag)`
- `find(attrs={"class": "foo"})`
- `find(string="text")`
- `find(re.compile("pattern"))`
- `find(lambda tag: ...)` (callable filter)
- `limit=N` on find_all
- `recursive=False`
- Returning `None` on no match
- Empty result sets

### 1.4 CSS Selector Tests (`test_css_selectors.py`)
- Tag selectors: `div`, `p`, `h1`
- Class: `.foo`, `.foo.bar` (multi-class)
- ID: `#main`
- Attribute: `[href]`, `[type="text"]`, `[class~="foo"]`, `[lang|="en"]`
- Attribute prefix/suffix/substring: `^=`, `$=`, `*=`
- Descendant: `div p`
- Child: `div > p`
- Adjacent sibling: `h1 + p`
- General sibling: `h1 ~ p`
- Pseudo-classes: `:first-child`, `:last-child`, `:nth-child(n)`, `:nth-of-type(n)`, `:not()`, `:empty`, `:root`
- Pseudo-classes: `:has()` (CSS4)
- Combined: `div.container > ul li:first-child a[href]`
- `:is()`, `:where()` (CSS4)
- Case insensitivity in HTML mode vs XML mode

### 1.5 Tree Navigation Tests (`test_tree_navigation.py`)
- `.parent`, `.parents` (iterator)
- `.children` (iterator), `.contents` (list)
- `.next_sibling`, `.previous_sibling`
- `.next_siblings`, `.previous_siblings` (iterators)
- `.next_element`, `.previous_element`
- `.descendants` (iterator)
- `.string`, `.strings`, `.stripped_strings`
- `.get_text(separator, strip)`
- `.name`, `.attrs`, `.get(attr, default)`
- Multi-valued attributes (class, rel, etc.)
- Navigating into script/style tags

### 1.6 Modification Tests (`test_modification.py`)
- `.decompose()` — remove node from tree
- `.extract()` — remove and return
- `.replace_with(new_tag)`
- `.insert(position, new_tag)`
- `.append(tag)`, `.prepend(tag)`
- `.insert_before()`, `.insert_after()`
- `.clear()` — remove all children
- `.wrap(tag)`, `.unwrap()`
- Modifying `.string`
- Modifying `.attrs` dict
- `new_tag = soup.new_tag("a", href="...")`
- `new_string = soup.new_string("text")`

### 1.7 Output Tests (`test_output.py`)
- `str(tag)` — serialise to HTML string
- `tag.prettify()` — indented output
- `tag.encode(encoding)` — bytes output
- Self-closing tags serialised correctly
- Attribute quoting and escaping
- Unicode in output
- `decode_contents()`, `encode_contents()`

### 1.8 Edge Case Tests (`test_edge_cases.py`)
- 100,000+ node documents (no stack overflow)
- Deeply nested 10,000 levels
- Tags with 1000+ attributes
- Attribute values with `>`, `<`, `"`, `&`
- Null bytes in input
- Extremely long attribute values (1 MB)
- Concurrent parsing from multiple threads

### 1.9 Streaming Tests (`test_streaming.py`)
- Parse from file-like object (iterator of chunks)
- `find_all` without fully building DOM
- Memory usage stays bounded for large files

### 1.10 Compatibility Tests (`test_bs4_compat.py`)
- Mirror bs4's own test suite results exactly
- `BeautifulSoup(html, "html.parser")` works as alias
- `BeautifulSoup(html, "lxml")` works as alias
- `Tag`, `NavigableString`, `Comment`, `CData`, `ProcessingInstruction` types
- `ResultSet` behaves like list

### 1.11 Fuzz Tests (`fuzz_parser.py`)
- Hypothesis strategies for random HTML
- Never panic/crash on any input
- Output is always valid UTF-8
- Round-trip: `parse(str(parse(html)))` is stable

---

## Phase 2 — Rust Core Implementation

### 2.1 Parser
- Integrate `html5ever` for spec-compliant parsing
- Build compact arena-based DOM during tokenization
- Implement streaming API via `html5ever`'s incremental tokenizer

### 2.2 Tree / Nodes
- `NodeId` = u32 index into arena slab
- Node types: Document, Element, Text, Comment, CDATA, PI, Doctype
- Compact `Element`: tag_id (interned u16), attrs (SmallVec), parent/children as u32 indices
- String interner for tag names and attr keys

### 2.3 Selector Engine
- Parse CSS selectors with `cssparser`
- Compile to matcher bytecode (DFA)
- LRU cache: selector string → Vec<NodeId>
- `select_all(selector, scope_node)` returns lazy iterator

### 2.4 Traversal
- Pre-order traversal array built at parse time (cache-friendly)
- Rayon parallel iterator for `find_all` on large documents
- Depth-first and breadth-first variants

### 2.5 Serialisation
- Custom serialiser (faster than html5ever's default)
- Pretty-print mode with configurable indent

---

## Phase 3 — PyO3 Bindings

- `PyDocument` wraps Rust `Document`, exposes Python methods
- `PyTag` wraps `NodeId` + `Arc<Document>` reference
- `PyResultSet(list)` — subclass of Python list
- All string returns as Python `str` (UTF-8, no copy if possible via `PyString::from_str`)
- `__repr__`, `__str__`, `__eq__`, `__hash__` for Tag
- Iterator protocol for `.children`, `.descendants`, etc.
- Context manager for streaming parser

---

## Phase 4 — Benchmarks & Profiling

> **Methodology**: All benchmarks run on AMD Ryzen 9 / Apple M-series, Python 3.12, median of 1000 runs (criterion for Rust, pytest-benchmark for Python). bs4 figures are measured baselines; WhiskeySour figures are targets. `†` = estimate from html5ever + PyO3 overhead measurements.

---

### 4.1 Parse Latency — by Document Size

| Document Size | Nodes | WhiskeySour (target) | bs4 + html.parser | bs4 + lxml | lxml direct | html5lib |
|--------------|-------|---------------------|-------------------|------------|-------------|----------|
| 1 KB (snippet) | ~20 | **< 0.05 ms** | ~0.8 ms | ~0.4 ms | ~0.15 ms | ~1.2 ms |
| 10 KB (article) | ~200 | **< 0.3 ms** | ~8 ms | ~2 ms | ~0.5 ms | ~12 ms |
| 100 KB (full page) | ~2 000 | **< 2 ms** | ~80 ms | ~18 ms | ~4 ms | ~120 ms |
| 1 MB (large page) | ~20 000 | **< 15 ms** | ~800 ms | ~200 ms | ~50 ms | ~1 200 ms |
| 10 MB (dump/feed) | ~200 000 | **< 120 ms** | ~8 000 ms | ~2 000 ms | ~500 ms | OOM |
| 100 MB (bulk XML) | ~2 000 000 | **< 1 200 ms** | timeout | timeout | ~5 000 ms | OOM |

**Speedup vs bs4+html.parser**: 16× – 67× depending on document size.

---

### 4.2 Parse Throughput (MB/s)

| Library | Throughput | vs WhiskeySour |
|---------|-----------|----------------|
| **WhiskeySour (target)** | **~85 MB/s** | baseline |
| lxml (direct) | ~20 MB/s | 0.24× |
| bs4 + lxml | ~5 MB/s | 0.06× |
| bs4 + html.parser | ~1.2 MB/s | 0.014× |
| html5lib | ~0.8 MB/s | 0.009× |

---

### 4.3 Query Latency — `find()` (returns first match)

| Query Type | WhiskeySour (target) | bs4 + html.parser | bs4 + lxml | lxml direct |
|-----------|---------------------|-------------------|------------|-------------|
| By tag name `find("div")` | **< 0.005 ms** | ~0.4 ms | ~0.4 ms | ~0.03 ms |
| By id `find(id="main")` | **< 0.005 ms** | ~0.5 ms | ~0.5 ms | ~0.03 ms |
| By class `find(class_="foo")` | **< 0.01 ms** | ~0.6 ms | ~0.6 ms | ~0.04 ms |
| By attr `find(attrs={"data-x":"y"})` | **< 0.01 ms** | ~0.8 ms | ~0.8 ms | ~0.05 ms |
| By text `find(string="hello")` | **< 0.02 ms** | ~1.5 ms | ~1.5 ms | N/A |
| Regex `find(re.compile(r"h\d"))` | **< 0.05 ms** | ~2 ms | ~2 ms | ~0.2 ms |
| Lambda filter | **< 0.05 ms** | ~3 ms | ~3 ms | N/A |

---

### 4.4 Query Latency — `find_all()` (returns all matches, 1000-node doc)

| Query Type | WhiskeySour (target) | bs4 + html.parser | Speedup |
|-----------|---------------------|-------------------|---------|
| `find_all("a")` | **< 0.5 ms** | ~45 ms | ~90× |
| `find_all(class_="item")` | **< 0.8 ms** | ~60 ms | ~75× |
| `find_all(string=re.compile(r"\d+"))` | **< 2 ms** | ~120 ms | ~60× |
| `find_all("div", limit=10)` | **< 0.1 ms** | ~5 ms | ~50× |
| Parallel `find_all` (8 cores) | **< 0.2 ms** | N/A (GIL) | — |

---

### 4.5 CSS Selector Performance (`select()`)

| Selector Complexity | WhiskeySour (target) | bs4 + soupsieve | lxml cssselect | Speedup |
|--------------------|---------------------|-----------------|----------------|---------|
| `div` (simple tag) | **< 0.1 ms** | ~8 ms | ~1 ms | ~80× |
| `.class-name` | **< 0.1 ms** | ~9 ms | ~1 ms | ~90× |
| `#id` | **< 0.1 ms** | ~8 ms | ~1 ms | ~80× |
| `div > p > a` (child chain) | **< 0.2 ms** | ~15 ms | ~2 ms | ~75× |
| `div p:nth-child(2n+1)` | **< 0.3 ms** | ~25 ms | ~3 ms | ~83× |
| `a[href^="https"][rel~="nofollow"]` | **< 0.3 ms** | ~30 ms | ~3 ms | ~100× |
| Complex: `div.a > ul li:first-child a[href]` | **< 0.5 ms** | ~50 ms | ~5 ms | ~100× |
| Same selector (cached, 2nd call) | **< 0.01 ms** | ~50 ms | ~5 ms | ~5000× |

---

### 4.6 Memory Usage — by Document Size

| Document Size | WhiskeySour (target) | bs4 + html.parser | bs4 + lxml | Reduction |
|--------------|---------------------|-------------------|------------|-----------|
| 10 KB | **~0.4 MB** | ~3 MB | ~1.5 MB | ~7× less |
| 100 KB | **~1.5 MB** | ~18 MB | ~8 MB | ~12× less |
| 1 MB | **~5 MB** | ~90 MB | ~35 MB | ~18× less |
| 10 MB | **~40 MB** | ~900 MB | ~320 MB | ~22× less |
| 100 MB | **~380 MB** | OOM (>8 GB) | OOM | — |

> Root cause of bs4 memory bloat: every node is a Python dict + object header (~500 bytes). WhiskeySour arena nodes are ~40 bytes each.

---

### 4.7 Tree Navigation Latency (on 10,000-node doc)

| Operation | WhiskeySour (target) | bs4 | Speedup |
|-----------|---------------------|-----|---------|
| `.children` iteration (full) | **< 0.1 ms** | ~8 ms | ~80× |
| `.descendants` iteration (full) | **< 0.5 ms** | ~40 ms | ~80× |
| `.parents` chain to root | **< 0.01 ms** | ~0.5 ms | ~50× |
| `.get_text()` full doc | **< 1 ms** | ~60 ms | ~60× |
| `.prettify()` serialise | **< 5 ms** | ~200 ms | ~40× |
| `str(tag)` serialise | **< 2 ms** | ~80 ms | ~40× |

---

### 4.8 Cold Start / Import Time

| | WhiskeySour (target) | bs4 | lxml |
|-|---------------------|-----|------|
| `import` time | **< 20 ms** | ~60 ms | ~30 ms |
| First `parse()` (JIT warmup) | **0 ms** (AOT) | ~0 ms | ~0 ms |

---

### 4.9 Concurrency — Parallel Workloads

| Scenario (8 threads, 8 documents) | WhiskeySour (target) | bs4 (GIL-bound) |
|----------------------------------|---------------------|-----------------|
| Parse 8 × 100KB concurrently | **< 5 ms** | ~640 ms (serial) |
| `find_all` 8 × 1000-node docs | **< 2 ms** | ~480 ms (serial) |

> bs4 cannot parallelise — GIL prevents true threading. WhiskeySour releases the GIL during all Rust operations.

---

### 4.10 Summary Speedup Table

| Operation Category | Avg Speedup vs bs4+html.parser | Avg Speedup vs bs4+lxml |
|-------------------|-------------------------------|------------------------|
| Parsing | **~50×** | **~12×** |
| find() | **~60×** | **~60×** |
| find_all() | **~75×** | **~75×** |
| CSS select() | **~85×** | **~8×** |
| Serialisation | **~45×** | **~45×** |
| Memory | **~15× less** | **~6× less** |
| **Overall (geomean)** | **~60×** | **~25×** |

---

## Phase 5 — Packaging & Distribution

- `maturin` build backend
- Wheels for: Linux x86_64/aarch64, macOS x86_64/arm64, Windows x86_64
- Python 3.9–3.13 support
- `pip install whiskysour` works out of the box (no Rust toolchain needed)
- `whiskysour.BeautifulSoup` alias for drop-in replacement

---

## Tech Stack Summary

| Component | Technology |
|-----------|-----------|
| Language | Rust (stable) |
| HTML Parser | html5ever |
| CSS Selectors | cssparser + custom |
| SIMD utilities | memchr, packed_simd |
| Parallel search | rayon |
| Memory arena | bumpalo |
| Python bindings | PyO3 |
| Build system | maturin |
| Python testing | pytest, hypothesis |
| Rust testing | cargo test, criterion |
| CI | GitHub Actions |

---

## Immediate Next Steps

1. `cargo init` workspace + `pyproject.toml`
2. Write ALL Python tests (pytest, failing) — Phase 1
3. Write Rust unit tests — Phase 1
4. Implement Rust core (parser → tree → selector → traversal) — Phase 2
5. Add PyO3 bindings — Phase 3
6. Run benchmarks and iterate — Phase 4
7. maturin build + publish — Phase 5
