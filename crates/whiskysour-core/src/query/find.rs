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

use markup5ever::LocalName;

use crate::document::Document;
use crate::node::NodeId;
use crate::selector::{matches_group, parse_selector_cached, MatchContext};
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
    /// Attribute must be present (`attr=True`).
    Present,
    /// Attribute must be absent (`attr=False`) — bs4 semantics.
    Absent,
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
    let limit = if opts.limit == 0 {
        usize::MAX
    } else {
        opts.limit
    };
    let q = CompiledFind::new(opts);
    let matches = |&id: &NodeId| q.matches(doc, id);

    if opts.recursive {
        // Non-recursive: only direct children.
        doc.children_ids(root).filter(matches).take(limit).collect()
    } else {
        // Recursive: full pre-order descent.
        DescendantsPreOrder::new(doc, root)
            .filter(matches)
            .take(limit)
            .collect()
    }
}

/// Returns the first matching node under `root`, or `None`.
pub fn find_one(doc: &Document, root: NodeId, opts: &FindOptions) -> Option<NodeId> {
    let q = CompiledFind::new(opts);
    if opts.recursive {
        doc.children_ids(root).find(|&c| q.matches(doc, c))
    } else {
        DescendantsPreOrder::new(doc, root).find(|&id| q.matches(doc, id))
    }
}

/// CSS `select()` — returns all elements matching the selector under `root`.
pub fn select(doc: &Document, root: NodeId, css: &str) -> Result<Vec<NodeId>, String> {
    select_limit(doc, root, css, 0)
}

/// CSS `select()` that stops after `limit` matches (0 = unlimited).
pub fn select_limit(
    doc: &Document,
    root: NodeId,
    css: &str,
    limit: usize,
) -> Result<Vec<NodeId>, String> {
    let group = parse_selector_cached(css).map_err(|e| e.0)?;
    let ctx = MatchContext::default();
    let limit = if limit == 0 { usize::MAX } else { limit };
    Ok(DescendantsPreOrder::new(doc, root)
        .filter(|&id| doc.get(id).data.is_element() && matches_group(doc, id, &group, &ctx))
        .take(limit)
        .collect())
}

/// CSS `select_one()` — returns the first matching element under `root`.
pub fn select_one(doc: &Document, root: NodeId, css: &str) -> Result<Option<NodeId>, String> {
    let group = parse_selector_cached(css).map_err(|e| e.0)?;
    let ctx = MatchContext::default();
    Ok(DescendantsPreOrder::new(doc, root)
        .find(|&id| doc.get(id).data.is_element() && matches_group(doc, id, &group, &ctx)))
}

// ── Node-level matching ───────────────────────────────────────────────────────

/// `FindOptions` with tag and attribute names resolved to interned atoms once
/// per query, so each element check compares atoms (an integer compare)
/// instead of strings.
struct CompiledFind<'o> {
    name: Option<CompiledName>,
    attrs: Vec<(LocalName, &'o AttrValueFilter)>,
    string: Option<&'o str>,
}

enum CompiledName {
    Any,
    Exact(LocalName),
    AnyOf(Vec<LocalName>),
}

impl<'o> CompiledFind<'o> {
    fn new(opts: &'o FindOptions) -> Self {
        CompiledFind {
            name: opts.name.as_ref().map(|nf| match nf {
                NameFilter::Any => CompiledName::Any,
                NameFilter::Exact(n) => CompiledName::Exact(LocalName::from(n.as_str())),
                NameFilter::AnyOf(ns) => {
                    CompiledName::AnyOf(ns.iter().map(|n| LocalName::from(n.as_str())).collect())
                }
            }),
            attrs: opts
                .attrs
                .iter()
                .map(|af| (LocalName::from(af.name.as_str()), &af.value))
                .collect(),
            string: opts.string.as_deref(),
        }
    }

    fn matches(&self, doc: &Document, node: NodeId) -> bool {
        let (tag, attrs) = match doc.get(node).data.as_element() {
            Some(e) => (&e.name.local, &e.attrs),
            None => return false,
        };

        // 1. Name filter
        match &self.name {
            None | Some(CompiledName::Any) => {}
            Some(CompiledName::Exact(n)) => {
                if tag != n {
                    return false;
                }
            }
            Some(CompiledName::AnyOf(ns)) => {
                if !ns.contains(tag) {
                    return false;
                }
            }
        }

        // 2. Attribute filters
        for (name, filter) in &self.attrs {
            let value = attrs
                .iter()
                .find(|a| a.name.local == *name)
                .map(|a| a.value.as_str());
            let ok = match filter {
                AttrValueFilter::Present | AttrValueFilter::Any => value.is_some(),
                AttrValueFilter::Absent => value.is_none(),
                AttrValueFilter::Exact(expected) => value == Some(expected.as_str()),
                // Space-separated token list (e.g. `class`) contains the token.
                AttrValueFilter::ContainsToken(token) => {
                    value.is_some_and(|v| v.split_ascii_whitespace().any(|t| t == token))
                }
            };
            if !ok {
                return false;
            }
        }

        // 3. String filter (text content equality)
        if let Some(expected) = self.string {
            if doc.get_text(node).trim() != expected {
                return false;
            }
        }

        true
    }
}
