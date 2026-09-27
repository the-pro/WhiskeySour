//! HTML5 serialiser.
//!
//! `serialize_node`  → outer HTML (tag + children)
//! `serialize_inner` → inner HTML (children only, no outer tag)
//! `prettify_node`   → indented outer HTML

use crate::document::Document;
use crate::node::{NodeData, NodeId};

// HTML5 void elements — must not have closing tags.
const VOID_ELEMENTS: &[&str] = &[
    "area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "param", "source",
    "track", "wbr",
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

        NodeData::Doctype(d) => {
            buf.push_str("<!DOCTYPE ");
            buf.push_str(&d.name);
            buf.push('>');
        }

        NodeData::Comment(text) => {
            buf.push_str("<!--");
            buf.push_str(text);
            buf.push_str("-->");
        }

        NodeData::ProcessingInstruction(pi) => {
            buf.push_str("<?");
            buf.push_str(&pi.target);
            buf.push(' ');
            buf.push_str(&pi.data);
            buf.push_str("?>");
        }

        NodeData::Text(text) => {
            // Check if the parent is a raw-text element.
            let raw = doc
                .get(node)
                .parent()
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

        NodeData::Element(e) => {
            let (name, attrs) = (&e.name, &e.attrs);
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
    let indent = Indent(depth * iw);

    match &doc.get(node).data {
        NodeData::Document => {
            for child in doc.children_ids(node) {
                write_pretty(doc, child, buf, depth, iw);
            }
        }

        NodeData::Doctype(d) => {
            let name = &d.name;
            indent.write(buf);
            buf.push_str("<!DOCTYPE ");
            buf.push_str(name);
            buf.push_str(">\n");
        }

        NodeData::Comment(text) => {
            indent.write(buf);
            buf.push_str("<!--");
            buf.push_str(text);
            buf.push_str("-->\n");
        }

        NodeData::Text(text) => {
            let trimmed = text.trim();
            if !trimmed.is_empty() {
                indent.write(buf);
                escape_text(trimmed, buf);
                buf.push('\n');
            }
        }

        NodeData::Element(e) => {
            let (name, attrs) = (&e.name, &e.attrs);
            let tag = name.local.as_ref();
            indent.write(buf);
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
                let n = doc.get(node);
                let inline_child = match n.first_child() {
                    Some(c) if !raw && n.first_child() == n.last_child() => {
                        matches!(doc.get(c).data, NodeData::Text(_)).then_some(c)
                    }
                    _ => None,
                };

                if let Some(child) = inline_child {
                    buf.push('>');
                    write_node(doc, child, buf);
                    buf.push_str("</");
                    buf.push_str(tag);
                    buf.push_str(">\n");
                } else {
                    buf.push_str(">\n");
                    for child in doc.children_ids(node) {
                        write_pretty(doc, child, buf, depth + 1, iw);
                    }
                    indent.write(buf);
                    buf.push_str("</");
                    buf.push_str(tag);
                    buf.push_str(">\n");
                }
            }
        }

        _ => {}
    }
}

/// `depth * indent_width` spaces, written without allocating.
#[derive(Clone, Copy)]
struct Indent(usize);

impl Indent {
    #[inline]
    fn write(self, buf: &mut String) {
        buf.extend(std::iter::repeat_n(' ', self.0));
    }
}

// ── Escaping ──────────────────────────────────────────────────────────────────

fn escape_text(s: &str, buf: &mut String) {
    escape_into(s, buf, |b| match b {
        b'&' => Some("&amp;"),
        b'<' => Some("&lt;"),
        b'>' => Some("&gt;"),
        _ => None,
    });
}

fn escape_attr(s: &str, buf: &mut String) {
    escape_into(s, buf, |b| match b {
        b'&' => Some("&amp;"),
        b'"' => Some("&quot;"),
        _ => None,
    });
}

/// Copy `s` into `buf`, replacing bytes that `entity` maps to an escape.
/// Unescaped runs are copied with a single `push_str` rather than per char.
#[inline]
fn escape_into(s: &str, buf: &mut String, entity: impl Fn(u8) -> Option<&'static str>) {
    let mut start = 0;
    for (i, &b) in s.as_bytes().iter().enumerate() {
        if let Some(rep) = entity(b) {
            // `b` is ASCII, so `start..i` and `i + 1..` are char boundaries.
            buf.push_str(&s[start..i]);
            buf.push_str(rep);
            start = i + 1;
        }
    }
    buf.push_str(&s[start..]);
}
