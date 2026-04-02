"""
whiskeysour — A high-performance BeautifulSoup-compatible HTML parser.

Drop-in replacement for bs4.BeautifulSoup backed by a Rust core.
"""

from __future__ import annotations

import re
from typing import (
    Any,
    Callable,
    Dict,
    Iterable,
    Iterator,
    List,
    Optional,
    Pattern,
    Sequence,
    Union,
)

try:
    from whiskeysour import _core
except ImportError as exc:  # pragma: no cover
    raise ImportError(
        "whiskeysour native extension not found. "
        "Run `maturin develop` (or `pip install whiskeysour`) to build it."
    ) from exc

# ── Re-export low-level types ─────────────────────────────────────────────────

_RustTag = _core._Tag
_RustDocument = _core._Document

# ── String-like wrappers ──────────────────────────────────────────────────────

class NavigableString(str):
    """A string node in the parse tree."""
    # BS4 compatibility: tag.name is None for text nodes, str for element nodes.
    # Code that filters children with `if child.name:` relies on this being falsy.
    name = None
    __slots__ = ("_rust", "_parent_ref")

    def __new__(cls, value: str, rust_tag=None) -> "NavigableString":
        obj = str.__new__(cls, value)
        obj._rust = rust_tag
        obj._parent_ref = None  # explicit parent override (e.g. from Tag.string)
        return obj

    @property
    def parent(self):
        if self._parent_ref is not None:
            return self._parent_ref
        if self._rust is None:
            return None
        p = self._rust.parent
        if p is None:
            return None
        return _wrap(p)

    @property
    def next_sibling(self):
        if self._rust is None:
            return None
        s = self._rust.next_sibling
        return _wrap(s) if s is not None else None

    @property
    def previous_sibling(self):
        if self._rust is None:
            return None
        s = self._rust.previous_sibling
        return _wrap(s) if s is not None else None

    @property
    def next_element(self):
        if self._rust is None:
            return None
        e = self._rust.next_element
        return _wrap(e) if e is not None else None

    @property
    def previous_element(self):
        if self._rust is None:
            return None
        e = self._rust.previous_element
        return _wrap(e) if e is not None else None

    def __repr__(self) -> str:
        return repr(str(self))


class CData(NavigableString):
    """CDATA section."""
    PREFIX = "<![CDATA["
    SUFFIX = "]]>"


class Comment(NavigableString):
    """An HTML/XML comment."""
    def __str__(self) -> str:
        return str.__str__(self)


class ProcessingInstruction(NavigableString):
    """A processing instruction."""


class Doctype(NavigableString):
    """A DOCTYPE declaration."""

    @classmethod
    def for_name_and_ids(cls, name, pub_id, sys_id):
        if pub_id is not None:
            value = f'{name} PUBLIC "{pub_id}"'
            if sys_id is not None:
                value += f' "{sys_id}"'
        elif sys_id is not None:
            value = f'{name} SYSTEM "{sys_id}"'
        else:
            value = name
        return cls(value)


class _AlwaysContains:
    """A sentinel list-like object that claims to contain any item."""
    def __contains__(self, item: Any) -> bool:
        return True
    def __iter__(self):
        return iter([])
    def __len__(self):
        return 0


class _DocumentWrapper:
    """Minimal document wrapper returned by Tag.parent when parent is the root."""
    name = "[document]"
    attrs: Dict = {}
    contents = _AlwaysContains()

    def __repr__(self) -> str:
        return "[document]"


class _AttrProxy(dict):
    """A dict-like proxy that syncs attribute mutations back to the Rust tag."""
    __slots__ = ("_rust",)

    def __init__(self, rust_tag: Any, data: Dict) -> None:
        super().__init__(data)
        self._rust = rust_tag

    def __setitem__(self, key: str, value: Any) -> None:
        super().__setitem__(key, value)
        if isinstance(value, list):
            self._rust[key] = " ".join(str(v) for v in value)
        else:
            self._rust[key] = str(value)

    def __delitem__(self, key: str) -> None:
        super().__delitem__(key)
        del self._rust[key]


# ── Multi-valued attribute names (bs4 compat) ────────────────────────────────

_MULTI_VALUED_ATTRS = frozenset({
    "class", "rel", "rev", "accept-charset", "headers", "accesskey",
})


def _coerce_attr(key: str, value: Optional[str]) -> Any:
    """Return value coerced to list for multi-valued attrs, str otherwise."""
    if value is None:
        return None
    if key in _MULTI_VALUED_ATTRS:
        return value.split()
    return value


# ── Tag wrapper ───────────────────────────────────────────────────────────────

# Filter types accepted by find() / find_all()
_NameSpec = Union[str, re.Pattern, Callable, List, bool, None]
_AttrSpec = Union[str, re.Pattern, Callable, List, bool, None]


def _attr_matches(value: Optional[str], spec: Any) -> bool:
    """Test a single attribute value against a filter spec."""
    if spec is True:
        return value is not None
    if spec is False or spec is None:
        return value is None
    if isinstance(spec, str):
        if isinstance(value, list):
            return spec in value
        return value == spec
    if isinstance(spec, re.Pattern):
        if value is None:
            return False
        if isinstance(value, list):
            return any(spec.search(v) for v in value)
        return bool(spec.search(value))
    if callable(spec):
        try:
            return bool(spec(value))
        except (TypeError, AttributeError):
            return False
    if isinstance(spec, list):
        return any(_attr_matches(value, s) for s in spec)
    return False


def _tag_name_matches(tag_name: Optional[str], spec: Any) -> bool:
    """Test a tag name against a name filter spec."""
    if spec is True:
        return tag_name is not None
    if spec is False or spec is None:
        return False
    if isinstance(spec, str):
        return tag_name == spec.lower()
    if isinstance(spec, re.Pattern):
        return tag_name is not None and bool(spec.search(tag_name))
    if callable(spec):
        return bool(spec(tag_name))
    if isinstance(spec, list):
        return any(_tag_name_matches(tag_name, s) for s in spec)
    return False


def _needs_python_filter(
    name: Any,
    attrs: Dict[str, Any],
    string: Any,
) -> bool:
    """Return True if any filter requires Python-side evaluation."""
    def _is_complex(v):
        return isinstance(v, (re.Pattern,)) or callable(v) or isinstance(v, list)

    if _is_complex(name):
        return True
    for v in attrs.values():
        if _is_complex(v):
            return True
    if _is_complex(string):
        return True
    return False


def _python_filter(
    tag: "Tag",
    name: Any,
    attrs: Dict[str, Any],
    string: Any,
) -> bool:
    """Python-side predicate for complex filters (regex / callable / list)."""
    tag_name = tag.name

    # Name check — if callable, pass the full Tag (bs4 behavior)
    if name is not None and name is not True:
        if callable(name):
            try:
                if not name(tag):
                    return False
            except Exception:
                return False
        elif not _tag_name_matches(tag_name, name):
            return False

    # Attribute checks
    for attr_name, spec in attrs.items():
        # Strip trailing underscore (e.g. class_ → class)
        real_name = attr_name[:-1] if attr_name.endswith("_") else attr_name
        raw = tag.get(real_name)
        # For multi-valued attrs with list spec: AND semantics (element must have ALL listed values)
        if real_name in _MULTI_VALUED_ATTRS and isinstance(spec, list):
            classes = raw if isinstance(raw, list) else (raw.split() if isinstance(raw, str) else [])
            if not all(cls in classes for cls in spec):
                return False
        else:
            if not _attr_matches(raw, spec):
                return False

    # String check
    if string is not None:
        actual = tag.string
        if not _attr_matches(actual, string):
            return False

    return True


class CompiledSelector:
    """
    A pre-compiled CSS selector that can be applied to any Tag or WhiskeySour document.

    Obtain via ``soup.compile("div.item > a")`` then reuse across many documents.
    The underlying Rust selector engine caches the compiled DFA, so repeated calls
    pay only the traversal cost — no re-parsing of the selector string.
    """

    def __init__(self, selector: str) -> None:
        self._selector = selector

    def select(self, root: "Tag | WhiskeySour", limit: int = 0) -> List["Tag"]:
        return root.select(self._selector, limit=limit)

    def select_one(self, root: "Tag | WhiskeySour") -> Optional["Tag"]:
        return root.select_one(self._selector)

    def __repr__(self) -> str:
        return f"CompiledSelector({self._selector!r})"


class Tag:
    """
    Wraps a Rust _Tag and exposes the BeautifulSoup-compatible API.
    """
    __slots__ = ("_rust",)

    def __init__(self, rust_tag: _RustTag) -> None:
        self._rust = rust_tag

    # ── Identity ──────────────────────────────────────────────────────────────

    @property
    def name(self) -> Optional[str]:
        return self._rust.name

    @property
    def attrs(self) -> "_AttrProxy":
        return _AttrProxy(self._rust, self._rust.attrs)

    @attrs.setter
    def attrs(self, value: Dict[str, Any]) -> None:
        # Replace all attributes
        for k in list(self._rust.attrs.keys()):
            del self._rust[k]
        for k, v in value.items():
            if isinstance(v, list):
                v = " ".join(v)
            self._rust[k] = str(v)

    def get(self, key: str, default: Any = None) -> Any:
        # get_coerced does a direct Rust attr scan with multi-value coercion
        # (no full dict allocation — much faster than self._rust.attrs.get())
        v = self._rust.get_coerced(key)
        return default if v is None else v

    def has_attr(self, key: str) -> bool:
        return self._rust.has_attr(key)

    def __getitem__(self, key: str) -> Any:
        v = self._rust.get_coerced(key)
        if v is None:
            raise KeyError(key)
        return v

    def __setitem__(self, key: str, value: Any) -> None:
        if isinstance(value, list):
            value = " ".join(value)
        self._rust[key] = str(value)

    def __delitem__(self, key: str) -> None:
        if self.has_attr(key):
            del self._rust[key]

    def __contains__(self, key: str) -> bool:
        return self.has_attr(key)

    # ── String content ────────────────────────────────────────────────────────

    @property
    def string(self) -> Optional[NavigableString]:
        node = self._rust.string_node
        if node is None:
            return None
        text = node.text_content or ""
        ns = NavigableString(text, rust_tag=node)
        ns._parent_ref = self  # parent is self (this Tag)
        return ns

    @string.setter
    def string(self, value: str) -> None:
        self._rust.string = value

    @property
    def strings(self) -> Iterator[NavigableString]:
        for node in self._rust.text_nodes():
            text = node.text_content or ""
            ns = NavigableString(text, rust_tag=node)
            yield ns

    @property
    def stripped_strings(self) -> Iterator[str]:
        return iter(self._rust.stripped_strings)

    def get_text(self, separator: str = "", strip: bool = False) -> str:
        return self._rust.get_text(separator, strip)

    # Alias
    text = property(lambda self: self.get_text())

    # ── Tree navigation ───────────────────────────────────────────────────────

    @property
    def parent(self) -> Optional[Any]:
        p = self._rust.parent
        if p is None:
            return None
        return _wrap(p)

    @property
    def parents(self) -> Iterator[Any]:
        return (_wrap(p) for p in self._rust.parents)

    @property
    def contents(self) -> List[Any]:
        return [_wrap(c) for c in self._rust.contents]

    @property
    def children(self) -> Iterator[Any]:
        return (_wrap(c) for c in self._rust.children)

    @property
    def descendants(self) -> Iterator[Any]:
        return (_wrap(d) for d in self._rust.descendants)

    @property
    def next_sibling(self) -> Optional[Any]:
        s = self._rust.next_sibling
        return _wrap(s) if s is not None else None

    @property
    def previous_sibling(self) -> Optional[Any]:
        s = self._rust.previous_sibling
        return _wrap(s) if s is not None else None

    @property
    def next_siblings(self) -> Iterator[Any]:
        return (_wrap(s) for s in self._rust.next_siblings)

    @property
    def previous_siblings(self) -> Iterator[Any]:
        return (_wrap(s) for s in self._rust.previous_siblings)

    @property
    def next_element(self) -> Optional[Any]:
        e = self._rust.next_element
        return _wrap(e) if e is not None else None

    @property
    def previous_element(self) -> Optional[Any]:
        e = self._rust.previous_element
        return _wrap(e) if e is not None else None

    # ── Find / select ─────────────────────────────────────────────────────────

    def find(
        self,
        name: _NameSpec = None,
        attrs: Dict[str, Any] = {},
        recursive: bool = True,
        string: Any = None,
        **kwargs: Any,
    ) -> Optional["Tag"]:
        attrs = {**attrs, **kwargs}
        result = self._find_impl(name, attrs, string, recursive, limit=1)
        return result[0] if result else None

    def find_all(
        self,
        name: _NameSpec = None,
        attrs: Dict[str, Any] = {},
        recursive: bool = True,
        string: Any = None,
        limit: int = 0,
        **kwargs: Any,
    ) -> List["Tag"]:
        attrs = {**attrs, **kwargs}
        return self._find_impl(name, attrs, string, recursive, limit)

    # Alias: tag(…) == tag.find_all(…)
    def __call__(self, *args, **kwargs) -> List["Tag"]:
        return self.find_all(*args, **kwargs)

    def _find_impl(
        self,
        name: Any,
        attrs: Dict[str, Any],
        string: Any,
        recursive: bool,
        limit: int,
    ) -> List[Any]:
        # Special case: find_all(string=...) with no name/attrs → return NavigableString objects
        if string is not None and name is None and not attrs:
            return self._find_string_nodes(string, limit)

        complex_filter = _needs_python_filter(name, attrs, string)

        if not complex_filter:
            # Fast path: delegate entirely to Rust
            rust_results = self._rust.find_all(
                name=name,
                attrs=attrs,
                recursive=recursive,
                string=string,
                limit=limit,
            )
            return [Tag(r) for r in rust_results]
        else:
            # Slow path: Rust returns all element candidates, Python filters
            rust_name = name if isinstance(name, str) and name is not True else None
            rust_attrs: Dict[str, str] = {
                k: v for k, v in attrs.items() if isinstance(v, str)
            }
            candidates = self._rust.find_all(
                name=rust_name,
                attrs=rust_attrs,
                recursive=recursive,
                string=None,
                limit=0,
            )
            results: List[Tag] = []
            for r in candidates:
                wrapped = Tag(r)
                if _python_filter(wrapped, name, attrs, string):
                    results.append(wrapped)
                    if limit and len(results) >= limit:
                        break
            return results

    def _find_string_nodes(self, string_spec: Any, limit: int) -> List[NavigableString]:
        """Return NavigableString nodes (text/comment/etc.) matching string_spec."""
        results: List[NavigableString] = []
        _STRING_TYPES = ("text", "comment", "cdata", "doctype", "processing_instruction")
        for node in self._rust.descendants:
            nt = node.node_type
            if nt not in _STRING_TYPES:
                continue
            wrapped = _wrap(node)
            # Pass the NavigableString object to callables (for isinstance checks)
            # but compare string value for string/regex specs
            if callable(string_spec):
                try:
                    match = bool(string_spec(wrapped))
                except Exception:
                    match = False
            else:
                match = _attr_matches(str(wrapped) if wrapped is not None else None, string_spec)
            if match:
                results.append(wrapped)
                if limit and len(results) >= limit:
                    break
        return results

    def select(self, selector: str, limit: int = 0) -> List["Tag"]:
        results = self._rust.select(selector)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    def select_one(self, selector: str) -> Optional["Tag"]:
        r = self._rust.select_one(selector)
        return Tag(r) if r is not None else None

    def compile(self, selector: str) -> "CompiledSelector":
        return CompiledSelector(selector)

    # ── Traversal helpers (bs4-compatible) ───────────────────────────────────

    def find_next(self, name=None, attrs={}, string=None, **kwargs) -> Optional["Tag"]:
        # Rust only supports name/string; attrs filtering is Python-side.
        name_str = name if isinstance(name, str) else None
        r = self._rust.find_next(name=name_str, string=string if isinstance(string, str) else None)
        return Tag(r) if r is not None else None

    def find_next_sibling(self, name=None, attrs={}, string=None, **kwargs) -> Optional["Tag"]:
        name_str = name if isinstance(name, str) else None
        r = self._rust.find_next_sibling(name=name_str)
        return Tag(r) if r is not None else None

    def find_next_siblings(self, name=None, attrs={}, string=None, limit=0, **kwargs) -> List["Tag"]:
        name_str = name if isinstance(name, str) else None
        results = self._rust.find_next_siblings(name=name_str)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    def find_previous_sibling(self, name=None, attrs={}, string=None, **kwargs) -> Optional["Tag"]:
        name_str = name if isinstance(name, str) else None
        r = self._rust.find_previous_sibling(name=name_str)
        return Tag(r) if r is not None else None

    def find_previous_siblings(self, name=None, attrs={}, string=None, limit=0, **kwargs) -> List["Tag"]:
        name_str = name if isinstance(name, str) else None
        results = self._rust.find_previous_siblings(name=name_str)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    def find_parent(self, name=None, attrs={}, **kwargs) -> Optional["Tag"]:
        name_str = name if isinstance(name, str) else None
        r = self._rust.find_parent(name=name_str)
        return Tag(r) if r is not None else None

    def find_parents(self, name=None, attrs={}, limit=0, **kwargs) -> List["Tag"]:
        name_str = name if isinstance(name, str) else None
        results = self._rust.find_parents(name=name_str)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    # ── Mutation ──────────────────────────────────────────────────────────────

    def _to_rust(self, other: Any) -> "_RustTag":
        """Convert a Python Tag, NavigableString, or str to a Rust _Tag."""
        if isinstance(other, Tag):
            return other._rust
        if isinstance(other, str):
            return self._rust._make_text(other)
        raise TypeError(f"Expected Tag or str, got {type(other).__name__!r}")

    def decompose(self) -> None:
        self._rust.decompose()

    def extract(self) -> "Tag":
        self._rust.extract()
        return self

    def replace_with(self, *others: Any) -> "Tag":
        # bs4 allows multiple replacement nodes; insert them in order before self
        for other in others:
            self._rust.insert_before(self._to_rust(other))
        self._rust.extract()
        return self

    def insert(self, position: int, other: Any) -> None:
        self._rust.insert(position, self._to_rust(other))

    def append(self, other: Any) -> None:
        self._rust.append(self._to_rust(other))

    def prepend(self, other: Any) -> None:
        self._rust.prepend(self._to_rust(other))

    def insert_before(self, *others: Any) -> None:
        for other in others:
            self._rust.insert_before(self._to_rust(other))

    def insert_after(self, *others: Any) -> None:
        # Insert after self, maintaining order (last goes last)
        ref = self
        for other in others:
            r = self._to_rust(other)
            ref._rust.insert_after(r)
            # Wrap to use as next reference for chaining
            ref = Tag(r)

    def clear(self) -> None:
        self._rust.clear()

    def wrap(self, other: Any) -> "Tag":
        self._rust.wrap(self._to_rust(other))
        return other if isinstance(other, Tag) else Tag(self._to_rust(other))

    def unwrap(self) -> "Tag":
        self._rust.unwrap()
        return self

    # ── Navigation helpers (bs4-compatible) ──────────────────────────────────

    def find_all_next(self, name=None, attrs={}, string=None, limit=0, **kwargs) -> List["Tag"]:
        """All elements after this one in document order."""
        name_str = name if isinstance(name, str) else None
        results = self._rust.find_next_elements(name=name_str)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    def find_all_previous(self, name=None, attrs={}, string=None, limit=0, **kwargs) -> List["Tag"]:
        """All elements before this one in document order (nearest first)."""
        name_str = name if isinstance(name, str) else None
        results = self._rust.find_prev_elements(name=name_str)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    # ── Serialisation ─────────────────────────────────────────────────────────

    def __str__(self) -> str:
        return self._rust.__str__()

    def __repr__(self) -> str:
        return self.__str__()

    def prettify(self, indent_width: int = 2, encoding: Optional[str] = None, *, indent: Optional[int] = None) -> str:
        # `indent` is a BS4-compatible alias for `indent_width`
        if indent is not None:
            indent_width = indent
        s = self._rust.prettify(indent_width)
        if encoding:
            return s.encode(encoding).decode(encoding)
        return s

    def decode(self, indent_level: int = 0) -> str:
        return self._rust.decode()

    def decode_contents(self, indent_level: int = 0) -> str:
        return self._rust.decode_contents()

    def encode(self, encoding: str = "utf-8") -> bytes:
        html = str(self)
        return html.encode(encoding, errors="replace")

    def encode_contents(self, encoding: str = "utf-8") -> bytes:
        html = self._rust.decode_contents()
        return html.encode(encoding, errors="replace")

    # ── Dunder ────────────────────────────────────────────────────────────────

    def __eq__(self, other: object) -> bool:
        if isinstance(other, Tag):
            return self._rust.__eq__(other._rust)
        return NotImplemented

    def __hash__(self) -> int:
        return self._rust.__hash__()

    def __bool__(self) -> bool:
        return True


# ── Document wrapper ──────────────────────────────────────────────────────────

class WhiskeySour:
    """
    A high-performance, BeautifulSoup-compatible HTML/XML parser.

    Usage::

        from whiskeysour import WhiskeySour
        soup = WhiskeySour("<html><body><p>Hello</p></body></html>", "html.parser")
        print(soup.find("p").get_text())
    """

    _VALID_FEATURES = frozenset({
        "html.parser", "html5lib", "lxml", "html", "xml", None,
    })

    def __init__(
        self,
        markup: Union[str, bytes] = "",
        features: Optional[str] = None,
        from_encoding: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        if features is not None and features not in self._VALID_FEATURES:
            raise ValueError(
                f"Unknown parser: {features!r}. "
                f"Use one of: {', '.join(repr(f) for f in self._VALID_FEATURES if f)}"
            )

        if isinstance(markup, bytes):
            markup_str = markup.decode(from_encoding or "utf-8", errors="replace")
        elif isinstance(markup, str):
            markup_str = markup
        else:
            # File-like object: read all content
            content = markup.read()
            if isinstance(content, bytes):
                markup_str = content.decode(from_encoding or "utf-8", errors="replace")
            else:
                markup_str = content

        self._rust = _RustDocument(
            markup_str,
            features or "html.parser",
            from_encoding or "",
        )

    # ── Document-level accessors ──────────────────────────────────────────────

    @property
    def html(self) -> Optional[Tag]:
        r = self._rust.html
        return Tag(r) if r is not None else None

    @property
    def head(self) -> Optional[Tag]:
        r = self._rust.head
        return Tag(r) if r is not None else None

    @property
    def body(self) -> Optional[Tag]:
        r = self._rust.body
        return Tag(r) if r is not None else None

    @property
    def title(self) -> Optional[Tag]:
        r = self._rust.title
        return Tag(r) if r is not None else None

    @property
    def name(self) -> str:
        return "[document]"

    @property
    def attrs(self) -> Dict[str, Any]:
        return {}

    @property
    def contents(self) -> List[Any]:
        return [_wrap(c) for c in self._rust.contents]

    @property
    def children(self) -> Iterator[Any]:
        return (_wrap(c) for c in self._rust.contents)

    def get_text(self, separator: str = "", strip: bool = False) -> str:
        return self._rust.get_text(separator, strip)

    text = property(lambda self: self.get_text())

    # ── Factory methods ───────────────────────────────────────────────────────

    def new_tag(
        self,
        name: str,
        namespace: Optional[str] = None,
        attrs: Dict[str, Any] = {},
        **kwargs: Any,
    ) -> Tag:
        all_attrs = {**attrs, **kwargs}
        str_attrs = {k: (v if isinstance(v, str) else " ".join(v) if isinstance(v, list) else str(v))
                     for k, v in all_attrs.items()}
        r = self._rust.new_tag(name, **str_attrs)
        return Tag(r)

    def new_string(self, s: str, cls=None) -> NavigableString:
        cls = cls or NavigableString
        return cls(s)

    # ── Find / select ─────────────────────────────────────────────────────────

    def find(
        self,
        name: _NameSpec = None,
        attrs: Dict[str, Any] = {},
        recursive: bool = True,
        string: Any = None,
        **kwargs: Any,
    ) -> Optional[Tag]:
        attrs = {**attrs, **kwargs}
        result = self._find_impl(name, attrs, string, recursive, limit=1)
        return result[0] if result else None

    def find_all(
        self,
        name: _NameSpec = None,
        attrs: Dict[str, Any] = {},
        recursive: bool = True,
        string: Any = None,
        limit: int = 0,
        **kwargs: Any,
    ) -> List[Tag]:
        attrs = {**attrs, **kwargs}
        return self._find_impl(name, attrs, string, recursive, limit)

    def __call__(self, *args, **kwargs) -> List[Tag]:
        return self.find_all(*args, **kwargs)

    def __getattr__(self, name: str) -> Optional[Tag]:
        # soup.div, soup.title, etc. — attribute lookup for HTML tag names.
        if name.startswith("_"):
            raise AttributeError(name)
        # Use the Rust find_all shortcut for first match by tag name
        results = self._rust.find_all(
            name=name, attrs={}, recursive=True, string=None, limit=1
        )
        if not results:
            return None
        return Tag(results[0])

    def _find_impl(
        self,
        name: Any,
        attrs: Dict[str, Any],
        string: Any,
        recursive: bool,
        limit: int,
    ) -> List[Any]:
        # Special case: find_all(string=...) with no name/attrs → return NavigableString objects
        if string is not None and name is None and not attrs:
            return self._find_string_nodes(string, limit)

        complex_filter = _needs_python_filter(name, attrs, string)

        if not complex_filter:
            rust_results = self._rust.find_all(
                name=name,
                attrs=attrs,
                recursive=recursive,
                string=string,
                limit=limit,
            )
            return [Tag(r) for r in rust_results]
        else:
            rust_name = name if isinstance(name, str) and name is not True else None
            rust_attrs = {k: v for k, v in attrs.items() if isinstance(v, str)}
            candidates = self._rust.find_all(
                name=rust_name,
                attrs=rust_attrs,
                recursive=recursive,
                string=None,
                limit=0,
            )
            results: List[Tag] = []
            for r in candidates:
                wrapped = Tag(r)
                if _python_filter(wrapped, name, attrs, string):
                    results.append(wrapped)
                    if limit and len(results) >= limit:
                        break
            return results

    def _find_string_nodes(self, string_spec: Any, limit: int) -> List[NavigableString]:
        """Return NavigableString nodes (text/comment/etc.) matching string_spec."""
        results: List[NavigableString] = []
        _STRING_TYPES = ("text", "comment", "cdata", "doctype", "processing_instruction")
        for node in self._rust.descendants:
            nt = node.node_type
            if nt not in _STRING_TYPES:
                continue
            wrapped = _wrap(node)
            # Pass the NavigableString object to callables (for isinstance checks)
            # but compare string value for string/regex specs
            if callable(string_spec):
                try:
                    match = bool(string_spec(wrapped))
                except Exception:
                    match = False
            else:
                match = _attr_matches(str(wrapped) if wrapped is not None else None, string_spec)
            if match:
                results.append(wrapped)
                if limit and len(results) >= limit:
                    break
        return results

    def select(self, selector: str, limit: int = 0) -> List[Tag]:
        results = self._rust.select(selector)
        wrapped = [Tag(r) for r in results]
        return wrapped[:limit] if limit else wrapped

    def select_one(self, selector: str) -> Optional[Tag]:
        r = self._rust.select_one(selector)
        return Tag(r) if r is not None else None

    def compile(self, selector: str) -> CompiledSelector:
        return CompiledSelector(selector)

    # ── Serialisation ─────────────────────────────────────────────────────────

    def __str__(self) -> str:
        return self._rust.__str__()

    def __repr__(self) -> str:
        return self.__str__()

    def prettify(self, indent_width: int = 2, encoding: Optional[str] = None, *, indent: Optional[int] = None) -> str:
        # `indent` is a BS4-compatible alias for `indent_width`
        if indent is not None:
            indent_width = indent
        s = self._rust.prettify(indent_width)
        if encoding:
            return s.encode(encoding).decode(encoding)
        return s

    def encode(self, encoding: str = "utf-8") -> bytes:
        html = str(self)
        # Substitute charset in meta tag to reflect the actual encoding
        if encoding.lower() not in ("utf-8", "utf8"):
            html = re.sub(
                r'(<meta[^>]+charset=")[^"]*(")',
                lambda m: m.group(1) + encoding + m.group(2),
                html, flags=re.IGNORECASE
            )
        return html.encode(encoding, errors="replace")

    def decode(self, indent_level: int = 0) -> str:
        return self._rust.decode()


# ── Streaming API ─────────────────────────────────────────────────────────────

class StreamParser:
    """
    Push-based streaming parser.  Feed byte chunks incrementally; the
    ``on_complete`` callback receives a fully-parsed :class:`WhiskeySour`
    document when :meth:`close` is called.

    Usage::

        parser = StreamParser(on_complete=lambda soup: process(soup))
        for chunk in response.iter_content(4096):
            parser.feed(chunk)
        parser.close()

    Also works as a context manager — :meth:`close` is called automatically
    on ``__exit__``::

        with StreamParser(on_complete=handler) as parser:
            for chunk in chunks:
                parser.feed(chunk)
    """

    def __init__(self, on_complete=None) -> None:
        self._buf: bytearray = bytearray()
        self._on_complete = on_complete

    def feed(self, chunk: bytes) -> None:
        """Append *chunk* to the internal buffer."""
        if chunk:
            self._buf.extend(chunk)

    def close(self) -> "WhiskeySour":
        """Finalise the parse, fire ``on_complete``, and return the document."""
        soup = WhiskeySour(bytes(self._buf), "html.parser")
        if self._on_complete is not None:
            self._on_complete(soup)
        return soup

    def __enter__(self) -> "StreamParser":
        return self

    def __exit__(self, *_) -> None:
        self.close()


def parse_stream(
    file_obj,
    find: Optional[str] = None,
    selector: Optional[str] = None,
    chunk_size: int = 65536,
):
    """
    Parse a file-like object and yield matching elements one at a time.

    This is useful for processing large files without keeping the entire
    document in memory after you have extracted what you need.

    :param file_obj: A binary or text file-like object (must support ``.read()``).
    :param find: Tag name to search for (e.g. ``"article"``).
    :param selector: CSS selector string (e.g. ``"a.read-more"``).
    :param chunk_size: Read chunk size in bytes (default 64 KB).

    Usage::

        with open("large.html", "rb") as f:
            for article in parse_stream(f, find="article"):
                print(article.find("h2").get_text())
    """
    # Read the file in chunks into a buffer so we can feed arbitrarily
    # large files without needing the whole content as a single string.
    buf = bytearray()
    while True:
        chunk = file_obj.read(chunk_size)
        if not chunk:
            break
        if isinstance(chunk, str):
            chunk = chunk.encode("utf-8")
        buf.extend(chunk)

    soup = WhiskeySour(bytes(buf), "html.parser")

    if selector:
        results = soup.select(selector)
    elif find:
        results = soup.find_all(find)
    else:
        results = [soup]

    yield from results


# ── Aliases ───────────────────────────────────────────────────────────────────

#: Drop-in alias for BeautifulSoup.
BeautifulSoup = WhiskeySour


# ── Internal helper ───────────────────────────────────────────────────────────

_NODE_TYPE_CLS = {
    "text": NavigableString,
    "comment": Comment,
    "cdata": CData,
    "doctype": Doctype,
    "processing_instruction": ProcessingInstruction,
}


def _wrap(rust_obj: Any) -> Any:
    """Wrap a Rust object returned by the core in the appropriate Python wrapper."""
    if rust_obj is None:
        return None
    if isinstance(rust_obj, _RustTag):
        nt = rust_obj.node_type
        if nt == "element":
            return Tag(rust_obj)
        if nt == "document":
            return _DocumentWrapper()
        cls = _NODE_TYPE_CLS.get(nt, NavigableString)
        text = rust_obj.text_content or ""
        ns = cls(text, rust_tag=rust_obj)
        return ns
    # Strings are returned as plain Python str from Rust (fallback)
    if isinstance(rust_obj, str):
        return NavigableString(rust_obj)
    return rust_obj


__all__ = [
    "WhiskeySour",
    "BeautifulSoup",
    "Tag",
    "NavigableString",
    "CData",
    "Comment",
    "ProcessingInstruction",
    "Doctype",
]
