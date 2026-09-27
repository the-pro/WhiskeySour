//! `Document` — owns the flat node arena and exposes tree operations.

use crate::node::{Attr, Node, NodeData, NodeId};
use markup5ever::QualName;

/// The central data structure: a flat Vec of Nodes with integer sibling/parent links.
///
/// Layout guarantees:
/// - `nodes[0]` is always the synthetic Document root.
/// - NodeId values are stable: appending new nodes never invalidates old ids.
/// - Deleted nodes are tombstoned (data = NodeData::Document with parent=None),
///   but their slot is *not* reused (keeps all existing ids valid).
#[derive(Debug)]
pub struct Document {
    pub(crate) nodes: Vec<Node>,
}

impl Document {
    // -----------------------------------------------------------------------
    // Construction
    // -----------------------------------------------------------------------

    /// Create an empty document (just the root Document node).
    pub fn new() -> Self {
        let root = Node::new(NodeData::Document);
        Document { nodes: vec![root] }
    }

    // -----------------------------------------------------------------------
    // Low-level arena
    // -----------------------------------------------------------------------

    /// Allocate a new node and return its id.
    pub fn alloc(&mut self, data: NodeData) -> NodeId {
        // u32::MAX is reserved as the "no node" link sentinel (see node.rs).
        let id = match NodeId::try_from(self.nodes.len()) {
            Ok(id) if id != NodeId::MAX => id,
            _ => panic!("document exceeds the maximum of {} nodes", NodeId::MAX),
        };
        self.nodes.push(Node::new(data));
        id
    }

    /// Borrow a node by id (panics on OOB — ids are always valid).
    #[inline]
    pub fn get(&self, id: NodeId) -> &Node {
        &self.nodes[id as usize]
    }

    /// Mutably borrow a node by id.
    #[inline]
    pub fn get_mut(&mut self, id: NodeId) -> &mut Node {
        &mut self.nodes[id as usize]
    }

    /// Total number of allocated nodes (including tombstoned ones).
    pub fn len(&self) -> usize {
        self.nodes.len()
    }

    /// A document always contains at least the synthetic root, so it is never
    /// truly empty; provided to satisfy the `len`/`is_empty` clippy pairing.
    pub fn is_empty(&self) -> bool {
        self.nodes.is_empty()
    }

    // -----------------------------------------------------------------------
    // Tree-link helpers
    // -----------------------------------------------------------------------

    /// Append `child` as the last child of `parent`.
    pub fn append_child(&mut self, parent: NodeId, child: NodeId) {
        let prev_last = self.nodes[parent as usize].last_child();

        // Link: prev_last ↔ child
        if let Some(prev) = prev_last {
            self.nodes[prev as usize].set_next_sibling(Some(child));
            self.nodes[child as usize].set_prev_sibling(Some(prev));
        } else {
            // parent had no children
            self.nodes[parent as usize].set_first_child(Some(child));
        }

        self.nodes[parent as usize].set_last_child(Some(child));
        self.nodes[child as usize].set_parent(Some(parent));
    }

    /// Prepend `child` as the first child of `parent`.
    pub fn prepend_child(&mut self, parent: NodeId, child: NodeId) {
        let prev_first = self.nodes[parent as usize].first_child();

        if let Some(next) = prev_first {
            self.nodes[next as usize].set_prev_sibling(Some(child));
            self.nodes[child as usize].set_next_sibling(Some(next));
        } else {
            self.nodes[parent as usize].set_last_child(Some(child));
        }

        self.nodes[parent as usize].set_first_child(Some(child));
        self.nodes[child as usize].set_parent(Some(parent));
    }

    /// Insert `new_node` immediately before `ref_node` in the sibling chain.
    pub fn insert_before(&mut self, ref_node: NodeId, new_node: NodeId) {
        let parent = match self.nodes[ref_node as usize].parent() {
            Some(p) => p,
            None => return, // ref_node has no parent; no-op
        };
        let prev_sib = self.nodes[ref_node as usize].prev_sibling();

        self.nodes[new_node as usize].set_parent(Some(parent));
        self.nodes[new_node as usize].set_next_sibling(Some(ref_node));
        self.nodes[new_node as usize].set_prev_sibling(prev_sib);
        self.nodes[ref_node as usize].set_prev_sibling(Some(new_node));

        match prev_sib {
            Some(prev) => self.nodes[prev as usize].set_next_sibling(Some(new_node)),
            None => self.nodes[parent as usize].set_first_child(Some(new_node)),
        }
    }

    /// Insert `new_node` immediately after `ref_node` in the sibling chain.
    pub fn insert_after(&mut self, ref_node: NodeId, new_node: NodeId) {
        let parent = match self.nodes[ref_node as usize].parent() {
            Some(p) => p,
            None => return,
        };
        let next_sib = self.nodes[ref_node as usize].next_sibling();

        self.nodes[new_node as usize].set_parent(Some(parent));
        self.nodes[new_node as usize].set_prev_sibling(Some(ref_node));
        self.nodes[new_node as usize].set_next_sibling(next_sib);
        self.nodes[ref_node as usize].set_next_sibling(Some(new_node));

        match next_sib {
            Some(next) => self.nodes[next as usize].set_prev_sibling(Some(new_node)),
            None => self.nodes[parent as usize].set_last_child(Some(new_node)),
        }
    }

    /// Detach `node` from its parent (does NOT free the node slot).
    pub fn detach(&mut self, node: NodeId) {
        let parent = match self.nodes[node as usize].take_parent() {
            Some(p) => p,
            None => return,
        };
        let prev = self.nodes[node as usize].take_prev_sibling();
        let next = self.nodes[node as usize].take_next_sibling();

        match prev {
            Some(p) => self.nodes[p as usize].set_next_sibling(next),
            None => self.nodes[parent as usize].set_first_child(next),
        }
        match next {
            Some(n) => self.nodes[n as usize].set_prev_sibling(prev),
            None => self.nodes[parent as usize].set_last_child(prev),
        }
    }

    /// Remove all children of `node`, leaving it empty.
    pub fn clear_children(&mut self, node: NodeId) {
        // Collect children first (avoid borrow conflicts)
        let children: Vec<NodeId> = self.children_ids(node).collect();
        for child in children {
            self.detach(child);
        }
    }

    // -----------------------------------------------------------------------
    // Child / sibling iterators
    // -----------------------------------------------------------------------

    /// Iterator over direct children of `node`.
    pub fn children_ids(&self, node: NodeId) -> ChildrenIter<'_> {
        ChildrenIter {
            doc: self,
            next: self.nodes[node as usize].first_child(),
        }
    }

    /// Iterator over all descendants in pre-order (depth-first).
    pub fn descendants_ids(&self, node: NodeId) -> DescendantsIter<'_> {
        DescendantsIter {
            doc: self,
            stack: self.nodes[node as usize]
                .first_child()
                .into_iter()
                .collect(),
        }
    }

    // -----------------------------------------------------------------------
    // Element attribute helpers
    // -----------------------------------------------------------------------

    /// Returns a reference to the attrs slice of an element node, or `None`.
    pub fn attrs(&self, node: NodeId) -> Option<&[Attr]> {
        self.nodes[node as usize].data.attrs()
    }

    /// Get the value of a specific attribute on an element node.
    pub fn get_attr(&self, node: NodeId, name: &str) -> Option<&str> {
        self.attrs(node)?
            .iter()
            .find(|a| a.local_name() == name)
            .map(|a| a.value.as_str())
    }

    /// Set (or add) an attribute on an element (plain string name, no namespace).
    pub fn set_attr(&mut self, node: NodeId, name: &str, value: &str) {
        if let Some(attrs) = self.nodes[node as usize].data.attrs_mut() {
            if let Some(a) = attrs.iter_mut().find(|a| a.local_name() == name) {
                a.value = value.to_owned();
            } else {
                use markup5ever::{LocalName, Namespace};
                let qname = QualName::new(None, Namespace::from(""), LocalName::from(name));
                attrs.push(Attr::new(qname, value));
            }
        }
    }

    /// Set (or add) an attribute using a full `QualName` (used by the parser).
    pub fn set_attr_qual(&mut self, node: NodeId, name: QualName, value: String) {
        if let Some(attrs) = self.nodes[node as usize].data.attrs_mut() {
            if let Some(a) = attrs.iter_mut().find(|a| a.name.local == name.local) {
                a.value = value;
            } else {
                attrs.push(Attr::new(name, value));
            }
        }
    }

    /// Remove an attribute from an element.
    pub fn remove_attr(&mut self, node: NodeId, name: &str) {
        if let Some(attrs) = self.nodes[node as usize].data.attrs_mut() {
            attrs.retain(|a| a.local_name() != name);
        }
    }

    // -----------------------------------------------------------------------
    // Text helpers
    // -----------------------------------------------------------------------

    /// Collect all text content beneath `node` (including nested text nodes).
    pub fn get_text(&self, node: NodeId) -> String {
        let mut buf = String::new();
        self.collect_text(node, &mut buf);
        buf
    }

    fn collect_text(&self, node: NodeId, buf: &mut String) {
        match &self.nodes[node as usize].data {
            NodeData::Text(t) => buf.push_str(t),
            _ => {
                // Recurse into all children (includes Element, Script, Style, etc.)
                for child in self.children_ids(node) {
                    self.collect_text(child, buf);
                }
            }
        }
    }

    // -----------------------------------------------------------------------
    // Coalesce adjacent text nodes (html5ever can produce them)
    // -----------------------------------------------------------------------

    /// Merge adjacent sibling Text nodes under `parent`.
    pub fn coalesce_text(&mut self, parent: NodeId) {
        let children: Vec<NodeId> = self.children_ids(parent).collect();
        let mut i = 0;
        while i < children.len() {
            if let NodeData::Text(t1) = &self.nodes[children[i] as usize].data {
                let mut merged = t1.clone();
                let mut j = i + 1;
                while j < children.len() {
                    if let NodeData::Text(t2) = &self.nodes[children[j] as usize].data {
                        merged.push_str(&t2.clone());
                        j += 1;
                    } else {
                        break;
                    }
                }
                if j > i + 1 {
                    // Remove nodes i+1 .. j-1
                    for &child in &children[i + 1..j] {
                        self.detach(child);
                    }
                    if let NodeData::Text(t) = &mut self.nodes[children[i] as usize].data {
                        *t = merged;
                    }
                }
                i = j;
            } else {
                i += 1;
            }
        }
    }
}

impl Default for Document {
    fn default() -> Self {
        Self::new()
    }
}

// -----------------------------------------------------------------------
// Iterator types
// -----------------------------------------------------------------------

pub struct ChildrenIter<'a> {
    doc: &'a Document,
    next: Option<NodeId>,
}

impl<'a> Iterator for ChildrenIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.next?;
        self.next = self.doc.nodes[id as usize].next_sibling();
        Some(id)
    }
}

pub struct DescendantsIter<'a> {
    doc: &'a Document,
    stack: Vec<NodeId>,
}

impl<'a> Iterator for DescendantsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.stack.pop()?;
        // Push children in reverse so we visit them left-to-right
        let children: Vec<NodeId> = self.doc.children_ids(id).collect();
        for child in children.into_iter().rev() {
            self.stack.push(child);
        }
        Some(id)
    }
}
