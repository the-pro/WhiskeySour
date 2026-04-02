//! Tree traversal iterators over a borrowed `Document`.

use crate::document::Document;
use crate::node::{NodeData, NodeId};

// ---------------------------------------------------------------------------
// Ancestors (parent chain up to the Document root)
// ---------------------------------------------------------------------------

pub struct AncestorsIter<'a> {
    doc: &'a Document,
    current: Option<NodeId>,
}

impl<'a> AncestorsIter<'a> {
    pub fn new(doc: &'a Document, start: NodeId) -> Self {
        AncestorsIter { doc, current: doc.get(start).parent }
    }
}

impl<'a> Iterator for AncestorsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.current?;
        self.current = self.doc.get(id).parent;
        Some(id)
    }
}

// ---------------------------------------------------------------------------
// Pre-order descendants
// ---------------------------------------------------------------------------

pub struct DescendantsPreOrder<'a> {
    doc: &'a Document,
    /// Stack of nodes to visit (last = next to emit).
    stack: Vec<NodeId>,
}

impl<'a> DescendantsPreOrder<'a> {
    /// Iterate over all descendants of `root` (not including `root` itself).
    pub fn new(doc: &'a Document, root: NodeId) -> Self {
        let stack: Vec<NodeId> = doc.children_ids(root).collect::<Vec<_>>()
            .into_iter().rev().collect();
        DescendantsPreOrder { doc, stack }
    }
}

impl<'a> Iterator for DescendantsPreOrder<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.stack.pop()?;
        // Push children in reverse order so left-most is popped next.
        let children: Vec<NodeId> = self.doc.children_ids(id).collect::<Vec<_>>()
            .into_iter().rev().collect();
        self.stack.extend(children);
        Some(id)
    }
}

// ---------------------------------------------------------------------------
// Element-only descendant iterator (skips Text, Comment, etc.)
// ---------------------------------------------------------------------------

pub struct ElementsIter<'a> {
    inner: DescendantsPreOrder<'a>,
}

impl<'a> ElementsIter<'a> {
    pub fn new(doc: &'a Document, root: NodeId) -> Self {
        ElementsIter { inner: DescendantsPreOrder::new(doc, root) }
    }
}

impl<'a> Iterator for ElementsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        loop {
            let id = self.inner.next()?;
            if self.inner.doc.get(id).data.is_element() {
                return Some(id);
            }
        }
    }
}

// ---------------------------------------------------------------------------
// Next siblings
// ---------------------------------------------------------------------------

pub struct NextSiblingsIter<'a> {
    doc: &'a Document,
    next: Option<NodeId>,
}

impl<'a> NextSiblingsIter<'a> {
    pub fn new(doc: &'a Document, start: NodeId) -> Self {
        NextSiblingsIter { doc, next: doc.get(start).next_sibling }
    }
}

impl<'a> Iterator for NextSiblingsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.next?;
        self.next = self.doc.get(id).next_sibling;
        Some(id)
    }
}

// ---------------------------------------------------------------------------
// Previous siblings (yielded nearest-first)
// ---------------------------------------------------------------------------

pub struct PrevSiblingsIter<'a> {
    doc: &'a Document,
    prev: Option<NodeId>,
}

impl<'a> PrevSiblingsIter<'a> {
    pub fn new(doc: &'a Document, start: NodeId) -> Self {
        PrevSiblingsIter { doc, prev: doc.get(start).prev_sibling }
    }
}

impl<'a> Iterator for PrevSiblingsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.prev?;
        self.prev = self.doc.get(id).prev_sibling;
        Some(id)
    }
}

// ---------------------------------------------------------------------------
// next_element: DFS order (enters children before moving to next sibling)
// ---------------------------------------------------------------------------

pub struct NextElementsIter<'a> {
    doc: &'a Document,
    next: Option<NodeId>,
}

impl<'a> NextElementsIter<'a> {
    pub fn new(doc: &'a Document, start: NodeId) -> Self {
        // start at the first child, or next sibling, or parent's next sibling
        let next = doc.get(start).first_child
            .or_else(|| doc.get(start).next_sibling)
            .or_else(|| {
                let mut cur = start;
                loop {
                    match doc.get(cur).parent {
                        Some(p) => match doc.get(p).next_sibling {
                            Some(n) => break Some(n),
                            None => cur = p,
                        },
                        None => break None,
                    }
                }
            });
        NextElementsIter { doc, next }
    }
}

impl<'a> Iterator for NextElementsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.next?;
        // Advance: try first child, then next sibling, then ancestor's next sibling
        self.next = self.doc.get(id).first_child
            .or_else(|| self.doc.get(id).next_sibling)
            .or_else(|| {
                let mut cur = id;
                loop {
                    match self.doc.get(cur).parent {
                        Some(p) => match self.doc.get(p).next_sibling {
                            Some(n) => break Some(n),
                            None => cur = p,
                        },
                        None => break None,
                    }
                }
            });
        Some(id)
    }
}

// ---------------------------------------------------------------------------
// nth-child helpers (used by selector matcher)
// ---------------------------------------------------------------------------

/// Return the 1-based index of `node` among its element siblings (of the same type if `same_type` is set).
pub fn child_index(doc: &Document, node: NodeId, same_type: bool) -> usize {
    let parent = match doc.get(node).parent {
        Some(p) => p,
        None => return 1,
    };
    let tag = doc.get(node).tag_name().map(|s| s.to_owned());
    let mut idx = 0usize;
    for sib in doc.children_ids(parent) {
        if !doc.get(sib).data.is_element() {
            continue;
        }
        if same_type {
            if doc.get(sib).tag_name() != tag.as_deref() {
                continue;
            }
        }
        idx += 1;
        if sib == node {
            return idx;
        }
    }
    idx
}

/// Return the 1-based index from the END among element siblings.
pub fn child_index_from_end(doc: &Document, node: NodeId, same_type: bool) -> usize {
    let parent = match doc.get(node).parent {
        Some(p) => p,
        None => return 1,
    };
    let tag = doc.get(node).tag_name().map(|s| s.to_owned());
    let total = doc.children_ids(parent)
        .filter(|&s| {
            if !doc.get(s).data.is_element() { return false; }
            if same_type { doc.get(s).tag_name() == tag.as_deref() } else { true }
        })
        .count();
    let from_start = child_index(doc, node, same_type);
    total + 1 - from_start
}
