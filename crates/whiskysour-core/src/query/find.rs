//! `find` / `find_all` / `select` query engine.
//!
//! This layer sits between the Python bindings and the raw tree. It handles:
//!   • Fast Rust-side filtering by tag name, id, class, attribute value
//!   • CSS selector-based search (delegates to `selector::matcher`)
//!   • Recursive vs. non-recursive search
//!   • `limit` support
//!
//! Regex and callable filters are handled in Python; the bindings call
//! `iter_elements` and apply Python-side predicates.

use crate::document::Document;
use crate::node::{NodeData, NodeId};
use crate::selector::{matches_selector_group, parse_selector, SelectorGroup};
use crate::traversal::DescendantsPreOrder;

// ── Filter types (Rust-side) ──────────────────────────────────────────────────

/// How to match a tag name.
#[derive(Debug, Clone)]
pub enum NameFilter {
    /// Match any element (`find(True)`).
    Any,
    /// Exact lowercase tag name.
    Exact(String),
    /// Match any of the given names.
    AnyOf(Vec<String>),
}

/// How to match a single attribute value.
#[derive(Debug, Clone)]
pub enum AttrValueFilter {
    /// Attribute must be present.
    Present,
    /// Exact string match.
    Exact(String),
    /// Space-separated token list contains this token (for `class`).
    ContainsToken(String),
    /// `True` — any non-empty value is acceptable.
    Any,
}

/// A named attribute constraint.
#[derive(Debug, Clone)]
pub struct AttrFilter {
    pub name: String,
    pub value: AttrValueFilter,
}

/// Options passed from the Python bindings for a single `find`/`find_all` call.
#[derive(Debug, Clone, Default)]
pub struct FindOptions {
    pub name: Option<NameFilter>,
    pub attrs: Vec<AttrFilter>,
    /// Match elements whose sole text content equals this string.
    pub string: Option<String>,
    /// Do not recurse into children — only check direct children.
    pub recursive: bool,
    /// Stop after collecting this many results (0 = unlimited).
    pub limit: usize,
}

// ── Core functions ────────────────────────────────────────────────────────────

/// Run `find_all` with the given options under `root`.
pub fn find_all(doc: &Document, root: NodeId, opts: &FindOptions) -> Vec<NodeId> {
    let mut results = Vec::new();
    let limit = if opts.limit == 0 { usize::MAX } else { opts.limit };

    if opts.recursive {
        // Non-recursive: only direct children.
        for child in doc.children_ids(root) {
            if doc.get(child).data.is_element() && node_matches(doc, child, opts) {
                results.push(child);
                if results.len() >= limit { break; }
            }
        }
    } else {
        // Recursive: full pre-order descent.
        for id in DescendantsPreOrder::new(doc, root) {
            if doc.get(id).data.is_element() && node_matches(doc, id, opts) {
                results.push(id);
                if results.len() >= limit { break; }
            }
        }
    }

    results
}

/// Returns the first matching node under `root`, or `None`.
pub fn find_one(doc: &Document, root: NodeId, opts: &FindOptions) -> Option<NodeId> {
    if opts.recursive {
        doc.children_ids(root)
            .find(|&c| doc.get(c).data.is_element() && node_matches(doc, c, opts))
    } else {
        DescendantsPreOrder::new(doc, root)
            .find(|&id| doc.get(id).data.is_element() && node_matches(doc, id, opts))
    }
}

/// CSS `select()` — returns all elements matching the selector under `root`.
pub fn select(doc: &Document, root: NodeId, css: &str) -> Result<Vec<NodeId>, String> {
    let group = parse_selector(css).map_err(|e| e.0)?;
    Ok(DescendantsPreOrder::new(doc, root)
        .filter(|&id| {
            doc.get(id).data.is_element() && matches_selector_group(doc, id, &group)
        })
        .collect())
}

/// CSS `select_one()` — returns the first matching element under `root`.
pub fn select_one(doc: &Document, root: NodeId, css: &str) -> Result<Option<NodeId>, String> {
    let group = parse_selector(css).map_err(|e| e.0)?;
    Ok(DescendantsPreOrder::new(doc, root)
        .find(|&id| {
            doc.get(id).data.is_element() && matches_selector_group(doc, id, &group)
        }))
}

/// Returns `(node_id, string_content)` for all text nodes that are direct
/// children of elements — used for `find(string=...)` support from Python.
pub fn iter_text_nodes(doc: &Document, root: NodeId) -> impl Iterator<Item = (NodeId, &str)> {
    // We iterate all descendants and yield text nodes.
    // Rust's borrow checker requires we return a concrete iterator or use a helper.
    // We collect here for simplicity; a future version can make this lazy.
    DescendantsPreOrder::new(doc, root)
        .filter_map(move |id| {
            if let NodeData::Text(t) = &doc.get(id).data {
                Some((id, t.as_str()))
            } else {
                None
            }
        })
        // Safety: the doc lifetime is tied to the iterator via the closure capture
        .collect::<Vec<_>>()
        .into_iter()
}

// ── Node-level matching ───────────────────────────────────────────────────────

fn node_matches(doc: &Document, node: NodeId, opts: &FindOptions) -> bool {
    // 1. Name filter
    if let Some(ref nf) = opts.name {
        if !name_matches(doc, node, nf) { return false; }
    }

    // 2. Attribute filters
    for af in &opts.attrs {
        if !attr_filter_matches(doc, node, af) { return false; }
    }

    // 3. String filter (text content equality)
    if let Some(ref expected) = opts.string {
        let text = doc.get_text(node);
        if text.trim() != expected.as_str() { return false; }
    }

    true
}

fn name_matches(doc: &Document, node: NodeId, filter: &NameFilter) -> bool {
    let tag = match doc.get(node).tag_name() {
        Some(t) => t,
        None => return false,
    };
    match filter {
        NameFilter::Any => true,
        NameFilter::Exact(n) => tag == n.as_str(),
        NameFilter::AnyOf(names) => names.iter().any(|n| n.as_str() == tag),
    }
}

fn attr_filter_matches(doc: &Document, node: NodeId, filter: &AttrFilter) -> bool {
    // Special handling: `class` filter uses token matching.
    if filter.name == "class" {
        if let AttrValueFilter::ContainsToken(ref cls) = filter.value {
            return match doc.get_attr(node, "class") {
                Some(v) => v.split_ascii_whitespace().any(|t| t == cls.as_str()),
                None => false,
            };
        }
    }

    match &filter.value {
        AttrValueFilter::Present => doc.get_attr(node, &filter.name).is_some(),
        AttrValueFilter::Any => doc.get_attr(node, &filter.name).is_some(),
        AttrValueFilter::Exact(expected) => {
            doc.get_attr(node, &filter.name) == Some(expected.as_str())
        }
        AttrValueFilter::ContainsToken(token) => {
            match doc.get_attr(node, &filter.name) {
                Some(v) => v.split_ascii_whitespace().any(|t| t == token.as_str()),
                None => false,
            }
        }
    }
}
