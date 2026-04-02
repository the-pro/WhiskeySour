//! CSS selector matching against the flat-arena `Document`.

use crate::document::Document;
use crate::node::{NodeData, NodeId};
use crate::traversal::{child_index, child_index_from_end};
use super::parser::{
    AttrOp, AttrSelector, Combinator, NthArg, PseudoClass,
    Selector, SelectorGroup, SelectorStep, SimpleSelector,
};

// ── Public entry points ───────────────────────────────────────────────────────

/// Returns `true` if `node` matches any selector in `group`.
pub fn matches_selector_group(doc: &Document, node: NodeId, group: &SelectorGroup) -> bool {
    group.0.iter().any(|s| matches_selector(doc, node, s))
}

// ── Selector matching (right-to-left) ─────────────────────────────────────────

fn matches_selector(doc: &Document, node: NodeId, sel: &Selector) -> bool {
    // Match from the rightmost step backwards.
    match_steps(doc, node, &sel.steps, sel.steps.len())
}

fn match_steps(doc: &Document, node: NodeId, steps: &[SelectorStep], upto: usize) -> bool {
    if upto == 0 { return true; }
    let step = &steps[upto - 1];

    if !matches_simple_sequence(doc, node, &step.simples) {
        return false;
    }
    if upto == 1 { return true; } // first step, no combinator to check

    let prev_step = &steps[upto - 2];
    match step.combinator {
        Combinator::None => true,
        Combinator::Descendant => {
            // Node must have an ancestor matching the previous chain.
            let mut cur = doc.get(node).parent;
            while let Some(p) = cur {
                if matches!(doc.get(p).data, NodeData::Document) { break; }
                // Temporarily build a pseudo-step to test the ancestor.
                if match_steps(doc, p, steps, upto - 1) { return true; }
                cur = doc.get(p).parent;
            }
            false
        }
        Combinator::Child => {
            match doc.get(node).parent {
                Some(p) if !matches!(doc.get(p).data, NodeData::Document) => {
                    match_steps(doc, p, steps, upto - 1)
                }
                _ => false,
            }
        }
        Combinator::Adjacent => {
            let mut prev = doc.get(node).prev_sibling;
            while let Some(sib) = prev {
                if doc.get(sib).data.is_element() {
                    return match_steps(doc, sib, steps, upto - 1);
                }
                prev = doc.get(sib).prev_sibling;
            }
            false
        }
        Combinator::Sibling => {
            let mut prev = doc.get(node).prev_sibling;
            while let Some(sib) = prev {
                if doc.get(sib).data.is_element() {
                    if match_steps(doc, sib, steps, upto - 1) { return true; }
                }
                prev = doc.get(sib).prev_sibling;
            }
            false
        }
    }
}

// ── Simple selector sequence matching ────────────────────────────────────────

fn matches_simple_sequence(doc: &Document, node: NodeId, simples: &[SimpleSelector]) -> bool {
    // Must be an element for any simple selector to match.
    if !doc.get(node).data.is_element() { return false; }
    simples.iter().all(|s| matches_simple(doc, node, s))
}

fn matches_simple(doc: &Document, node: NodeId, simple: &SimpleSelector) -> bool {
    match simple {
        SimpleSelector::Universal => true,

        SimpleSelector::Type(name) => {
            doc.get(node).tag_name() == Some(name.as_str())
        }

        SimpleSelector::Class(cls) => {
            matches_class(doc, node, cls)
        }

        SimpleSelector::Id(id) => {
            doc.get_attr(node, "id") == Some(id.as_str())
        }

        SimpleSelector::Attribute(attr_sel) => {
            matches_attribute(doc, node, attr_sel)
        }

        SimpleSelector::Pseudo(pseudo) => {
            matches_pseudo(doc, node, pseudo)
        }
    }
}

// ── Class matching (space-separated token list) ───────────────────────────────

fn matches_class(doc: &Document, node: NodeId, cls: &str) -> bool {
    match doc.get_attr(node, "class") {
        Some(class_val) => class_val.split_ascii_whitespace().any(|t| t == cls),
        None => false,
    }
}

// ── Attribute selector matching ───────────────────────────────────────────────

fn matches_attribute(doc: &Document, node: NodeId, attr: &AttrSelector) -> bool {
    let raw = match doc.get_attr(node, &attr.name) {
        Some(v) => v,
        None => return attr.op == AttrOp::Exists && false,
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
        AttrOp::Equals     => raw == val,
        AttrOp::Includes   => raw.split_ascii_whitespace().any(|t| t == val),
        AttrOp::DashMatch  => raw == val || raw.starts_with(&format!("{}-", val)),
        AttrOp::Prefix     => raw.starts_with(val),
        AttrOp::Suffix     => raw.ends_with(val),
        AttrOp::Substring  => raw.contains(val),
        AttrOp::Exists     => true,
    }
}

// ── Pseudo-class matching ─────────────────────────────────────────────────────

fn matches_pseudo(doc: &Document, node: NodeId, pseudo: &PseudoClass) -> bool {
    match pseudo {
        PseudoClass::Root => {
            // The <html> element is the root.
            matches!(doc.get(node).parent, Some(p) if matches!(doc.get(p).data, NodeData::Document))
        }

        PseudoClass::Empty => {
            !doc.children_ids(node).any(|c| {
                let n = doc.get(c);
                n.data.is_element() || matches!(&n.data, NodeData::Text(t) if !t.is_empty())
            })
        }

        PseudoClass::FirstChild => child_index(doc, node, false) == 1,
        PseudoClass::LastChild  => child_index_from_end(doc, node, false) == 1,
        PseudoClass::OnlyChild  => {
            child_index(doc, node, false) == 1 && child_index_from_end(doc, node, false) == 1
        }

        PseudoClass::FirstOfType => child_index(doc, node, true) == 1,
        PseudoClass::LastOfType  => child_index_from_end(doc, node, true) == 1,
        PseudoClass::OnlyOfType  => {
            child_index(doc, node, true) == 1 && child_index_from_end(doc, node, true) == 1
        }

        PseudoClass::NthChild(arg)     => arg.matches(child_index(doc, node, false)),
        PseudoClass::NthLastChild(arg) => arg.matches(child_index_from_end(doc, node, false)),
        PseudoClass::NthOfType(arg)    => arg.matches(child_index(doc, node, true)),
        PseudoClass::NthLastOfType(arg)=> arg.matches(child_index_from_end(doc, node, true)),

        PseudoClass::Not(group) => !matches_selector_group(doc, node, group),
        PseudoClass::Is(group) | PseudoClass::Where(group) => {
            matches_selector_group(doc, node, group)
        }

        PseudoClass::Has(group) => {
            // :has(rel-sel) — at least one descendant matches `group`.
            use crate::traversal::DescendantsPreOrder;
            DescendantsPreOrder::new(doc, node)
                .any(|d| matches_selector_group(doc, d, group))
        }
    }
}
