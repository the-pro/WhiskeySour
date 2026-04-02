# CLAUDE.md — WhiskeySour

Instructions for Claude Code working in this repository. These override default behaviour.

---

## Project overview

WhiskeySour is a high-performance, drop-in replacement for Python's BeautifulSoup, written in Rust and exposed to Python via PyO3 + maturin. The goal is identical API surface to BS4 with 7–50× faster operations and 12× lower memory per node.

**Status:** Beta. 450 unit tests passing, 508 including integration tests.

---

## Essential commands

Always activate the venv first. Cargo binaries live in `~/.cargo/bin/`.

```bash
# Activate environment (required before any python/maturin command)
source .venv/bin/activate

# Build Rust extension (dev — fast compile, debug symbols)
PATH="$HOME/.cargo/bin:$PATH" maturin develop

# Build release (use this before running benchmarks)
PATH="$HOME/.cargo/bin:$PATH" maturin develop --release

# Rust type-check without building (fast)
~/.cargo/bin/cargo check -p whiskysour-py

# Run unit tests (primary test suite)
source .venv/bin/activate && .venv/bin/pytest tests/python/unit/ --override-ini="addopts=" -q

# Run integration tests (BS4 API parity)
source .venv/bin/activate && .venv/bin/pytest tests/python/integration/ --override-ini="addopts=" -q

# Skip slow tests
.venv/bin/pytest tests/python/unit/ --override-ini="addopts=" -q -m "not slow"

# Run against BS4 shim (compatibility check — no file changes)
PYTHONPATH=/tmp .venv/bin/pytest tests/python/unit/ tests/python/integration/ --override-ini="addopts=" -q --tb=no

# Performance comparison report
python tests/python/performance/bench_comparison.py

# Rust formatting and linting
~/.cargo/bin/cargo fmt
~/.cargo/bin/cargo clippy -- -D warnings
```

---

## Repository structure

```
WhiskeySour/
├── Cargo.toml                      # Workspace root (two crates)
├── pyproject.toml                  # maturin build config; module = whiskysour._core
│
├── crates/
│   ├── whiskysour-core/            # Pure Rust library (no Python dependency)
│   │   └── src/
│   │       ├── node.rs             # Arena-allocated DOM: NodeId (u32), NodeData enum
│   │       ├── document.rs         # Document struct: flat Vec<Node> arena
│   │       ├── parser/             # html5ever TreeSink integration
│   │       ├── selector/           # CSS selector DFA + LRU cache (cssparser)
│   │       ├── traversal/          # Tree iterators (ancestors, descendants, siblings)
│   │       ├── query/              # find() / find_all() / select() logic
│   │       └── serialize/          # HTML serialisation + prettify
│   │
│   └── whiskysour-py/
│       └── src/lib.rs              # All PyO3 bindings: PyTag (_Tag) + PyDocument (_Document)
│
├── python/whiskysour/
│   ├── __init__.py                 # Public Python API: Tag, NavigableString, WhiskeySour, etc.
│   └── _core.pyi                  # Type stubs for the Rust extension
│
└── tests/python/
    ├── conftest.py                 # parse / parse_fragment / html_doc fixtures
    ├── unit/                       # 450 tests across 10 files
    ├── integration/                # 58 BS4 API parity tests
    ├── performance/                # pytest-benchmark suites + bench_comparison.py
    └── fuzz/                       # Hypothesis property tests
```

---

## Architecture: the two-layer design

**Never collapse these layers.** The split is intentional and load-bearing.

### Layer 1 — `whiskysour-core` (pure Rust)
- Zero Python dependency. Can be used as a standalone Rust crate.
- `Document` = flat `Vec<Node>` arena; `NodeId` = `u32` index. No heap allocation per node beyond the arena itself.
- `NodeData` is an enum: `Document | Element { name, attrs, self_closing, is_template } | Text | Comment | CData | ProcessingInstruction | Doctype`.
- `attrs` on elements: `SmallVec<[Attr; 4]>` — avoids heap alloc for elements with ≤4 attributes.
- The tree is owned by `Document`, shared across threads via `Arc<RwLock<Document>>`.

### Layer 2 — `whiskysour-py` (`crates/whiskysour-py/src/lib.rs`)
- PyO3 bindings only. No parsing or query logic here.
- `PyTag` holds `Arc<RwLock<Document>>` + `NodeId`. Cloning is cheap (Arc clone).
- `PyDocument` is the document root wrapper.
- All Rust tree operations release the GIL (`py.allow_threads(...)`) so concurrent Python threads can parse simultaneously.

### Layer 3 — `python/whiskysour/__init__.py`
- BeautifulSoup-compatible Python shim.
- `_wrap(rust_obj)` dispatches `node_type` → `Tag | NavigableString | Comment | ...`
- `_AttrProxy` — a `dict` subclass that syncs mutations back to Rust. **Created lazily** (only when `.attrs` is accessed); never create it on hot read paths.
- `_python_filter` — handles regex/callable/list filters that cannot be expressed in Rust's type system.

---

## Performance rules — read before touching hot paths

These are non-negotiable. A >5% regression against the baseline blocks merge.

1. **Never call `self._rust.attrs` (full dict build) on a hot path.** Use `self._rust.get_coerced(key)` instead — it scans attrs directly in Rust and returns `Option<PyObject>` with no dict allocation. This is why `Tag.get()` and `Tag.__getitem__()` use `get_coerced`.

2. **`_AttrProxy` costs ~1µs to construct** (Python dict allocation + Rust attr copy). It exists for mutation support only. Query-path code must not touch `.attrs`.

3. **The fast path in `find_all`** delegates everything to Rust when there are no regex/callable/list filters (`_needs_python_filter` returns False). Preserve this: adding Python-side work to the fast path breaks the 7–14× speedups.

4. **GIL release.** Tree traversals in `lib.rs` must use `py.allow_threads(...)` for any non-trivial Rust work. Parsing always releases the GIL.

5. **`SmallVec<[Attr; 4]>` on element attrs** avoids a heap allocation for the common case of ≤4 attributes. Do not change this to `Vec` without a benchmark justifying it.

6. **Selector LRU cache** in `whiskysour-core/selector/` caches compiled DFAs. Do not clear it eagerly or add locks that serialise selector access across threads.

7. **Run benchmarks with `--release` builds** (`maturin develop --release`) before reporting numbers. Dev builds are 2–3× slower.

---

## PyO3 patterns (version 0.22)

Use these exact patterns. The old `&PyAny` / `PyBytes::new` API is removed.

```rust
// ✓ Correct
fn my_method<'py>(&self, py: Python<'py>, arg: &Bound<'py, PyAny>) -> PyResult<PyObject> { ... }
PyBytes::new_bound(py, &bytes)
PyList::new_bound(py, &items)

// ✗ Wrong (pre-0.22 API — will not compile)
fn my_method(&self, py: Python, arg: &PyAny) -> PyResult<PyObject> { ... }
PyBytes::new(py, &bytes)
```

`#[pymodule]` signature:
```rust
#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> { ... }
```

`node_type` string values returned by `PyTag.node_type`: `"element"` | `"text"` | `"comment"` | `"cdata"` | `"doctype"` | `"document"`. Use these for dispatch in `_wrap()`.

---

## BS4 compatibility rules

WhiskeySour aims to be a faithful drop-in. When touching the Python shim:

- **`NavigableString.name = None`** — class attribute, not instance attribute. Code that does `if child.name:` must see `None` (falsy) for text nodes and a string for element nodes. Do not remove this.
- **`class_` → `class` stripping** — trailing underscores on attribute kwargs are stripped in `_python_filter` (line: `real_name = attr_name[:-1] if attr_name.endswith("_") else attr_name`). This must happen before any attr lookup.
- **Multi-valued attributes** — `class`, `rel`, `rev`, `accept-charset`, `headers`, `accesskey` are returned as `list[str]`, not `str`. The set is `_MULTI_VALUED_ATTRS`. `get_coerced` in Rust applies this coercion directly.
- **`del tag["missing"]`** is a silent no-op (matches BS4). `Tag.__delitem__` checks `has_attr` before deleting.
- **`Tag.string`** sets `_parent_ref = self` on the returned `NavigableString` so that `s.parent is tag` holds.

### Deliberate differences from BS4 (do not "fix" these)

| Behaviour | WhiskeySour | BeautifulSoup | Reason |
|-----------|-------------|---------------|--------|
| `find_all(class_=["a","b"])` | AND semantics | OR semantics | More useful / CSS-consistent |
| `insert(pos, child)` | Counts element children only | Counts all nodes | Cleaner mental model |
| `new_tag(class_="x")` | Stores as `class` | Stores as `class_` literally | WS behaviour is more correct |
| Attribute order in `str()` | Insertion order | Alphabetical | Faster + more predictable |
| `</br>` in source | 2 `<br>` (HTML5 spec) | 1 `<br>` | html5ever is spec-compliant |
| Null bytes `\x00` | Stripped (HTML5 spec) | Passed through | html5ever is spec-compliant |
| Duplicate attributes | First wins (HTML5 spec) | Last wins | html5ever is spec-compliant |

---

## Testing rules

1. **Every code change must ship with tests.** Bug fixes get a regression test. New features get unit tests covering the happy path and at least one edge case.

2. **Test file ownership:**
   - `test_parsing.py` — HTML5 parsing, fragments, void elements, encoding
   - `test_find.py` — find / find_all / filter types
   - `test_css_selectors.py` — CSS3 selectors, pseudo-classes
   - `test_tree_navigation.py` — parent / children / siblings / descendants
   - `test_modification.py` — decompose / extract / replace_with / insert / append / wrap
   - `test_output.py` — str() / prettify() / encode() / round-trip
   - `test_encoding.py` — UTF-8/16, BOM, meta charset
   - `test_edge_cases.py` — 10k+ nodes, deep nesting, concurrency, control chars
   - `test_streaming.py` — StreamParser, parse_stream()
   - `integration/test_bs4_compat.py` — cross-library parity

3. **Use the `parse` fixture, not `WhiskeySour(...)` directly.** This lets the test suite run against the BS4 shim for compatibility checking.

4. **Mark slow tests** (large documents, deep nesting) with `@pytest.mark.slow`.

5. **Do not add `hasattr(node, "name")` checks.** Use `node.name is not None` — `NavigableString.name = None` makes `hasattr` return `True` for all nodes.

6. **The baseline pass rate is 450/451 unit tests.** Do not submit changes that reduce this.

---

## Rust code rules

- Run `cargo fmt` and `cargo clippy -- -D warnings` before committing Rust changes. Clippy warnings are errors.
- Keep `whiskysour-core` free of any PyO3 dependency. It must remain usable as a pure Rust library.
- Prefer `&str` over `String` in function signatures where ownership is not needed.
- `NodeId` is `u32`. Do not widen to `usize` or `u64` — it would inflate the arena.
- Do not use `unwrap()` in production code paths. Use `?` or explicit error handling. `unwrap()` is acceptable in tests.
- `unsafe` blocks require a comment explaining why it is sound and why safe alternatives are insufficient.

---

## What not to do

- **Do not call `self._rust.attrs` in `Tag.get()`, `Tag.__getitem__`, or `Tag.__contains__`.** Use `get_coerced` / `has_attr`.
- **Do not add Python-side caching** (e.g. `@functools.lru_cache` on instance methods). The Rust selector cache already handles this; double-caching adds GIL pressure.
- **Do not change `_MULTI_VALUED_ATTRS`** without updating `get_coerced` in `lib.rs` to match — the two lists must stay in sync.
- **Do not run `maturin build --release` and commit the wheel.** Wheels are build artefacts.
- **Do not use `git add -A` or `git add .`** when committing. Stage specific files to avoid committing `__pycache__`, `.pyc`, or build artefacts.
- **Do not add `find_all_parallel()` or any multi-threaded traversal API** without a design discussion. The `Arc<RwLock<Document>>` allows concurrent reads but the Python API must remain GIL-aware.
