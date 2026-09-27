//! CSS selector matching against the flat-arena `Document`.

use super::parser::{
    AttrOp, AttrSelector, Combinator, PseudoClass, Selector, SelectorGroup, SelectorStep,
    SimpleSelector,
};
use std::cell::RefCell;
use std::collections::HashMap;
use std::hash::{BuildHasherDefault, Hasher};

use markup5ever::{local_name, LocalName};
use smallvec::SmallVec;

use crate::document::Document;
use crate::node::{NodeData, NodeId};
use crate::traversal::{child_index, child_index_from_end};

// ── Match context ─────────────────────────────────────────────────────────────

/// Per-query scratch state shared across every node tested by one
/// `select()` call.
///
/// It memoises nth-child / nth-of-type sibling indices. Without it,
/// `li:nth-child(2n)` over a list of N items rescans the sibling list for
/// every candidate (O(N²)); with it each sibling is counted at most once per
/// index kind.
#[derive(Default)]
pub(crate) struct MatchContext {
    /// (node, kind) → 1-based index. `kind` bit 0 = same-type, bit 1 = from-end.
    nth: RefCell<HashMap<(NodeId, u8), u32, BuildHasherDefault<IdHasher>>>,
}

/// Multiplicative hasher for small integer keys; SipHash is needlessly slow here.
#[derive(Default)]
pub(crate) struct IdHasher(u64);

impl Hasher for IdHasher {
    #[inline]
    fn finish(&self) -> u64 {
        self.0
    }
    #[inline]
    fn write(&mut self, bytes: &[u8]) {
        for &b in bytes {
            self.write_u64(b as u64);
        }
    }
    #[inline]
    fn write_u8(&mut self, n: u8) {
        self.write_u64(n as u64);
    }
    #[inline]
    fn write_u32(&mut self, n: u32) {
        self.write_u64(n as u64);
    }
    #[inline]
    fn write_u64(&mut self, n: u64) {
        self.0 = (self.0.rotate_left(5) ^ n).wrapping_mul(0x51_7c_c1_b7_27_22_0a_95);
    }
}

impl MatchContext {
    /// 1-based position of `node` among its element siblings, optionally
    /// restricted to siblings with the same tag name and/or counted from the end.
    ///
    /// On a miss, walks towards the edge being counted from until it reaches a
    /// sibling whose index is already known, then caches every sibling it
    /// passed. `select()` visits siblings in document order, so a full query is
    /// linear, and an early match (e.g. `select_one("li:first-child")`) only
    /// touches the siblings it needs.
    fn child_index(&self, doc: &Document, node: NodeId, same_type: bool, from_end: bool) -> usize {
        let kind = same_type as u8 | ((from_end as u8) << 1);
        if let Some(&idx) = self.nth.borrow().get(&(node, kind)) {
            return idx as usize;
        }
        let local = match doc.get(node).data.qual_name() {
            Some(name) => &name.local,
            // Selectors only test elements, but stay correct for anything else.
            _ if from_end => return child_index_from_end(doc, node, same_type),
            _ => return child_index(doc, node, same_type),
        };

        let step = |id: NodeId| {
            let n = doc.get(id);
            if from_end {
                n.next_sibling()
            } else {
                n.prev_sibling()
            }
        };

        let mut map = self.nth.borrow_mut();
        // Counted siblings from `node` outwards; the last is nearest the edge.
        let mut path: SmallVec<[NodeId; 8]> = SmallVec::new();
        path.push(node);
        let mut base = 0u32;
        let mut cur = step(node);
        while let Some(id) = cur {
            if let Some(name) = doc.get(id).data.qual_name() {
                if !same_type || name.local == *local {
                    if let Some(&idx) = map.get(&(id, kind)) {
                        base = idx;
                        break;
                    }
                    path.push(id);
                }
            }
            cur = step(id);
        }

        let len = path.len() as u32;
        for (k, &id) in path.iter().enumerate() {
            map.insert((id, kind), base + len - k as u32);
        }
        (base + len) as usize
    }
}

// ── Public entry points ───────────────────────────────────────────────────────

/// Returns `true` if `node` matches any selector in `group`.
pub fn matches_selector_group(doc: &Document, node: NodeId, group: &SelectorGroup) -> bool {
    matches_group(doc, node, group, &MatchContext::default())
}

/// Like [`matches_selector_group`], sharing memoised state across calls.
pub(crate) fn matches_group(
    doc: &Document,
    node: NodeId,
    group: &SelectorGroup,
    ctx: &MatchContext,
) -> bool {
    group.0.iter().any(|s| matches_selector(doc, node, s, ctx))
}

// ── Selector matching (right-to-left) ─────────────────────────────────────────

fn matches_selector(doc: &Document, node: NodeId, sel: &Selector, ctx: &MatchContext) -> bool {
    // Match from the rightmost step backwards.
    match_steps(doc, node, &sel.steps, sel.steps.len(), ctx)
}

fn match_steps(
    doc: &Document,
    node: NodeId,
    steps: &[SelectorStep],
    upto: usize,
    ctx: &MatchContext,
) -> bool {
    if upto == 0 {
        return true;
    }
    let step = &steps[upto - 1];

    if !matches_simple_sequence(doc, node, &step.simples, ctx) {
        return false;
    }
    if upto == 1 {
        return true;
    } // first step, no combinator to check

    match step.combinator {
        Combinator::None => true,
        Combinator::Descendant => {
            // Node must have an ancestor matching the previous chain.
            let mut cur = doc.get(node).parent();
            while let Some(p) = cur {
                if matches!(doc.get(p).data, NodeData::Document) {
                    break;
                }
                // Temporarily build a pseudo-step to test the ancestor.
                if match_steps(doc, p, steps, upto - 1, ctx) {
                    return true;
                }
                cur = doc.get(p).parent();
            }
            false
        }
        Combinator::Child => match doc.get(node).parent() {
            Some(p) if !matches!(doc.get(p).data, NodeData::Document) => {
                match_steps(doc, p, steps, upto - 1, ctx)
            }
            _ => false,
        },
        Combinator::Adjacent => {
            let mut prev = doc.get(node).prev_sibling();
            while let Some(sib) = prev {
                if doc.get(sib).data.is_element() {
                    return match_steps(doc, sib, steps, upto - 1, ctx);
                }
                prev = doc.get(sib).prev_sibling();
            }
            false
        }
        Combinator::Sibling => {
            let mut prev = doc.get(node).prev_sibling();
            while let Some(sib) = prev {
                if doc.get(sib).data.is_element() && match_steps(doc, sib, steps, upto - 1, ctx) {
                    return true;
                }
                prev = doc.get(sib).prev_sibling();
            }
            false
        }
    }
}

// ── Simple selector sequence matching ────────────────────────────────────────

fn matches_simple_sequence(
    doc: &Document,
    node: NodeId,
    simples: &[SimpleSelector],
    ctx: &MatchContext,
) -> bool {
    // Must be an element for any simple selector to match.
    if !doc.get(node).data.is_element() {
        return false;
    }
    simples.iter().all(|s| matches_simple(doc, node, s, ctx))
}

fn matches_simple(
    doc: &Document,
    node: NodeId,
    simple: &SimpleSelector,
    ctx: &MatchContext,
) -> bool {
    match simple {
        SimpleSelector::Universal => true,

        SimpleSelector::Type(name) => doc.get(node).tag_name() == Some(name.as_str()),

        SimpleSelector::Class(cls) => matches_class(doc, node, cls),

        SimpleSelector::Id(id) => attr_by_atom(doc, node, &local_name!("id")) == Some(id.as_str()),

        SimpleSelector::Attribute(attr_sel) => matches_attribute(doc, node, attr_sel),

        SimpleSelector::Pseudo(pseudo) => matches_pseudo(doc, node, pseudo, ctx),
    }
}

// ── Class matching (space-separated token list) ───────────────────────────────

fn matches_class(doc: &Document, node: NodeId, cls: &str) -> bool {
    match attr_by_atom(doc, node, &local_name!("class")) {
        Some(class_val) => class_val.split_ascii_whitespace().any(|t| t == cls),
        None => false,
    }
}

/// Attribute lookup by interned name: an atom compare per attribute instead of
/// a string compare.
#[inline]
fn attr_by_atom<'d>(doc: &'d Document, node: NodeId, name: &LocalName) -> Option<&'d str> {
    doc.get(node)
        .data
        .attrs()?
        .iter()
        .find(|a| a.name.local == *name)
        .map(|a| a.value.as_str())
}

// ── Attribute selector matching ───────────────────────────────────────────────

fn matches_attribute(doc: &Document, node: NodeId, attr: &AttrSelector) -> bool {
    let raw = match doc.get_attr(node, &attr.name) {
        Some(v) => v,
        // A missing attribute never matches, regardless of operator.
        None => return false,
    };

    if attr.op == AttrOp::Exists {
        return true;
    }

    let val = attr.value.as_str();
    let (raw_cmp, val_cmp) = if attr.case_insensitive {
        // Allocate lowercase copies only when needed.
        let r = raw.to_ascii_lowercase();
        let v = val.to_ascii_lowercase();
        // We need owned strings; this branch is uncommon so the alloc is fine.
        return attr_op_match(&attr.op, &r, &v);
    } else {
        (raw, val)
    };

    attr_op_match(&attr.op, raw_cmp, val_cmp)
}

fn attr_op_match(op: &AttrOp, raw: &str, val: &str) -> bool {
    match op {
        AttrOp::Equals => raw == val,
        AttrOp::Includes => raw.split_ascii_whitespace().any(|t| t == val),
        AttrOp::DashMatch => {
            raw == val || (raw.starts_with(val) && raw.as_bytes().get(val.len()) == Some(&b'-'))
        }
        AttrOp::Prefix => raw.starts_with(val),
        AttrOp::Suffix => raw.ends_with(val),
        AttrOp::Substring => raw.contains(val),
        AttrOp::Exists => true,
    }
}

// ── Pseudo-class matching ─────────────────────────────────────────────────────

fn matches_pseudo(doc: &Document, node: NodeId, pseudo: &PseudoClass, ctx: &MatchContext) -> bool {
    let idx = |same_type, from_end| ctx.child_index(doc, node, same_type, from_end);
    match pseudo {
        PseudoClass::Root => {
            // The <html> element is the root.
            matches!(doc.get(node).parent(), Some(p) if matches!(doc.get(p).data, NodeData::Document))
        }

        PseudoClass::Empty => !doc.children_ids(node).any(|c| {
            let n = doc.get(c);
            n.data.is_element() || matches!(&n.data, NodeData::Text(t) if !t.is_empty())
        }),

        PseudoClass::FirstChild => idx(false, false) == 1,
        PseudoClass::LastChild => idx(false, true) == 1,
        PseudoClass::OnlyChild => idx(false, false) == 1 && idx(false, true) == 1,

        PseudoClass::FirstOfType => idx(true, false) == 1,
        PseudoClass::LastOfType => idx(true, true) == 1,
        PseudoClass::OnlyOfType => idx(true, false) == 1 && idx(true, true) == 1,

        PseudoClass::NthChild(arg) => arg.matches(idx(false, false)),
        PseudoClass::NthLastChild(arg) => arg.matches(idx(false, true)),
        PseudoClass::NthOfType(arg) => arg.matches(idx(true, false)),
        PseudoClass::NthLastOfType(arg) => arg.matches(idx(true, true)),

        PseudoClass::Not(group) => !matches_group(doc, node, group, ctx),
        PseudoClass::Is(group) | PseudoClass::Where(group) => matches_group(doc, node, group, ctx),

        PseudoClass::Has(group) => {
            // :has(rel-sel) — at least one descendant matches `group`.
            use crate::traversal::DescendantsPreOrder;
            DescendantsPreOrder::new(doc, node).any(|d| matches_group(doc, d, group, ctx))
        }
    }
}
