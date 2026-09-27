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
        AncestorsIter {
            doc,
            current: doc.get(start).parent(),
        }
    }
}

impl<'a> Iterator for AncestorsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.current?;
        self.current = self.doc.get(id).parent();
        Some(id)
    }
}

// ---------------------------------------------------------------------------
// Pre-order descendants
// ---------------------------------------------------------------------------

pub struct DescendantsPreOrder<'a> {
    doc: &'a Document,
    root: NodeId,
    /// Next node to emit. Walks the existing first_child / next_sibling /
    /// parent links, so iteration never allocates.
    next: Option<NodeId>,
}

impl<'a> DescendantsPreOrder<'a> {
    /// Iterate over all descendants of `root` (not including `root` itself).
    pub fn new(doc: &'a Document, root: NodeId) -> Self {
        DescendantsPreOrder {
            doc,
            root,
            next: doc.get(root).first_child(),
        }
    }
}

impl<'a> Iterator for DescendantsPreOrder<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.next?;
        let node = self.doc.get(id);
        self.next = node.first_child().or_else(|| {
            // No children: take the nearest following sibling of `id` or of
            // one of its ancestors, without climbing past `root`.
            let mut cur = id;
            loop {
                let n = self.doc.get(cur);
                if let Some(sib) = n.next_sibling() {
                    break Some(sib);
                }
                match n.parent() {
                    Some(p) if p != self.root => cur = p,
                    _ => break None,
                }
            }
        });
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
        ElementsIter {
            inner: DescendantsPreOrder::new(doc, root),
        }
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
        NextSiblingsIter {
            doc,
            next: doc.get(start).next_sibling(),
        }
    }
}

impl<'a> Iterator for NextSiblingsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.next?;
        self.next = self.doc.get(id).next_sibling();
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
        PrevSiblingsIter {
            doc,
            prev: doc.get(start).prev_sibling(),
        }
    }
}

impl<'a> Iterator for PrevSiblingsIter<'a> {
    type Item = NodeId;
    fn next(&mut self) -> Option<NodeId> {
        let id = self.prev?;
        self.prev = self.doc.get(id).prev_sibling();
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
        let next = doc
            .get(start)
            .first_child()
            .or_else(|| doc.get(start).next_sibling())
            .or_else(|| {
                let mut cur = start;
                loop {
                    match doc.get(cur).parent() {
                        Some(p) => match doc.get(p).next_sibling() {
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
        self.next = self
            .doc
            .get(id)
            .first_child()
            .or_else(|| self.doc.get(id).next_sibling())
            .or_else(|| {
                let mut cur = id;
                loop {
                    match self.doc.get(cur).parent() {
                        Some(p) => match self.doc.get(p).next_sibling() {
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
    if doc.get(node).parent().is_none() {
        return 1;
    }
    1 + PrevSiblingsIter::new(doc, node)
        .filter(|&s| sibling_counts(doc, node, s, same_type))
        .count()
}

/// Return the 1-based index from the END among element siblings.
pub fn child_index_from_end(doc: &Document, node: NodeId, same_type: bool) -> usize {
    if doc.get(node).parent().is_none() {
        return 1;
    }
    1 + NextSiblingsIter::new(doc, node)
        .filter(|&s| sibling_counts(doc, node, s, same_type))
        .count()
}

/// Whether sibling `sib` counts towards `node`'s nth-child / nth-of-type index.
#[inline]
fn sibling_counts(doc: &Document, node: NodeId, sib: NodeId, same_type: bool) -> bool {
    match (&doc.get(sib).data, same_type) {
        (NodeData::Element(_), false) => true,
        (NodeData::Element(e), true) => {
            let name = &e.name;
            doc.get(node).data.qual_name().map(|q| &q.local) == Some(&name.local)
        }
        _ => false,
    }
}
