# AGENTS.md — WhiskeySour

Instructions for AI coding agents (Codex, Devin, Copilot Workspace, etc.) working in this repository.

---

## What this project is

WhiskeySour is a Rust-backed, drop-in replacement for Python's BeautifulSoup library. It exposes an identical public API to BS4 but parses HTML 7–11× faster, queries 7–14× faster, serialises 43–50× faster, and uses 12× less memory per node.

The project is in **Beta**. The core implementation is complete and 450 unit tests pass.

---

## Build system

This is a **Rust + Python hybrid project** built with [maturin](https://www.maturin.rs). You cannot run the Python tests without first compiling the Rust extension.

### Prerequisites
- Python 3.9+, virtual environment at `.venv/`
- Rust toolchain installed at `~/.cargo/bin/`

### Build the extension

```bash
source .venv/bin/activate
PATH="$HOME/.cargo/bin:$PATH" maturin develop          # dev build (default for tests)
PATH="$HOME/.cargo/bin:$PATH" maturin develop --release # release build (required for benchmarks)
```

### Type-check Rust without building

```bash
~/.cargo/bin/cargo check -p whiskysour-py
```

### Lint and format

```bash
~/.cargo/bin/cargo fmt                        # format Rust
~/.cargo/bin/cargo clippy -- -D warnings      # lint Rust; warnings are errors
```

---

## Running tests

Always activate the venv first. Pass `--override-ini="addopts="` to suppress benchmark-only defaults.

```bash
# Unit tests (primary suite — must all pass before any PR)
source .venv/bin/activate
.venv/bin/pytest tests/python/unit/ --override-ini="addopts=" -q

# Skip slow tests (large documents / deep nesting)
.venv/bin/pytest tests/python/unit/ --override-ini="addopts=" -q -m "not slow"

# Integration tests (BS4 API parity — requires beautifulsoup4)
.venv/bin/pytest tests/python/integration/ --override-ini="addopts=" -q

# Fuzz / property-based tests (requires hypothesis)
.venv/bin/pytest tests/python/fuzz/ --override-ini="addopts=" -v

# Check compatibility against real BS4 (no file changes needed)
PYTHONPATH=/tmp .venv/bin/pytest tests/python/unit/ tests/python/integration/ \
  --override-ini="addopts=" -q --tb=no
```

### Test pass-rate contract

| Suite | Minimum pass rate |
|-------|:-----------------:|
| `unit/` | **450 / 451** (1 skipped is expected) |
| `integration/` | **58 / 58** |

Do not submit changes that reduce these numbers.

---

## Project structure

```
WhiskeySour/
├── Cargo.toml                      # Rust workspace (two crates)
├── pyproject.toml                  # maturin config; Python package = whiskysour
├── pytest.ini
│
├── crates/
│   ├── whiskysour-core/            # Pure Rust library — no Python dependency
│   │   └── src/
│   │       ├── node.rs             # NodeId (u32), NodeData enum, Attr, arena types
│   │       ├── document.rs         # Document: flat Vec<Node> arena
│   │       ├── parser/             # html5ever TreeSink integration
│   │       ├── selector/           # CSS DFA + LRU cache
│   │       ├── traversal/          # Iterator types for tree walks
│   │       ├── query/              # find / find_all / select
│   │       └── serialize/          # HTML output + prettify
│   │
│   └── whiskysour-py/
│       └── src/lib.rs              # PyO3 bindings: _Tag, _Document Python classes
│
├── python/whiskysour/
│   ├── __init__.py                 # Full BS4-compatible Python API
│   └── _core.pyi                  # Type stubs
│
└── tests/python/
    ├── conftest.py                 # Shared fixtures (parse, parse_fragment, html_doc)
    ├── unit/                       # 450 tests across 10 files
    ├── integration/                # 58 BS4 compatibility tests
    ├── performance/                # Benchmarks + bench_comparison.py
    └── fuzz/                       # Hypothesis property tests
```

---

## Architecture: three layers

These layers must remain separate. Do not merge their responsibilities.

### Layer 1 — `whiskysour-core` (pure Rust)
The tree is a flat `Vec<Node>` arena. A `NodeId` is a `u32` index — no pointers, no `Rc`, no `Box`. Each `Node` carries a `NodeData` enum variant: `Document`, `Element`, `Text`, `Comment`, `CData`, `ProcessingInstruction`, or `Doctype`.

Element attributes use `SmallVec<[Attr; 4]>` — zero heap allocation for elements with ≤4 attrs (which covers the overwhelming majority of real HTML).

This crate has no PyO3 dependency and must stay that way. It can be used as a standalone Rust library.

### Layer 2 — `whiskysour-py/src/lib.rs`
PyO3 glue code only. `PyTag` = `Arc<RwLock<Document>>` + `NodeId`. Cloning is cheap. All non-trivial Rust work releases the Python GIL with `py.allow_threads(...)`.

### Layer 3 — `python/whiskysour/__init__.py`
The BeautifulSoup-compatible Python shim. Key components:
- `Tag` — wraps `_Tag` (a `PyTag`), exposes the full BS4 API.
- `NavigableString` — a `str` subclass with `name = None` (class attribute) and navigation properties.
- `_AttrProxy` — a `dict` subclass that syncs mutations back to Rust. **Lazy**: only created when `.attrs` is accessed.
- `_wrap(rust_obj)` — dispatches `node_type` to `Tag`, `NavigableString`, `Comment`, etc.
- `_python_filter` / `_needs_python_filter` — handles regex / callable / list filters in Python for filters Rust cannot express.
- `WhiskeySour` / `BeautifulSoup` — the top-level constructors (same class, two names).

---

## Critical performance rules

Violating these causes measurable regressions. A >5% regression against the baseline blocks merge.

### 1. Never call `.attrs` on a hot read path
`Tag.attrs` builds a full Python `dict` from the Rust attribute list (~1µs). For attribute reads, use `Tag.get(key)` (which calls `get_coerced` in Rust — a direct O(n_attrs) scan with no dict allocation) or `Tag.has_attr(key)`.

### 2. Preserve the Rust fast path in `find_all`
`_needs_python_filter` returns `False` when all filters are plain strings or booleans. In this case `find_all` delegates everything to Rust. Any Python-side work added to this code path will destroy the 7–14× speedups.

### 3. Keep `_MULTI_VALUED_ATTRS` in sync
The set in `__init__.py` and the `MULTI` slice in `lib.rs/get_coerced` must list the same attributes. If you add one, add it to both.

### 4. Use release builds for benchmarks
Dev builds (`maturin develop`) are 2–3× slower than release builds. Never quote benchmark numbers from a dev build.

---

## PyO3 version 0.22 API

This project uses PyO3 0.22. The old `&PyAny` API is gone.

```rust
// ✓ Correct 0.22 patterns
fn method<'py>(&self, py: Python<'py>, val: &Bound<'py, PyAny>) -> PyResult<PyObject>
PyBytes::new_bound(py, &bytes)
PyList::new_bound(py, &items)
PyDict::new_bound(py)

// ✗ Pre-0.22 (will not compile)
fn method(&self, py: Python, val: &PyAny) -> PyResult<PyObject>
PyBytes::new(py, &bytes)
```

`node_type` values: `"element"` | `"text"` | `"comment"` | `"cdata"` | `"doctype"` | `"document"`.

---

## BeautifulSoup compatibility

### Things that must remain compatible
- `tag.name` → `str` for elements, `None` for text/comment nodes.
- `tag.attrs` → `dict`-like object; mutations must propagate to Rust.
- `find()`, `find_all()`, `select()`, `select_one()` signatures must match BS4.
- `get_text(separator="", strip=False)` must match BS4 semantics.
- `prettify(indent_width=2)` and `prettify(indent=N)` (WS extension alias).
- `NavigableString.name is None` — this is load-bearing for the `if child.name:` idiom.
- `del tag["missing_attr"]` → silent no-op (not `KeyError`).

### Deliberate differences — do not "fix" these

These are intentional WS decisions. Filing a bug to make WS match BS4 on these points will be rejected.

| Topic | WS behaviour | BS4 behaviour |
|-------|-------------|---------------|
| `find_all(class_=["a","b"])` | AND (element must have all classes) | OR |
| `insert(pos, child)` position | Counts element children only | Counts all child nodes |
| `new_tag(class_="x")` | Stores attr as `class` | Stores attr as `class_` |
| Attribute order in output | Source / insertion order | Alphabetical |
| `</br>` | Creates 2 `<br>` (HTML5) | Creates 1 `<br>` |
| Null byte `\x00` | Stripped (HTML5) | Passed through |
| Duplicate attributes | First wins (HTML5) | Last wins |

### html5ever spec compliance
WS uses html5ever (the Firefox/Chrome HTML parser). It is more spec-correct than BS4's html.parser. Some BS4 tests for malformed HTML will correctly fail against WS — this is expected.

---

## Testing rules

1. **All code changes require tests.** Bug fixes must include a regression test. New features require unit tests for the happy path and at least one edge case.

2. **Use the `parse` fixture** from `conftest.py`, not `WhiskeySour(...)` directly. The fixture enables running the test suite against the BS4 shim for compatibility checking.

3. **Do not use `hasattr(node, "name")`.** Use `node.name is not None`. Since `NavigableString.name = None` is a class attribute, `hasattr` returns `True` for all nodes — the test would never filter out text nodes.

4. **Mark slow tests.** Tests that parse documents with 10k+ nodes or use `@pytest.mark.slow`.

5. **Test file ownership** — add tests to the correct file:
   - Parsing behaviour → `test_parsing.py`
   - find / find_all filters → `test_find.py`
   - CSS selectors → `test_css_selectors.py`
   - Navigation (parent/children/siblings) → `test_tree_navigation.py`
   - DOM mutation → `test_modification.py`
   - Output / serialisation → `test_output.py`
   - Encoding → `test_encoding.py`
   - Streaming → `test_streaming.py`
   - BS4 parity → `integration/test_bs4_compat.py`

---

## Rust code rules

- `cargo fmt` and `cargo clippy -- -D warnings` must pass. Clippy warnings are CI errors.
- `whiskysour-core` must have **zero PyO3 imports**. It is a pure Rust library.
- Do not use `unwrap()` in production code. Use `?` or explicit `match`. `unwrap()` is permitted in tests and `#[cfg(test)]` blocks.
- `NodeId` is `u32`. Do not widen it.
- `unsafe` requires a `// SAFETY:` comment that explains why the block is sound and why a safe alternative is not available.
- Do not introduce `Rc` or `RefCell` anywhere in the arena code. The tree is shared via `Arc<RwLock<Document>>`.

---

## Pull request checklist

Before marking a PR ready for review:

- [ ] `cargo fmt` passes (no diff)
- [ ] `cargo clippy -- -D warnings` passes
- [ ] `pytest tests/python/unit/ --override-ini="addopts=" -q` — 450+ passing
- [ ] `pytest tests/python/integration/ --override-ini="addopts=" -q` — 58 passing
- [ ] No performance regression >5% (run `bench_comparison.py` if touching hot paths)
- [ ] New tests added for every changed behaviour
- [ ] No `hasattr(node, "name")` patterns introduced
- [ ] `_MULTI_VALUED_ATTRS` in `__init__.py` and `MULTI` in `lib.rs/get_coerced` are in sync if either was changed
