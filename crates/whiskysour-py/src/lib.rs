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
use pyo3::types::{PyBool, PyBytes, PyDict, PyList};
use smallvec::SmallVec;

use whiskysour_core::{
    document::Document,
    node::{Attr, NodeData, NodeId, DOCUMENT_ID},
    parser::{parse_html, parse_html_bytes, ParseOptions},
    query::{
        find_all, find_one, select, select_one, AttrFilter, AttrValueFilter, FindOptions,
        NameFilter,
    },
    serialize::{prettify_node, serialize_inner, serialize_node},
    traversal::{
        AncestorsIter, DescendantsPreOrder, NextElementsIter, NextSiblingsIter, PrevSiblingsIter,
    },
};

// ── Shared document handle ────────────────────────────────────────────────────

type DocHandle = Arc<RwLock<Document>>;

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
///   tag.parent        → _Tag | None
///   tag.children      → list[_Tag]  (all child nodes)
///   tag.contents      → list[_Tag]
///   tag.find(...)     → _Tag | None
///   tag.find_all(...) → list[_Tag]
///   tag.select(css)   → list[_Tag]
///   tag.select_one(css)→ _Tag | None
///   str(tag)          → outer HTML
///   tag.prettify()    → indented HTML
#[pyclass(name = "_Tag")]
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
        PyTag { doc: Arc::clone(&self.doc), id }
    }

    fn wrap_opt(&self, id: Option<NodeId>) -> Option<PyTag> {
        id.map(|i| self.wrap_id(i))
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
        let mut opts = FindOptions::default();
        opts.recursive = !recursive; // NOTE: our flag is inverted (true = non-recursive)
        opts.limit = limit;

        // Name filter
        if let Some(name) = name_arg {
            if name.is_none() {
                // No filter
            } else if name.is_instance_of::<PyBool>() {
                let b: bool = name.extract()?;
                if b { opts.name = Some(NameFilter::Any); }
            } else if let Ok(s) = name.extract::<String>() {
                opts.name = Some(NameFilter::Exact(s.to_ascii_lowercase()));
            } else if let Ok(lst) = name.extract::<Vec<String>>() {
                opts.name = Some(NameFilter::AnyOf(
                    lst.into_iter().map(|s| s.to_ascii_lowercase()).collect()
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

    fn extend_attr_filters(&self, filters: &mut Vec<AttrFilter>, d: &Bound<'_, PyDict>) -> PyResult<()> {
        for (k, v) in d.iter() {
            let name: String = k.extract()?;
            let name = if name == "class_" { "class".to_owned() } else { name };
            let vf = pyobj_to_attr_value(&v, &name)?;
            filters.push(AttrFilter { name, value: vf });
        }
        Ok(())
    }

    fn read_doc<F, R>(&self, f: F) -> R
    where F: FnOnce(&Document) -> R
    {
        let doc = self.doc.read().expect("document lock poisoned");
        f(&doc)
    }

    fn write_doc<F, R>(&self, f: F) -> R
    where F: FnOnce(&mut Document) -> R
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
        return Ok(if b { AttrValueFilter::Present } else { AttrValueFilter::Present });
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
    fn attrs(&self, py: Python) -> PyObject {
        let d = PyDict::new(py);
        self.read_doc(|doc| {
            if let Some(attrs) = doc.get(self.id).data.attrs() {
                for a in attrs.iter() {
                    let local = a.local_name();
                    // `class` and `rel` → list; everything else → str.
                    let val: PyObject = if local == "class" || local == "rel"
                        || local == "rev" || local == "accept-charset"
                        || local == "headers" || local == "accesskey"
                    {
                        let tokens: Vec<&str> = a.value.split_ascii_whitespace().collect();
                        PyList::new_bound(py, &tokens).into()
                    } else {
                        a.value.clone().into_py(py)
                    };
                    d.set_item(local, val).ok();
                }
            }
        });
        d.into()
    }

    fn get(&self, py: Python, key: &str, default: Option<PyObject>) -> PyObject {
        let val = self.read_doc(|doc| doc.get_attr(self.id, key).map(|v| v.to_owned()));
        match val {
            Some(v) => v.into_py(py),
            None => default.unwrap_or_else(|| py.None()),
        }
    }

    /// Single-key lookup with multi-value coercion (class/rel/etc. → list).
    /// Returns None if the attribute is absent. Much cheaper than building
    /// the full `attrs` dict when only one value is needed.
    fn get_coerced(&self, py: Python, key: &str) -> Option<PyObject> {
        const MULTI: &[&str] = &[
            "class", "rel", "rev", "accept-charset", "headers", "accesskey",
        ];
        let is_multi = MULTI.contains(&key);
        self.read_doc(|doc| {
            let attrs = doc.get(self.id).data.attrs()?;
            for a in attrs.iter() {
                if a.local_name() == key {
                    return Some(if is_multi {
                        let tokens: Vec<&str> = a.value.split_ascii_whitespace().collect();
                        PyList::new_bound(py, &tokens).into()
                    } else {
                        a.value.clone().into_py(py)
                    });
                }
            }
            None
        })
    }

    fn has_attr(&self, key: &str) -> bool {
        self.read_doc(|doc| doc.get_attr(self.id, key).is_some())
    }

    fn __getitem__(&self, py: Python, key: &str) -> PyResult<PyObject> {
        let val = self.read_doc(|doc| doc.get_attr(self.id, key).map(|v| v.to_owned()));
        match val {
            Some(v) => Ok(v.into_py(py)),
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

    /// Returns the single text child _Tag node (for NavigableString.parent/next_element support).
    #[getter]
    fn string_node(&self) -> Option<PyTag> {
        self.read_doc(|doc| {
            let mut text_nodes: Vec<NodeId> = Vec::new();
            collect_string_nodes(doc, self.id, &mut text_nodes);
            if text_nodes.len() == 1 { Some(text_nodes[0]) } else { None }
        }).map(|id| self.wrap_id(id))
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

    /// `.string` — the single text child if the element has exactly one
    /// non-empty text descendant; None otherwise.
    #[getter]
    fn string(&self) -> Option<String> {
        self.read_doc(|doc| {
            // Collect all non-empty text nodes.
            let mut texts: Vec<String> = Vec::new();
            collect_strings(doc, self.id, &mut texts);
            if texts.len() == 1 {
                Some(texts.remove(0))
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
    fn strings(&self, py: Python) -> PyObject {
        let v: Vec<String> = self.read_doc(|doc| {
            let mut out = Vec::new();
            collect_strings(doc, self.id, &mut out);
            out
        });
        v.into_py(py)
    }

    /// `.stripped_strings` — text descendants with leading/trailing whitespace stripped
    /// and empty strings removed.
    #[getter]
    fn stripped_strings(&self, py: Python) -> PyObject {
        let v: Vec<String> = self.read_doc(|doc| {
            let mut out = Vec::new();
            collect_strings(doc, self.id, &mut out);
            out.into_iter()
                .map(|s| s.trim().to_owned())
                .filter(|s| !s.is_empty())
                .collect()
        });
        v.into_py(py)
    }

    /// `.get_text(separator="", strip=False)`
    #[pyo3(signature = (separator="", strip=false))]
    fn get_text(&self, separator: &str, strip: bool) -> String {
        self.read_doc(|doc| {
            let mut texts = Vec::new();
            collect_strings(doc, self.id, &mut texts);
            if strip {
                texts.iter_mut().for_each(|s| *s = s.trim().to_owned());
                texts.retain(|s| !s.is_empty());
            }
            texts.join(separator)
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
            NodeData::Doctype { name, .. } => Some(name.clone()),
            NodeData::ProcessingInstruction { data, .. } => Some(data.clone()),
            _ => None,
        })
    }

    #[getter]
    fn parent(&self) -> Option<PyTag> {
        // Return ALL parents including Document node, so Python can wrap it as [document]
        self.read_doc(|doc| doc.get(self.id).parent).map(|p| self.wrap_id(p))
    }

    /// `.parents` — list of all ancestors including the Document.
    #[getter]
    fn parents(&self, py: Python) -> PyObject {
        let ids: Vec<NodeId> = self.read_doc(|doc| {
            AncestorsIter::new(doc, self.id).collect()
        });
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_py(py)
    }

    /// `.contents` — list of all direct children (tags + text nodes).
    #[getter]
    fn contents(&self, py: Python) -> PyObject {
        let ids: Vec<NodeId> = self.read_doc(|doc| doc.children_ids(self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_py(py)
    }

    /// `.children` — same as `.contents` but returned as a Python list (generator in bs4).
    #[getter]
    fn children(&self, py: Python) -> PyObject {
        self.contents(py)
    }

    /// `.descendants` — all descendants in pre-order.
    #[getter]
    fn descendants(&self, py: Python) -> PyObject {
        let ids: Vec<NodeId> = self.read_doc(|doc| DescendantsPreOrder::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_py(py)
    }

    #[getter]
    fn next_sibling(&self) -> Option<PyTag> {
        self.read_doc(|doc| doc.get(self.id).next_sibling).map(|i| self.wrap_id(i))
    }

    #[getter]
    fn previous_sibling(&self) -> Option<PyTag> {
        self.read_doc(|doc| doc.get(self.id).prev_sibling).map(|i| self.wrap_id(i))
    }

    #[getter]
    fn next_siblings(&self, py: Python) -> PyObject {
        let ids: Vec<NodeId> = self.read_doc(|doc| NextSiblingsIter::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_py(py)
    }

    #[getter]
    fn previous_siblings(&self, py: Python) -> PyObject {
        let ids: Vec<NodeId> = self.read_doc(|doc| PrevSiblingsIter::new(doc, self.id).collect());
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.wrap_id(i)).collect();
        tags.into_py(py)
    }

    #[getter]
    fn next_element(&self) -> Option<PyTag> {
        self.read_doc(|doc| {
            // DFS: first child, else next sibling, else ancestor's next sibling.
            doc.get(self.id).first_child
                .or_else(|| doc.get(self.id).next_sibling)
                .or_else(|| {
                    let mut cur = self.id;
                    loop {
                        match doc.get(cur).parent {
                            Some(p) => match doc.get(p).next_sibling {
                                Some(n) => break Some(n),
                                None => cur = p,
                            },
                            None => break None,
                        }
                    }
                })
        }).map(|i| self.wrap_id(i))
    }

    #[getter]
    fn previous_element(&self) -> Option<PyTag> {
        // Previous in DFS order = prev_sibling's last descendant, or parent.
        self.read_doc(|doc| {
            match doc.get(self.id).prev_sibling {
                Some(prev) => {
                    // Walk to deepest last-child of prev.
                    let mut cur = prev;
                    loop {
                        match doc.get(cur).last_child {
                            Some(lc) => cur = lc,
                            None => break,
                        }
                    }
                    Some(cur)
                }
                None => doc.get(self.id).parent,
            }
        }).map(|i| self.wrap_id(i))
    }

    // ── find / find_all ───────────────────────────────────────────────────────

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, **kwargs))]
    fn find(
        &self,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Option<PyTag>> {
        let opts = self.build_find_opts(name.as_ref(), attrs.as_ref(), recursive, 1, string, kwargs.as_ref())?;
        let result = self.read_doc(|doc| find_one(doc, self.id, &opts));
        Ok(result.map(|i| self.wrap_id(i)))
    }

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    fn find_all(
        &self,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        let mut opts = self.build_find_opts(name.as_ref(), attrs.as_ref(), recursive, limit, string, kwargs.as_ref())?;
        opts.limit = limit;
        let ids = self.read_doc(|doc| find_all(doc, self.id, &opts));
        Ok(ids.into_iter().map(|i| self.wrap_id(i)).collect())
    }

    /// Alias: `tag("p")` == `tag.find_all("p")`.
    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    fn __call__(
        &self,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        self.find_all(name, attrs, recursive, string, limit, kwargs)
    }

    // ── CSS selectors ─────────────────────────────────────────────────────────

    fn select(&self, css: &str) -> PyResult<Vec<PyTag>> {
        let ids = self.read_doc(|doc| select(doc, self.id, css))
            .map_err(|e| PyValueError::new_err(e))?;
        Ok(ids.into_iter().map(|i| self.wrap_id(i)).collect())
    }

    fn select_one(&self, css: &str) -> PyResult<Option<PyTag>> {
        let id = self.read_doc(|doc| select_one(doc, self.id, css))
            .map_err(|e| PyValueError::new_err(e))?;
        Ok(id.map(|i| self.wrap_id(i)))
    }

    // ── find_next / find_previous family ──────────────────────────────────────

    #[pyo3(signature = (name=None, string=None))]
    fn find_next(&self, name: Option<&str>, string: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            NextElementsIter::new(doc, self.id).find(|&id| {
                if let Some(n) = name {
                    doc.get(id).tag_name() == Some(n)
                } else {
                    doc.get(id).data.is_element()
                }
            })
        }).map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_next_sibling(&self, name: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            NextSiblingsIter::new(doc, self.id).find(|&id| {
                doc.get(id).data.is_element() &&
                name.map_or(true, |n| doc.get(id).tag_name() == Some(n))
            })
        }).map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_next_siblings(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            NextSiblingsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element() &&
                    name.map_or(true, |n| doc.get(id).tag_name() == Some(n))
                })
                .collect::<Vec<_>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    #[pyo3(signature = (name=None))]
    fn find_previous_sibling(&self, name: Option<&str>) -> Option<PyTag> {
        self.read_doc(|doc| {
            PrevSiblingsIter::new(doc, self.id).find(|&id| {
                doc.get(id).data.is_element() &&
                name.map_or(true, |n| doc.get(id).tag_name() == Some(n))
            })
        }).map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_previous_siblings(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            PrevSiblingsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element() &&
                    name.map_or(true, |n| doc.get(id).tag_name() == Some(n))
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
                    doc.get(p).data.is_element() &&
                    name.map_or(true, |n| doc.get(p).tag_name() == Some(n))
                })
        }).map(|i| self.wrap_id(i))
    }

    #[pyo3(signature = (name=None))]
    fn find_parents(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            AncestorsIter::new(doc, self.id)
                .take_while(|&p| !matches!(doc.get(p).data, NodeData::Document))
                .filter(|&p| {
                    doc.get(p).data.is_element() &&
                    name.map_or(true, |n| doc.get(p).tag_name() == Some(n))
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
            let elem_children: Vec<NodeId> = doc.children_ids(self_id)
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

    fn __str__(&self) -> String {
        self.read_doc(|doc| serialize_node(doc, self.id))
    }

    fn __repr__(&self) -> String {
        self.__str__()
    }

    #[pyo3(signature = (indent_width=2))]
    fn prettify(&self, indent_width: usize) -> String {
        self.read_doc(|doc| prettify_node(doc, self.id, indent_width))
    }

    fn decode(&self) -> String {
        self.__str__()
    }

    fn decode_contents(&self) -> String {
        self.read_doc(|doc| serialize_inner(doc, self.id))
    }

    #[pyo3(signature = (encoding="utf-8"))]
    fn encode<'py>(&self, py: Python<'py>, encoding: &str) -> PyResult<Bound<'py, PyBytes>> {
        let s = self.__str__();
        Ok(PyBytes::new_bound(py, s.as_bytes()))
    }

    fn encode_contents<'py>(&self, py: Python<'py>, encoding: &str) -> Bound<'py, PyBytes> {
        PyBytes::new_bound(py, self.decode_contents().as_bytes())
    }

    // ── Equality / hash ───────────────────────────────────────────────────────

    fn __eq__(&self, other: &PyTag) -> bool {
        Arc::ptr_eq(&self.doc, &other.doc) && self.id == other.id
    }

    fn __hash__(&self) -> u64 {
        use std::hash::{Hash, Hasher};
        use std::collections::hash_map::DefaultHasher;
        let mut h = DefaultHasher::new();
        self.id.hash(&mut h);
        (Arc::as_ptr(&self.doc) as u64).hash(&mut h);
        h.finish()
    }

    // ── Internal helper (Python can't call this) ──────────────────────────────
    fn wrap_node(&self, id: NodeId) -> PyTag {
        PyTag { doc: Arc::clone(&self.doc), id }
    }

    /// All elements after self in document order (for find_all_next).
    fn find_next_elements(&self, name: Option<&str>) -> Vec<PyTag> {
        let ids = self.read_doc(|doc| {
            NextElementsIter::new(doc, self.id)
                .filter(|&id| {
                    doc.get(id).data.is_element() &&
                    name.map_or(true, |n| doc.get(id).tag_name() == Some(n))
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
                    doc.get(id).data.is_element() &&
                    name.map_or(true, |n| doc.get(id).tag_name() == Some(n))
                })
                .collect::<Vec<NodeId>>()
        });
        ids.into_iter().map(|i| self.wrap_id(i)).collect()
    }

    /// Create a text node in the same document. Used by the Python shim to
    /// convert string arguments to mutation methods into _Tag objects.
    fn _make_text(&self, text: &str) -> PyTag {
        let id = self.doc.write().expect("lock").alloc(NodeData::Text(text.to_owned()));
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
    #[pyo3(signature = (markup, features="html.parser", from_encoding=None))]
    fn new(markup: Bound<'_, PyAny>, features: &str, from_encoding: Option<&str>) -> PyResult<Self> {
        let _ = features; // used by Python shim; we always use html5ever
        let opts = ParseOptions { from_encoding: from_encoding.map(|s| s.to_owned()) };
        let doc = if let Ok(s) = markup.extract::<String>() {
            parse_html(&s, opts)
        } else if let Ok(b) = markup.extract::<Vec<u8>>() {
            parse_html_bytes(&b, opts)
        } else {
            // File-like object: read it.
            let data: Vec<u8> = markup.call_method0("read")?.extract()?;
            parse_html_bytes(&data, opts)
        };
        Ok(PyDocument { doc: Arc::new(RwLock::new(doc)) })
    }

    // ── Document-level shortcuts ──────────────────────────────────────────────

    #[getter]
    fn html(&self) -> Option<PyTag> {
        let doc = self.doc.read().ok()?;
        let id = doc.children_ids(DOCUMENT_ID)
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
        let doc = self.doc.read().map_err(|_| PyValueError::new_err("lock error"))?;
        Ok(find_by_name(&doc, DOCUMENT_ID, name).map(|id| self.tag(id)))
    }

    // ── find / find_all (delegates to root _Tag) ──────────────────────────────

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, **kwargs))]
    fn find(
        &self,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Option<PyTag>> {
        self.root_tag().find(name, attrs, recursive, string, kwargs)
    }

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    fn find_all(
        &self,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        self.root_tag().find_all(name, attrs, recursive, string, limit, kwargs)
    }

    #[pyo3(signature = (name=None, attrs=None, recursive=true, string=None, limit=0, **kwargs))]
    fn __call__(
        &self,
        name: Option<Bound<'_, PyAny>>,
        attrs: Option<Bound<'_, PyDict>>,
        recursive: bool,
        string: Option<&str>,
        limit: usize,
        kwargs: Option<Bound<'_, PyDict>>,
    ) -> PyResult<Vec<PyTag>> {
        self.find_all(name, attrs, recursive, string, limit, kwargs)
    }

    fn select(&self, css: &str) -> PyResult<Vec<PyTag>> {
        self.root_tag().select(css)
    }

    fn select_one(&self, css: &str) -> PyResult<Option<PyTag>> {
        self.root_tag().select_one(css)
    }

    // ── Tree access (doc acts as a tag for navigation purposes) ───────────────

    #[getter]
    fn contents(&self, py: Python) -> PyObject {
        self.root_tag().contents(py)
    }

    #[pyo3(signature = (separator="", strip=false))]
    fn get_text(&self, separator: &str, strip: bool) -> String {
        self.root_tag().get_text(separator, strip)
    }

    /// All descendants of the document (all nodes including text/comment).
    #[getter]
    fn descendants(&self, py: Python) -> PyObject {
        let ids: Vec<NodeId> = self.doc.read()
            .map(|doc| DescendantsPreOrder::new(&doc, DOCUMENT_ID).collect())
            .unwrap_or_default();
        let tags: Vec<PyTag> = ids.into_iter().map(|i| self.tag(i)).collect();
        tags.into_py(py)
    }

    // ── new_tag / new_string ──────────────────────────────────────────────────

    #[pyo3(signature = (name, **kwargs))]
    fn new_tag(&self, name: &str, kwargs: Option<Bound<'_, PyDict>>) -> PyResult<PyTag> {
        let mut attrs: SmallVec<[Attr; 4]> = SmallVec::new();
        if let Some(kw) = kwargs {
            for (k, v) in kw.iter() {
                let key: String = k.extract()?;
                let key = if key == "class_" { "class".to_owned() } else { key };
                let val: String = v.extract()?;
                let qname = QualName::new(
                    None,
                    Namespace::from(""),
                    LocalName::from(key.as_str()),
                );
                attrs.push(Attr::new(qname, val));
            }
        }
        let qname = QualName::new(
            None,
            markup5ever::ns!(html),
            LocalName::from(name),
        );
        let data = NodeData::Element {
            name: qname,
            attrs,
            self_closing: false,
            is_template: false,
        };
        let id = self.doc.write().map_err(|_| PyValueError::new_err("lock"))?.alloc(data);
        Ok(self.tag(id))
    }

    fn new_string(&self, text: &str) -> PyTag {
        let id = self.doc.write().expect("lock").alloc(NodeData::Text(text.to_owned()));
        self.tag(id)
    }

    // ── Serialisation ─────────────────────────────────────────────────────────

    fn __str__(&self) -> String {
        self.doc.read().map(|doc| serialize_node(&doc, DOCUMENT_ID)).unwrap_or_default()
    }

    fn __repr__(&self) -> String { self.__str__() }

    #[pyo3(signature = (indent_width=2))]
    fn prettify(&self, indent_width: usize) -> String {
        self.doc.read()
            .map(|doc| prettify_node(&doc, DOCUMENT_ID, indent_width))
            .unwrap_or_default()
    }

    fn decode(&self) -> String { self.__str__() }

    #[pyo3(signature = (encoding="utf-8"))]
    fn encode<'py>(&self, py: Python<'py>, encoding: &str) -> Bound<'py, PyBytes> {
        PyBytes::new_bound(py, self.__str__().as_bytes())
    }

    // ── Internal helpers ──────────────────────────────────────────────────────

    fn root_tag(&self) -> PyTag {
        self.tag(DOCUMENT_ID)
    }
}

// ── Helpers ───────────────────────────────────────────────────────────────────

fn collect_strings(doc: &Document, node: NodeId, out: &mut Vec<String>) {
    match &doc.get(node).data {
        NodeData::Text(t) if !t.is_empty() => out.push(t.clone()),
        NodeData::Comment(_) => {} // skip comments
        NodeData::Element { name, .. }
            if matches!(name.local.as_ref(), "script" | "style") => {} // skip like BS4
        _ => {
            for child in doc.children_ids(node) {
                collect_strings(doc, child, out);
            }
        }
    }
}

fn collect_string_nodes(doc: &Document, node: NodeId, out: &mut Vec<NodeId>) {
    match &doc.get(node).data {
        NodeData::Text(t) if !t.is_empty() => out.push(node),
        NodeData::Comment(_) => {}
        _ => {
            for child in doc.children_ids(node) {
                collect_string_nodes(doc, child, out);
            }
        }
    }
}

fn find_by_name(doc: &Document, root: NodeId, name: &str) -> Option<NodeId> {
    DescendantsPreOrder::new(doc, root)
        .find(|&id| doc.get(id).tag_name() == Some(name))
}

// ── Module registration ───────────────────────────────────────────────────────

#[pymodule]
fn _core(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyDocument>()?;
    m.add_class::<PyTag>()?;
    Ok(())
}
