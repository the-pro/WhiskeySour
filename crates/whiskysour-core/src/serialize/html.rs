//! HTML5 serialiser.
//!
//! `serialize_node`  → outer HTML (tag + children)
//! `serialize_inner` → inner HTML (children only, no outer tag)
//! `prettify_node`   → indented outer HTML

use crate::document::Document;
use crate::node::{NodeData, NodeId, DOCUMENT_ID};

// HTML5 void elements — must not have closing tags.
const VOID_ELEMENTS: &[&str] = &[
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
];

// Raw-text elements whose content must NOT be escaped.
const RAW_TEXT: &[&str] = &["script", "style"];

// ── Public functions ──────────────────────────────────────────────────────────

/// Serialise `node` to an HTML string (includes the node's own tag).
pub fn serialize_node(doc: &Document, node: NodeId) -> String {
    let mut buf = String::new();
    write_node(doc, node, &mut buf);
    buf
}

/// Serialise only the children of `node` (innerHTML).
pub fn serialize_inner(doc: &Document, node: NodeId) -> String {
    let mut buf = String::new();
    write_children(doc, node, &mut buf);
    buf
}

/// Indented outer HTML.
pub fn prettify_node(doc: &Document, node: NodeId, indent_width: usize) -> String {
    let mut buf = String::new();
    write_pretty(doc, node, &mut buf, 0, indent_width);
    buf
}

// ── Flat serialiser ───────────────────────────────────────────────────────────

fn write_node(doc: &Document, node: NodeId, buf: &mut String) {
    match &doc.get(node).data {
        NodeData::Document => write_children(doc, node, buf),

        NodeData::Doctype { name, .. } => {
            buf.push_str("<!DOCTYPE ");
            buf.push_str(name);
            buf.push('>');
        }

        NodeData::Comment(text) => {
            buf.push_str("<!--");
            buf.push_str(text);
            buf.push_str("-->");
        }

        NodeData::ProcessingInstruction { target, data } => {
            buf.push_str("<?");
            buf.push_str(target);
            buf.push(' ');
            buf.push_str(data);
            buf.push_str("?>");
        }

        NodeData::Text(text) => {
            // Check if the parent is a raw-text element.
            let raw = doc.get(node).parent
                .and_then(|p| doc.get(p).tag_name())
                .map(|t| RAW_TEXT.contains(&t))
                .unwrap_or(false);
            if raw {
                buf.push_str(text);
            } else {
                escape_text(text, buf);
            }
        }

        NodeData::CData(text) => {
            buf.push_str("<![CDATA[");
            buf.push_str(text);
            buf.push_str("]]>");
        }

        NodeData::Element { name, attrs, .. } => {
            let tag = name.local.as_ref();
            buf.push('<');
            buf.push_str(tag);

            for attr in attrs.iter() {
                buf.push(' ');
                buf.push_str(attr.local_name());
                buf.push_str("=\"");
                escape_attr(&attr.value, buf);
                buf.push('"');
            }

            if VOID_ELEMENTS.contains(&tag) {
                buf.push('>');
            } else {
                buf.push('>');
                write_children(doc, node, buf);
                buf.push_str("</");
                buf.push_str(tag);
                buf.push('>');
            }
        }
    }
}

fn write_children(doc: &Document, node: NodeId, buf: &mut String) {
    for child in doc.children_ids(node) {
        write_node(doc, child, buf);
    }
}

// ── Pretty printer ────────────────────────────────────────────────────────────

fn write_pretty(doc: &Document, node: NodeId, buf: &mut String, depth: usize, iw: usize) {
    let indent = " ".repeat(depth * iw);

    match &doc.get(node).data {
        NodeData::Document => {
            for child in doc.children_ids(node) {
                write_pretty(doc, child, buf, depth, iw);
            }
        }

        NodeData::Doctype { name, .. } => {
            buf.push_str(&indent);
            buf.push_str("<!DOCTYPE ");
            buf.push_str(name);
            buf.push_str(">\n");
        }

        NodeData::Comment(text) => {
            buf.push_str(&indent);
            buf.push_str("<!--");
            buf.push_str(text);
            buf.push_str("-->\n");
        }

        NodeData::Text(text) => {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                buf.push_str(&indent);
                escape_text(trimmed, buf);
                buf.push('\n');
            }
        }

        NodeData::Element { name, attrs, .. } => {
            let tag = name.local.as_ref();
            buf.push_str(&indent);
            buf.push('<');
            buf.push_str(tag);
            for attr in attrs.iter() {
                buf.push(' ');
                buf.push_str(attr.local_name());
                buf.push_str("=\"");
                escape_attr(&attr.value, buf);
                buf.push('"');
            }

            if VOID_ELEMENTS.contains(&tag) {
                buf.push_str(">\n");
            } else {
                let raw = RAW_TEXT.contains(&tag);
                // Inline if single text child and not raw-text element.
                let children: Vec<NodeId> = doc.children_ids(node).collect();
                let inline = !raw
                    && children.len() == 1
                    && matches!(doc.get(children[0]).data, NodeData::Text(_));

                if inline {
                    buf.push('>');
                    write_node(doc, children[0], buf);
                    buf.push_str("</");
                    buf.push_str(tag);
                    buf.push_str(">\n");
                } else {
                    buf.push_str(">\n");
                    for child in &children {
                        write_pretty(doc, *child, buf, depth + 1, iw);
                    }
                    buf.push_str(&indent);
                    buf.push_str("</");
                    buf.push_str(tag);
                    buf.push_str(">\n");
                }
            }
        }

        _ => {}
    }
}

// ── Escaping ──────────────────────────────────────────────────────────────────

fn escape_text(s: &str, buf: &mut String) {
    for c in s.chars() {
        match c {
            '&'  => buf.push_str("&amp;"),
            '<'  => buf.push_str("&lt;"),
            '>'  => buf.push_str("&gt;"),
            _    => buf.push(c),
        }
    }
}

fn escape_attr(s: &str, buf: &mut String) {
    for c in s.chars() {
        match c {
            '&'  => buf.push_str("&amp;"),
            '"'  => buf.push_str("&quot;"),
            _    => buf.push(c),
        }
    }
}
