//! PyO3 extension module: `whiskysour._core`
//!
//! Exposes two Python classes:
//!   `_Document` — owns the parsed tree (Arc<RwLock<Document>>)
//!   `_Tag`      — a reference into the tree (Arc + NodeId)
//!
//! The public Python API (BeautifulSoup-compatible) lives in `python/whiskysour/__init__.py`
//! which imports these and wraps them in a friendlier interface.

use std::sync::{Arc, RwLock};

use markup5ever::{namespace_url, LocalName, Namespace, QualName};
use pyo3::exceptions::{PyKeyError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::{PyBool, PyBytes, PyDict, PyList, PyString, PyTuple};
use pyo3::IntoPyObject;

use whiskysour_core::{
    document::Document,
    node::{Attr, NodeData, NodeId, DOCUMENT_ID},
    parser::{parse_html, parse_html_bytes, ParseOptions},
    query::{
        find_all, find_one, select_limit, select_one, AttrFilter, AttrValueFilter, FindOptions,
        NameFilter,
    },
    serialize::{prettify_node, serialize_inner, serialize_node},
    traversal::{
        AncestorsIter, DescendantsPreOrder, NextElementsIter, NextSiblingsIter, PrevSiblingsIter,
    },
};

// ── Shared document handle ────────────────────────────────────────────────────

type DocHandle = Arc<RwLock<Document>>;

/// Estimated subtree size (in nodes) from which queries release the GIL.
/// Releasing and re-acquiring it costs a fixed amount per call, which only
/// pays off once the Rust work is well above that cost.
const GIL_RELEASE_MIN_NODES: u32 = 4096;

/// A non-element node's item kind and text, as built by `PyTag::node_items`.
type ItemInfo<'py> = (u8, Option<Bound<'py, PyString>>);

// ── _Tag ─────────────────────────────────────────────────────────────────────

/// A reference to a single node inside a parsed document.
///
/// Python usage:
///   tag.name          → str | None
///   tag.attrs         → dict
///   tag[key]          → str
///   tag.get(key)      → str | None
///   tag.has_attr(key) → bool
///   tag.string        → str | None
///   tag.get_text()    → str
///   tag.parent()        → _Tag | None
///   tag.children      → list[_Tag]  (all child nodes)
///   tag.contents      → list[_Tag]
///   tag.find(...)     → _Tag | None
///   tag.find_all(...) → list[_Tag]
///   tag.select(css)   → list[_Tag]
///   tag.select_one(css)→ _Tag | None
///   str(tag)          → outer HTML
///   tag.prettify()    → indented HTML
#[pyclass(name = "_Tag", from_py_object)]
#[derive(Clone)]
pub struct PyTag {
    doc: DocHandle,
    pub id: NodeId,
}

impl PyTag {
    fn new(doc: DocHandle, id: NodeId) -> Self {
        PyTag { doc, id }
    }

    fn wrap_id(&self, id: NodeId) -> PyTag {
        PyTag {
            doc: Arc::clone(&self.doc),
            id,
        }
    }

    // Build FindOptions from Python keyword arguments.
    fn build_find_opts(
        &self,
        name_arg: Option<&Bound<'_, PyAny>>,
        attrs_arg: Option<&Bound<'_, PyDict>>,
        recursive: bool,
        limit: usize,
        string_arg: Option<&str>,
        kwargs: Option<&Bound<'_, PyDict>>,
    ) -> PyResult<FindOptions> {
        // NOTE: our `recursive` flag is inverted (true = non-recursive).
        let mut opts = FindOptions {
            recursive: !recursive,
            limit,
            ..Default::default()
        };

        // Name filter
        if let Some(name) = name_arg {
            if name.is_none() {
                // No filter
            } else if name.is_instance_of::<PyBool>() {
                let b: bool = name.extract()?;
                if b {
                    opts.name = Some(NameFilter::Any);
                }
            } else if let Ok(s) = name.extract::<String>() {
                opts.name = Some(NameFilter::Exact(s.to_ascii_lowercase()));
            } else if let Ok(lst) = name.extract::<Vec<String>>() {
                opts.name = Some(NameFilter::AnyOf(
                    lst.into_iter().map(|s| s.to_ascii_lowercase()).collect(),
                ));
            } else {
                // True / other truthy — match any element
                opts.name = Some(NameFilter::Any);
            }
        }

        // string filter
        if let Some(s) = string_arg {
            opts.string = Some(s.to_owned());
        }

        // attrs dict
        if let Some(d) = attrs_arg {
            self.extend_attr_filters(&mut opts.attrs, d)?;
        }

        // kwargs: class_ → class, id → id, etc.
        if let Some(kw) = kwargs {
            self.extend_attr_filters(&mut opts.attrs, kw)?;
        }

        Ok(opts)
    }

    fn extend_attr_filters(
        &self,
        filters: &mut Vec<AttrFilter>,
        d: &Bound<'_, PyDict>,
    ) -> PyResult<()> {
        for (k, v) in d.iter() {
            let name: String = k.extract()?;
            let name = if name == "class_" {
                "class".to_owned()
            } else {
                name
            };
            let vf = pyobj_to_attr_value(&v, &name)?;
            filters.push(AttrFilter { name, value: vf });
        }
        Ok(())
    }

    /// Build the Python "items" for `ids` in one pass: an element becomes its
    /// `_Tag`; any other node becomes `(kind, text, _Tag)` with `kind` from
    /// [`item_kind`]. Lets the shim wrap nodes without calling back into Rust
    /// for `node_type` and `text_content` on every node.
    fn node_items(
        &self,
        py: Python<'_>,
        ids: impl FnOnce(&Document) -> Vec<NodeId>,
    ) -> PyResult<Py<PyList>> {
        // Read the tree under the lock, creating only `str` objects there: they
        // are not GC-tracked, so no Python code can run while the lock is held.
        let raw: Vec<(NodeId, Option<ItemInfo<'_>>)> = self.read_doc(|doc| {
            ids(doc)
                .into_iter()
                .map(|id| {
                    let info = item_kind(&doc.get(id).data)
                        .map(|(kind, text)| (kind, text.map(|t| PyString::new(py, t))));
                    (id, info)
                })
                .collect()
        });
        let list = PyList::empty(py);
        for (id, info) in raw {
            let tag = Bound::new(py, self.wrap_id(id))?.into_any();
            match info {
                None => list.append(tag)?,
                Some((kind, text)) => {
                    let text = match text {
                        Some(t) => t.into_any(),
                        None => py.None().into_bound(py),
                    };
                    list.append(PyTuple::new(
                        py,
                        [kind.into_pyobject(py)?.into_any(), text, tag],
                    )?)?
                }
            }
        }
        Ok(list.unbind())
    }

    /// Like `read_doc`, but releases the GIL while `f` runs when the subtree
    /// is large enough for that to pay off, so other Python threads can run
    /// during big queries without taxing small per-element calls.
    fn read_doc_detached<F, R>(&self, py: Python<'_>, f: F) -> R
    where
        F: FnOnce(&Document) -> R + Send,
        R: Send,
    {
        if self.read_doc(|doc| subtree_span(doc, self.id)) >= GIL_RELEASE_MIN_NODES {
            // Take the lock *inside* the detached section. Holding it while
            // waiting to re-acquire the GIL could deadlock with a thread that
            // holds the GIL and is waiting for the write lock.
            py.detach(|| self.read_doc(f))
        } else {
            self.read_doc(f)
        }
    }

    fn read_doc<F, R>(&self, f: F) -> R
    where
        F: FnOnce(&Document) -> R,
    {
        let doc = self.doc.read().expect("document lock poisoned");
        f(&doc)
    }

    fn write_doc<F, R>(&self, f: F) -> R
    where
        F: FnOnce(&mut Document) -> R,
    {
        let mut doc = self.doc.write().expect("document lock poisoned");
        f(&mut doc)
    }
}

fn pyobj_to_attr_value(v: &Bound<'_, PyAny>, attr_name: &str) -> PyResult<AttrValueFilter> {
    if v.is_none() {
        return Ok(AttrValueFilter::Present);
    }
    if let Ok(b) = v.extract::<bool>() {
        // bs4 semantics: attr=True → must be present, attr=False → must be absent.
        return Ok(if b {
            AttrValueFilter::Present
        } else {
            AttrValueFilter::Absent
        });
    }
    if let Ok(s) = v.extract::<String>() {
        // For `class`, use token-contains semantics.
        return Ok(if attr_name == "class" {
            AttrValueFilter::ContainsToken(s)
        } else {
            AttrValueFilter::Exact(s)
        });
    }
    // Fallback: present
    Ok(AttrValueFilter::Present)
}

#[pymethods]
impl PyTag {
    // ── Basic properties ──────────────────────────────────────────────────────

    #[getter]
    fn name(&self) -> Option<String> {
        self.read_doc(|doc| doc.get(self.id).tag_name().map(|s| s.to_owned()))
    }

    #[getter]
    fn attrs(&self, py: Python) -> Py<PyAny> {
        let d = PyDict::new(py);
        self.read_doc(|doc| {
            if let Some(attrs) = doc.get(self.id).data.attrs() {
                for a in attrs.iter() {
                    let local = a.local_name();
                    // `class` and `rel` → list; everything else → str.
                    let val: Py<PyAny> = if local == "class"
                        || local == "rel"
                        || local == "rev"
                        || local == "accept-charset"
                        || local == "headers"
                        || local == "accesskey"
                    {
                        let tokens: Vec<&str> = a.value.split_ascii_whitespace().collect();
                        PyList::new(py, &tokens).unwrap().into_any().unbind()
                    } else {
                        a.value
                            .clone()
                            .into_pyobject(py)
                            .unwrap()
                            .into_any()
                            .unbind()
                    };
                    d.set_item(local, val).ok();
                }
            }
        });
        d.into_any().unbind()
    }

    fn get(&self, py: Python, key: &str, default: Option<Py<PyAny>>) -> Py<PyAny> {
        let val = self.read_doc(|doc| doc.get_attr(self.id, key).map(|v| v.to_owned()));
        match val {
            Some(v) => v.into_pyobject(py).unwrap().into_any().unbind(),
            None => default.unwrap_or_else(|| py.None()),
        }
    }

    /// Single-key lookup with multi-value coercion (class/rel/etc. → list).
    /// Returns None if the attribute is absent. Much cheaper than building
    /// the full `attrs` dict when only one value is needed.
    fn get_coerced(&self, py: Python, key: &str) -> Option<Py<PyAny>> {
        const MULTI: &[&str] = &[
            "class",
            "rel",
            "rev",
            "accept-charset",
            "headers",
            "accesskey",
        ];
        let is_multi = MULTI.contains(&key);
        self.read_doc(|doc| {
            let attrs = doc.get(self.id).data.attrs()?;
            for a in attrs.iter() {
                if a.local_name() == key {
                    return Some(if is_multi {
                        let tokens: Vec<&str> = a.value.split_ascii_whitespace().collect();
                        PyList::new(py, &tokens).unwrap().into_any().unbind()
                    } else {
                        a.value
                            .clone()
                            .into_pyobject(py)
                            .unwrap()
                            .into_any()
                            .unbind()
                    });
                }
            }
            None
        })
    }

    fn has_attr(&self, key: &str) -> bool {
        self.read_doc(|doc| doc.get_attr(self.id, key).is_some())
    }

    fn __getitem__(&self, py: Python, key: &str) -> PyResult<Py<PyAny>> {
        let val = self.read_doc(|doc| doc.get_attr(self.id, key).map(|v| v.to_owned()));
        match val {
            Some(v) => Ok(v.into_pyobject(py).unwrap().into_any().unbind()),
            None => Err(PyKeyError::new_err(key.to_owned())),
        }
    }

    fn __setitem__(&self, key: &str, value: &str) {
        self.write_doc(|doc| doc.set_attr(self.id, key, value));
    }

    fn __delitem__(&self, key: &str) -> PyResult<()> {
        let exists = self.read_doc(|doc| doc.get_attr(self.id, key).is_some());
        if !exists {
            return Err(PyKeyError::new_err(key.to_owned()));
        }
        self.write_doc(|doc| doc.remove_attr(self.id, key));
        Ok(())
    }

    fn __contains__(&self, key: &str) -> bool {
        self.has_attr(key)
    }

    // ── String / text ─────────────────────────────────────────────────────────

    /// Returns the single text child _Tag node (for NavigableString.parent()/next_element support).
    #[getter]
    fn string_node(&self) -> Option<PyTag> {
        self.read_doc(|doc| {
            // Only need to know whether there is exactly one, so stop at two.
            let mut text_nodes: Vec<NodeId> = Vec::with_capacity(2);
            collect_string_nodes(doc, self.id, &mut text_nodes, 2);
            if text_nodes.len() == 1 {
                Some(text_nodes[0])
            } else {
                None
            }
        })
        .map(|id| self.wrap_id(id))
    }

    /// All descendant text node _Tags (for NavigableString iteration with parent support).
    fn text_nodes(&self) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            DescendantsPreOrder::new(doc, self.id)
                .filter(|&id| matches!(doc.get(id).data, NodeData::Text(_)))
                .collect::<Vec<_>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    /// Like `text_nodes`, as `(kind, text, _Tag)` items (see `node_items`).
    fn text_node_items(&self, py: Python<'_>) -> PyResult<Py<PyList>> {
        let id = self.id;
        self.node_items(py, |doc| {
            DescendantsPreOrder::new(doc, id)
                .filter(|&d| matches!(doc.get(d).data, NodeData::Text(_)))
                .collect()
        })
    }

    /// `.contents` as pre-classified items (see `node_items`).
    #[getter]
    fn contents_items(&self, py: Python<'_>) -> PyResult<Py<PyList>> {
        let id = self.id;
        self.node_items(py, |doc| doc.children_ids(id).collect())
    }

    /// `.descendants` as pre-classified items (see `node_items`).
    #[getter]
    fn descendants_items(&self, py: Python<'_>) -> PyResult<Py<PyList>> {
        let id = self.id;
        self.node_items(py, |doc| DescendantsPreOrder::new(doc, id).collect())
    }

    /// Descendant string-like nodes (text, comment, CDATA, doctype, PI) in
    /// document order. With `value`, only nodes whose text equals it; `limit`
    /// (0 = unlimited) caps the result. Backs `find_all(string=...)`.
    #[pyo3(signature = (value=None, limit=0))]
    fn find_strings(&self, py: Python<'_>, value: Option<&str>, limit: usize) -> Vec<PyTag> {
        let limit = if limit == 0 { usize::MAX } else { limit };
        let ids = self.read_doc_detached(py, |doc| {
            DescendantsPreOrder::new(doc, self.id)
                .filter(|&id| {
                    let text = match &doc.get(id).data {
                        NodeData::Text(t) | NodeData::Comment(t) | NodeData::CData(t) => t,
                        NodeData::Doctype(d) => &d.name,
                        NodeData::ProcessingInstruction(pi) => &pi.data,
                        NodeData::Element { .. } | NodeData::Document => return false,
                    };
                    value.is_none_or(|v| v == text)
                })
                .take(limit)
                .collect::<Vec<_>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    /// `.string` — the single text child if the element has exactly one
    /// non-empty text descendant; None otherwise.
    #[getter]
    fn string(&self) -> Option<String> {
        self.read_doc(|doc| {
            // Exactly one non-empty text node → that text; otherwise None.
            let mut only: Option<&str> = None;
            let mut count = 0usize;
            for_each_string(doc, self.id, &mut |t| {
                count += 1;
                only = Some(t);
            });
            if count == 1 {
                only.map(str::to_owned)
            } else {
                None
            }
        })
    }

    #[setter]
    fn set_string(&self, value: &str) {
        self.write_doc(|doc| {
            doc.clear_children(self.id);
            let txt = doc.alloc(NodeData::Text(value.to_owned()));
            doc.append_child(self.id, txt);
        });
    }

    /// `.strings` — iterator over all text descendants (as Python list).
    #[getter]
    fn strings(&self, py: Python) -> Py<PyAny> {
        let v: Vec<String> = self.read_doc(|doc| {
            let mut out = Vec::new();
            for_each_string(doc, self.id, &mut |t| out.push(t.to_owned()));
            out
        });
        v.into_pyobject(py).unwrap().into_any().unbind()
    }

    /// `.stripped_strings` — text descendants with leading/trailing whitespace stripped
    /// and empty strings removed.
    #[getter]
    fn stripped_strings(&self, py: Python) -> Py<PyAny> {
        let v: Vec<String> = self.read_doc(|doc| {
            let mut out = Vec::new();
            for_each_string(doc, self.id, &mut |t| {
                let t = t.trim();
                if !t.is_empty() {
                    out.push(t.to_owned());
                }
            });
            out
        });
        v.into_pyobject(py).unwrap().into_any().unbind()
    }

    /// `.get_text(separator="", strip=False)`
    #[pyo3(signature = (separator="", strip=false))]
    fn get_text(&self, py: Python<'_>, separator: &str, strip: bool) -> String {
        self.read_doc_detached(py, |doc| {
            // Append straight into one buffer; no per-node String clones.
            let mut out = String::new();
            let mut first = true;
            for_each_string(doc, self.id, &mut |t| {
                let t = if strip { t.trim() } else { t };
                if strip && t.is_empty() {
                    return;
                }
                if !first {
                    out.push_str(separator);
                }
                first = false;
                out.push_str(t);
            });
            out
        })
    }

    // ── Tree navigation ───────────────────────────────────────────────────────

    /// Returns the node type: "element", "text", "comment", "cdata", "doctype", or "document".
    #[getter]
    fn node_type(&self) -> &'static str {
        self.read_doc(|doc| match &doc.get(self.id).data {
            NodeData::Element { .. } => "element",
            NodeData::Text(_) => "text",
            NodeData::Comment(_) => "comment",
            NodeData::CData(_) => "cdata",
            NodeData::Doctype { .. } => "doctype",
            NodeData::ProcessingInstruction { .. } => "processing_instruction",
            NodeData::Document => "document",
        })
    }

    /// For text/comment/cdata/doctype nodes: returns the text content. None for elements.
    #[getter]
    fn text_content(&self) -> Option<String> {
        self.read_doc(|doc| match &doc.get(self.id).data {
            NodeData::Text(t) => Some(t.clone()),
            NodeData::Comment(c) => Some(c.clone()),
            NodeData::CData(d) => Some(d.clone()),
            NodeData::Doctype(d) => Some(d.name.clone()),
            NodeData::ProcessingInstruction(pi) => Some(pi.data.clone()),
            _ => None,
        })
    }

    #[getter]
    fn parent(&self) -> Option<PyTag> {
        // Return ALL parents including Document node, so Python can wrap it as [document]
        self.read_doc(|doc| doc.get(self.id).parent())
            .map(|p| self.wrap_id(p))
    }

    /// `.parents` — list of all ancestors including the Document.
    #[getter]
    fn parents(&self, py: Python) -> Py<PyAny> {
        let ids: Vec<NodeId> = self.read_doc(|doc| AncestorsIter::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_pyobject(py).unwrap().into_any().unbind()
    }

    /// `.contents` — list of all direct children (tags + text nodes).
    #[getter]
    fn contents(&self, py: Python) -> Py<PyAny> {
        let ids: Vec<NodeId> = self.read_doc(|doc| doc.children_ids(self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_pyobject(py).unwrap().into_any().unbind()
    }

    /// `.children` — same as `.contents` but returned as a Python list (generator in bs4).
    #[getter]
    fn children(&self, py: Python) -> Py<PyAny> {
        self.contents(py)
    }

    /// `.descendants` — all descendants in pre-order.
    #[getter]
    fn descendants(&self, py: Python) -> Py<PyAny> {
        let ids: Vec<NodeId> =
            self.read_doc(|doc| DescendantsPreOrder::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_pyobject(py).unwrap().into_any().unbind()
    }

    #[getter]
    fn next_sibling(&self) -> Option<PyTag> {
        self.read_doc(|doc| doc.get(self.id).next_sibling())
            .map(|i| self.wrap_id(i))
    }

    #[getter]
    fn previous_sibling(&self) -> Option<PyTag> {
        self.read_doc(|doc| doc.get(self.id).prev_sibling())
            .map(|i| self.wrap_id(i))
    }

    #[getter]
    fn next_siblings(&self, py: Python) -> Py<PyAny> {
        let ids: Vec<NodeId> = self.read_doc(|doc| NextSiblingsIter::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_pyobject(py).unwrap().into_any().unbind()
    }

    #[getter]
    fn previous_siblings(&self, py: Python) -> Py<PyAny> {
        let ids: Vec<NodeId> = self.read_doc(|doc| PrevSiblingsIter::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_pyobject(py).unwrap().into_any().unbind()
    }

    #[getter]
    fn next_element(&self) -> Option<PyTag> {
        self.read_doc(|doc| {
            // DFS: first child, else next sibling, else ancestor's next sibling.
            doc.get(self.id)
                .first_child()
                .or_else(|| doc.get(self.id).next_sibling())
                .or_else(|| {
                    let mut cur = self.id;
                    loop {
                        match doc.get(cur).parent() {
                            Some(p) => match doc.get(p).next_sibling() {
                                Some(n) => break Some(n),
                                None => cur = p,
                            },
                            None => break None,
                        }
                    }
                })
        })
        .map(|i| self.wrap_id(i))
    }

    #[getter]
    fn previous_element(&self) -> Option<PyTag> {
        // Previous in DFS order = prev_sibling's last descendant, or parent.
        self.read_doc(|doc| {
            match doc.get(self.id).prev_sibling() {
                Some(prev) => {
                    // Walk to deepest last-child of prev.
                    let mut cur = prev;
                    while let Some(lc) = doc.get(cur).last_child() {
                        cur = lc;
                    }
                    Some(cur)
                }
                None => doc.get(self.id).parent(),
            }
        })
        .map(|i| self.wrap_id(i))
    }

    // ── find / find_all ───────────────────────────────────────────────────────

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, **kwargs))]
    fn find(
        &self,
        py: Python<'_>,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Option<PyTag>> {
        let opts = self.build_find_opts(
            name.as_ref(),
            attrs.as_ref(),
            recursive,
            1,
            string,
            kwargs.as_ref(),
        )?;
        let result = self.read_doc_detached(py, |doc| find_one(doc, self.id, &opts));
        Ok(result.map(|i| self.wrap_id(i)))
    }

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    // Mirrors BeautifulSoup's find_all() signature, plus the GIL token.
    #[allow(clippy::too_many_arguments)]
    fn find_all(
        &self,
        py: Python<'_>,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        let mut opts = self.build_find_opts(
            name.as_ref(),
            attrs.as_ref(),
            recursive,
            limit,
            string,
            kwargs.as_ref(),
        )?;
        opts.limit = limit;
        let ids = self.read_doc_detached(py, |doc| find_all(doc, self.id, &opts));
        Ok(ids.into_iter().map(|i| self.wrap_id(i)).collect())
    }

    /// Alias: `tag("p")` == `tag.find_all("p")`.
    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    // Mirrors BeautifulSoup's find_all() signature, plus the GIL token.
    #[allow(clippy::too_many_arguments)]
    fn __call__(
        &self,
        py: Python<'_>,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        self.find_all(py, name, attrs, recursive, string, limit, kwargs)
    }

    // ── CSS selectors ─────────────────────────────────────────────────────────

    #[pyo3(signature = (css, limit=0))]
    fn select(&self, py: Python<'_>, css: &str, limit: usize) -> PyResult<Vec<PyTag>> {
        let ids = self
            .read_doc_detached(py, |doc| select_limit(doc, self.id, css, limit))
            .map_err(PyValueError::new_err)?;
        Ok(ids.into_iter().map(|i| self.wrap_id(i)).collect())
    }

    fn select_one(&self, py: Python<'_>, css: &str) -> PyResult<Option<PyTag>> {
        let id = self
            .read_doc_detached(py, |doc| select_one(doc, self.id, css))
            .map_err(PyValueError::new_err)?;
        Ok(id.map(|i| self.wrap_id(i)))
    }

    // ── find_next / find_previous family ──────────────────────────────────────

    #[pyo3(signature = (name=None, string=None))]
    fn find_next(&self, name: Option<&str>, string: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            NextElementsIter::new(doc, self.id).find(|&id| {
                if !doc.get(id).data.is_element() {
                    return false;
                }
                if let Some(n) = name {
                    if doc.get(id).tag_name() != Some(n) {
                        return false;
                    }
                }
                if let Some(s) = string {
                    if doc.get_text(id).trim() != s {
                        return false;
                    }
                }
                true
            })
        })
        .map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_next_sibling(&self, name: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            NextSiblingsIter::new(doc, self.id).find(|&id| {
                doc.get(id).data.is_element()
                    && name.is_none_or(|n| doc.get(id).tag_name() == Some(n))
            })
        })
        .map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_next_siblings(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            NextSiblingsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element()
                        && name.is_none_or(|n| doc.get(id).tag_name() == Some(n))
                })
                .collect::<Vec<_>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    #[pyo3(signature = (name=None))]
    fn find_previous_sibling(&self, name: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            PrevSiblingsIter::new(doc, self.id).find(|&id| {
                doc.get(id).data.is_element()
                    && name.is_none_or(|n| doc.get(id).tag_name() == Some(n))
            })
        })
        .map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_previous_siblings(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            PrevSiblingsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element()
                        && name.is_none_or(|n| doc.get(id).tag_name() == Some(n))
                })
                .collect::<Vec<_>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    #[pyo3(signature = (name=None))]
    fn find_parent(&self, name: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            AncestorsIter::new(doc, self.id)
                .take_while(|&p| !matches!(doc.get(p).data, NodeData::Document))
                .find(|&p| {
                    doc.get(p).data.is_element()
                        && name.is_none_or(|n| doc.get(p).tag_name() == Some(n))
                })
        })
        .map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_parents(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            AncestorsIter::new(doc, self.id)
                .take_while(|&p| !matches!(doc.get(p).data, NodeData::Document))
                .filter(|&p| {
                    doc.get(p).data.is_element()
                        && name.is_none_or(|n| doc.get(p).tag_name() == Some(n))
                })
                .collect::<Vec<_>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    // ── Mutation ──────────────────────────────────────────────────────────────

    fn decompose(&self) {
        self.write_doc(|doc| doc.detach(self.id));
    }

    fn extract(&self) -> PyTag {
        self.write_doc(|doc| doc.detach(self.id));
        self.clone()
    }

    fn append(&self, child: &PyTag) {
        let child_id = child.id;
        self.write_doc(|doc| {
            doc.detach(child_id);
            doc.append_child(self.id, child_id);
        });
    }

    fn prepend(&self, child: &PyTag) {
        let child_id = child.id;
        self.write_doc(|doc| {
            doc.detach(child_id);
            doc.prepend_child(self.id, child_id);
        });
    }

    fn insert(&self, pos: usize, child: &PyTag) {
        let child_id = child.id;
        let self_id = self.id;
        self.write_doc(|doc| {
            doc.detach(child_id);
            // Count only element children for position (bs4 compat: position refers to element children)
            let elem_children: Vec<NodeId> = doc
                .children_ids(self_id)
                .filter(|&id| doc.get(id).data.is_element())
                .collect();
            if pos == 0 {
                doc.prepend_child(self_id, child_id);
            } else if pos >= elem_children.len() {
                doc.append_child(self_id, child_id);
            } else {
                doc.insert_before(elem_children[pos], child_id);
            }
        });
    }

    fn insert_before(&self, new_node: &PyTag) {
        let new_id = new_node.id;
        self.write_doc(|doc| {
            doc.detach(new_id);
            doc.insert_before(self.id, new_id);
        });
    }

    fn insert_after(&self, new_node: &PyTag) {
        let new_id = new_node.id;
        self.write_doc(|doc| {
            doc.detach(new_id);
            doc.insert_after(self.id, new_id);
        });
    }

    fn replace_with(&self, replacement: &PyTag) {
        let rep_id = replacement.id;
        let self_id = self.id;
        self.write_doc(|doc| {
            doc.detach(rep_id);
            doc.insert_before(self_id, rep_id);
            doc.detach(self_id);
        });
    }

    fn clear(&self) {
        self.write_doc(|doc| doc.clear_children(self.id));
    }

    fn wrap(&self, wrapper: &PyTag) -> PyTag {
        let wrap_id = wrapper.id;
        let self_id = self.id;
        self.write_doc(|doc| {
            // Insert wrapper before self, then move self inside wrapper.
            doc.insert_before(self_id, wrap_id);
            doc.detach(self_id);
            doc.append_child(wrap_id, self_id);
        });
        self.wrap_node(wrap_id)
    }

    fn unwrap(&self) {
        let self_id = self.id;
        self.write_doc(|doc| {
            // Move children before self, then detach self.
            let children: Vec<NodeId> = doc.children_ids(self_id).collect();
            for child in children {
                doc.detach(child);
                doc.insert_before(self_id, child);
            }
            doc.detach(self_id);
        });
    }

    // ── Serialisation ─────────────────────────────────────────────────────────

    fn __str__(&self, py: Python<'_>) -> String {
        self.read_doc_detached(py, |doc| serialize_node(doc, self.id))
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        self.__str__(py)
    }

    #[pyo3(signature = (indent_width=2))]
    fn prettify(&self, py: Python<'_>, indent_width: usize) -> String {
        self.read_doc_detached(py, |doc| prettify_node(doc, self.id, indent_width))
    }

    fn decode(&self, py: Python<'_>) -> String {
        self.__str__(py)
    }

    fn decode_contents(&self, py: Python<'_>) -> String {
        self.read_doc_detached(py, |doc| serialize_inner(doc, self.id))
    }

    #[pyo3(signature = (encoding="utf-8"))]
    fn encode<'py>(&self, py: Python<'py>, encoding: &str) -> PyResult<Bound<'py, PyBytes>> {
        // Byte encoding is applied by the Python shim; the core always emits UTF-8.
        let _ = encoding;
        let s = self.__str__(py);
        Ok(PyBytes::new(py, s.as_bytes()))
    }

    fn encode_contents<'py>(&self, py: Python<'py>, encoding: &str) -> Bound<'py, PyBytes> {
        let _ = encoding;
        PyBytes::new(py, self.decode_contents(py).as_bytes())
    }

    // ── Equality / hash ───────────────────────────────────────────────────────

    fn __eq__(&self, other: &PyTag) -> bool {
        Arc::ptr_eq(&self.doc, &other.doc) && self.id == other.id
    }

    fn __hash__(&self) -> u64 {
        use std::collections::hash_map::DefaultHasher;
        use std::hash::{Hash, Hasher};
        let mut h = DefaultHasher::new();
        self.id.hash(&mut h);
        (Arc::as_ptr(&self.doc) as u64).hash(&mut h);
        h.finish()
    }

    // ── Internal helper (Python can't call this) ──────────────────────────────
    fn wrap_node(&self, id: NodeId) -> PyTag {
        PyTag {
            doc: Arc::clone(&self.doc),
            id,
        }
    }

    /// All elements after self in document order (for find_all_next).
    fn find_next_elements(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            NextElementsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element()
                        && name.is_none_or(|n| doc.get(id).tag_name() == Some(n))
                })
                .collect::<Vec<NodeId>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    /// All elements before self in document order (for find_all_previous).
    fn find_prev_elements(&self, name: Option<&str>) -> Vec<PyTag> {
        // Collect ancestors+preceding siblings by walking backwards
        let ids = self.read_doc(|doc| {
            PrevSiblingsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element()
                        && name.is_none_or(|n| doc.get(id).tag_name() == Some(n))
                })
                .collect::<Vec<NodeId>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    /// Create a text node in the same document. Used by the Python shim to
    /// convert string arguments to mutation methods into _Tag objects.
    fn _make_text(&self, text: &str) -> PyTag {
        let id = self
            .doc
            .write()
            .expect("lock")
            .alloc(NodeData::Text(text.to_owned()));
        self.wrap_id(id)
    }
}

// ── _Document ─────────────────────────────────────────────────────────────────

/// The parsed document. Python-facing constructor.
#[pyclass(name = "_Document")]
pub struct PyDocument {
    doc: DocHandle,
}

impl PyDocument {
    fn tag(&self, id: NodeId) -> PyTag {
        PyTag::new(Arc::clone(&self.doc), id)
    }
}

#[pymethods]
impl PyDocument {
    #[new]
    #[pyo3(signature = (markup, features=None, from_encoding=None))]
    fn new(
        py: Python<'_>,
        markup: Bound<'_, PyAny>,
        features: Option<&str>,
        from_encoding: Option<&str>,
    ) -> PyResult<Self> {
        let _ = features; // accepted for BS4 compat; we always use html5ever
        let opts = ParseOptions {
            from_encoding: from_encoding.map(|s| s.to_owned()),
        };

        // Extract the input while holding the GIL, then release it for the
        // (potentially large) parse so other Python threads can run concurrently.
        enum Input {
            Str(String),
            Bytes(Vec<u8>),
        }
        let input = if let Ok(s) = markup.extract::<String>() {
            Input::Str(s)
        } else if let Ok(b) = markup.extract::<Vec<u8>>() {
            Input::Bytes(b)
        } else {
            // File-like object: read it.
            Input::Bytes(markup.call_method0("read")?.extract()?)
        };

        let doc = py.detach(move || match input {
            Input::Str(s) => parse_html(&s, opts),
            Input::Bytes(b) => parse_html_bytes(&b, opts),
        });
        Ok(PyDocument {
            doc: Arc::new(RwLock::new(doc)),
        })
    }

    // ── Document-level shortcuts ──────────────────────────────────────────────

    #[getter]
    fn html(&self) -> Option<PyTag> {
        let doc = self.doc.read().ok()?;
        let id = doc
            .children_ids(DOCUMENT_ID)
            .find(|&id| doc.get(id).tag_name() == Some("html"))?;
        Some(self.tag(id))
    }

    #[getter]
    fn head(&self) -> Option<PyTag> {
        let doc = self.doc.read().ok()?;
        find_by_name(&doc, DOCUMENT_ID, "head").map(|id| self.tag(id))
    }

    #[getter]
    fn body(&self) -> Option<PyTag> {
        let doc = self.doc.read().ok()?;
        find_by_name(&doc, DOCUMENT_ID, "body").map(|id| self.tag(id))
    }

    #[getter]
    fn title(&self) -> Option<PyTag> {
        let doc = self.doc.read().ok()?;
        find_by_name(&doc, DOCUMENT_ID, "title").map(|id| self.tag(id))
    }

    /// `soup.div` → first <div>; `soup.p` → first <p>; etc.
    fn __getattr__(&self, name: &str) -> PyResult<Option<PyTag>> {
        let doc = self
            .doc
            .read()
            .map_err(|_| PyValueError::new_err("lock error"))?;
        Ok(find_by_name(&doc, DOCUMENT_ID, name).map(|id| self.tag(id)))
    }

    // ── find / find_all (delegates to root _Tag) ──────────────────────────────

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, **kwargs))]
    fn find(
        &self,
        py: Python<'_>,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Option<PyTag>> {
        self.root_tag()
            .find(py, name, attrs, recursive, string, kwargs)
    }

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    // Mirrors BeautifulSoup's find_all() signature, plus the GIL token.
    #[allow(clippy::too_many_arguments)]
    fn find_all(
        &self,
        py: Python<'_>,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        self.root_tag()
            .find_all(py, name, attrs, recursive, string, limit, kwargs)
    }

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    // Mirrors BeautifulSoup's find_all() signature, plus the GIL token.
    #[allow(clippy::too_many_arguments)]
    fn __call__(
        &self,
        py: Python<'_>,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        self.find_all(py, name, attrs, recursive, string, limit, kwargs)
    }

    #[pyo3(signature = (css, limit=0))]
    fn select(&self, py: Python<'_>, css: &str, limit: usize) -> PyResult<Vec<PyTag>> {
        self.root_tag().select(py, css, limit)
    }

    #[pyo3(signature = (value=None, limit=0))]
    fn find_strings(&self, py: Python<'_>, value: Option<&str>, limit: usize) -> Vec<PyTag> {
        self.root_tag().find_strings(py, value, limit)
    }

    fn select_one(&self, py: Python<'_>, css: &str) -> PyResult<Option<PyTag>> {
        self.root_tag().select_one(py, css)
    }

    // ── Tree access (doc acts as a tag for navigation purposes) ───────────────

    #[getter]
    fn contents(&self, py: Python) -> Py<PyAny> {
        self.root_tag().contents(py)
    }

    #[getter]
    fn contents_items(&self, py: Python<'_>) -> PyResult<Py<PyList>> {
        self.root_tag().contents_items(py)
    }

    #[getter]
    fn descendants_items(&self, py: Python<'_>) -> PyResult<Py<PyList>> {
        self.root_tag().descendants_items(py)
    }

    #[pyo3(signature = (separator="", strip=false))]
    fn get_text(&self, py: Python<'_>, separator: &str, strip: bool) -> String {
        self.root_tag().get_text(py, separator, strip)
    }

    /// All descendants of the document (all nodes including text/comment).
    #[getter]
    fn descendants(&self, py: Python) -> Py<PyAny> {
        let ids: Vec<NodeId> = self
            .doc
            .read()
            .map(|doc| DescendantsPreOrder::new(&doc, DOCUMENT_ID).collect())
            .unwrap_or_default();
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.tag(i)).collect();
        tags.into_pyobject(py).unwrap().into_any().unbind()
    }

    // ── new_tag / new_string ──────────────────────────────────────────────────

    #[pyo3(signature = (name, **kwargs))]
    fn new_tag(&self, name: &str, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<PyTag> {
        let mut attrs: Vec<Attr> = Vec::new();
        if let Some(kw) = kwargs {
            for (k, v) in kw.iter() {
                let key: String = k.extract()?;
                let key = if key == "class_" {
                    "class".to_owned()
                } else {
                    key
                };
                let val: String = v.extract()?;
                let qname = QualName::new(None, Namespace::from(""), LocalName::from(key.as_str()));
                attrs.push(Attr::new(qname, val));
            }
        }
        let qname = QualName::new(None, markup5ever::ns!(html), LocalName::from(name));
        let data = NodeData::element(qname, attrs, false, false);
        let id = self
            .doc
            .write()
            .map_err(|_| PyValueError::new_err("lock"))?
            .alloc(data);
        Ok(self.tag(id))
    }

    fn new_string(&self, text: &str) -> PyTag {
        let id = self
            .doc
            .write()
            .expect("lock")
            .alloc(NodeData::Text(text.to_owned()));
        self.tag(id)
    }

    // ── Serialisation ─────────────────────────────────────────────────────────

    fn __str__(&self, py: Python<'_>) -> String {
        self.root_tag().__str__(py)
    }

    fn __repr__(&self, py: Python<'_>) -> String {
        self.__str__(py)
    }

    #[pyo3(signature = (indent_width=2))]
    fn prettify(&self, py: Python<'_>, indent_width: usize) -> String {
        self.root_tag().prettify(py, indent_width)
    }

    fn decode(&self, py: Python<'_>) -> String {
        self.__str__(py)
    }

    #[pyo3(signature = (encoding="utf-8"))]
    fn encode<'py>(&self, py: Python<'py>, encoding: &str) -> Bound<'py, PyBytes> {
        let _ = encoding;
        PyBytes::new(py, self.__str__(py).as_bytes())
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    fn root_tag(&self) -> PyTag {
        self.tag(DOCUMENT_ID)
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

/// Cheap estimate of the number of nodes in `node`'s subtree, used to decide
/// whether a query is worth releasing the GIL for. The parser allocates ids in
/// document order, so a subtree spans the ids up to the next node after it.
/// Mutation can break that ordering; the estimate then only affects whether
/// the GIL is released, never the result.
fn subtree_span(doc: &Document, node: NodeId) -> u32 {
    let mut cur = node;
    loop {
        let n = doc.get(cur);
        if let Some(next) = n.next_sibling() {
            return next.saturating_sub(node);
        }
        match n.parent() {
            Some(p) => cur = p,
            None => {
                return u32::try_from(doc.len())
                    .unwrap_or(u32::MAX)
                    .saturating_sub(node)
            }
        }
    }
}

/// Item kind codes shared with the Python shim's `_ITEM_CLASSES`:
/// 0 text, 1 comment, 2 CDATA, 3 doctype, 4 processing instruction, 5 document.
/// Returns `None` for elements, otherwise the kind and the node's text.
fn item_kind(data: &NodeData) -> Option<(u8, Option<&str>)> {
    match data {
        NodeData::Element { .. } => None,
        NodeData::Text(t) => Some((0, Some(t))),
        NodeData::Comment(t) => Some((1, Some(t))),
        NodeData::CData(t) => Some((2, Some(t))),
        NodeData::Doctype(d) => Some((3, Some(&d.name))),
        NodeData::ProcessingInstruction(pi) => Some((4, Some(&pi.data))),
        NodeData::Document => Some((5, None)),
    }
}

/// Visit every non-empty text node under `node` in document order, skipping
/// comments and `<script>`/`<style>` content (BS4 behaviour).
fn for_each_string<'d>(doc: &'d Document, node: NodeId, f: &mut impl FnMut(&'d str)) {
    match &doc.get(node).data {
        NodeData::Text(t) if !t.is_empty() => f(t),
        NodeData::Comment(_) => {} // skip comments
        NodeData::Element(e) if matches!(e.name.local.as_ref(), "script" | "style") => {} // skip like BS4
        _ => {
            for child in doc.children_ids(node) {
                for_each_string(doc, child, f);
            }
        }
    }
}

/// Collect up to `max` non-empty text node ids under `node`.
fn collect_string_nodes(doc: &Document, node: NodeId, out: &mut Vec<NodeId>, max: usize) {
    match &doc.get(node).data {
        NodeData::Text(t) if !t.is_empty() => out.push(node),
        NodeData::Comment(_) => {}
        _ => {
            for child in doc.children_ids(node) {
                if out.len() >= max {
                    return;
                }
                collect_string_nodes(doc, child, out, max);
            }
        }
    }
}

fn find_by_name(doc: &Document, root: NodeId, name: &str) -> Option<NodeId> {
    DescendantsPreOrder::new(doc, root).find(|&id| doc.get(id).tag_name() == Some(name))
}

// ── Module registration ───────────────────────────────────────────────────────

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyDocument>()?;
    m.add_class::<PyTag>()?;
    Ok(())
}
