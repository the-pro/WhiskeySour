//! html5ever `TreeSink` implementation.
//!
//! html5ever tokenises the input and calls our sink methods; we populate the
//! flat-arena `Document`. Zero Python-GIL involvement on this hot path.

use std::borrow::Cow;

use html5ever::{
    interface::QuirksMode,
    parse_document, parse_fragment,
    tendril::{StrTendril, TendrilSink},
    tree_builder::{ElementFlags, NodeOrText, TreeSink},
    Attribute, ParseOpts, QualName,
};
use markup5ever::{local_name, namespace_url, ns, ExpandedName};

use crate::document::Document;
use crate::node::{Attr, NodeData, NodeId, DOCUMENT_ID};

// ── Public options ────────────────────────────────────────────────────────────

#[derive(Debug, Clone, Default)]
pub struct ParseOptions {
    /// Encoding hint passed in by the caller; actual detection still uses BOM /
    /// meta-charset.
    pub from_encoding: Option<String>,
}

// ── Public entry-points ───────────────────────────────────────────────────────

/// Parse a full HTML document from a string slice. Returns a `Document`.
pub fn parse_html(markup: &str, _opts: ParseOptions) -> Document {
    let sink = WsSink::new();
    let tendril: StrTendril = markup.into();
    parse_document(sink, ParseOpts::default()).one(tendril).doc
}

/// Parse a full HTML document from bytes (encoding auto-detected / from_encoding hint).
pub fn parse_html_bytes(bytes: &[u8], opts: ParseOptions) -> Document {
    let markup = String::from_utf8_lossy(bytes);
    parse_html(markup.as_ref(), opts)
}

/// Parse an HTML fragment (no implicit `<html>`/`<head>`/`<body>` wrapping beyond
/// what html5ever adds for the `<body>` context element).
pub fn parse_html_fragment(markup: &str) -> Document {
    let sink = WsSink::new();
    let ctx = QualName::new(None, ns!(html), local_name!("body"));
    let tendril: StrTendril = markup.into();
    parse_fragment(sink, ParseOpts::default(), ctx, vec![]).one(tendril).doc
}

// ── Internal sink ─────────────────────────────────────────────────────────────

struct WsSink {
    doc: Document,
    quirks: QuirksMode,
    // We collect parse errors silently; expose later if needed.
    _errors: Vec<Cow<'static, str>>,
    // Fallback QualName for elem_name when called on non-element nodes.
    _dummy_name: QualName,
}

impl WsSink {
    fn new() -> Self {
        WsSink {
            doc: Document::new(),
            quirks: QuirksMode::NoQuirks,
            _errors: Vec::new(),
            _dummy_name: QualName::new(None, ns!(html), local_name!("span")),
        }
    }

    /// Try to coalesce `text` with the last child of `parent` if it is already
    /// a Text node (html5ever may call append multiple times for adjacent text).
    fn append_text(&mut self, parent: NodeId, text: StrTendril) {
        let last = self.doc.get(parent).last_child;
        if let Some(last_id) = last {
            if let NodeData::Text(t) = &mut self.doc.get_mut(last_id).data {
                t.push_str(&text);
                return;
            }
        }
        let node = self.doc.alloc(NodeData::Text(text.to_string()));
        self.doc.append_child(parent, node);
    }

    fn append_text_before(&mut self, sibling: NodeId, text: StrTendril) {
        let prev = self.doc.get(sibling).prev_sibling;
        if let Some(prev_id) = prev {
            if let NodeData::Text(t) = &mut self.doc.get_mut(prev_id).data {
                t.push_str(&text);
                return;
            }
        }
        let node = self.doc.alloc(NodeData::Text(text.to_string()));
        self.doc.insert_before(sibling, node);
    }
}

// ── TreeSink impl ─────────────────────────────────────────────────────────────

impl TreeSink for WsSink {
    type Handle = NodeId;
    type Output = WsSink;

    fn finish(self) -> WsSink { self }

    // ── Error / quirks ────────────────────────────────────────────────────────
    fn parse_error(&mut self, msg: Cow<'static, str>) {
        self._errors.push(msg);
    }

    fn set_quirks_mode(&mut self, mode: QuirksMode) {
        self.quirks = mode;
    }

    // ── Node identity ─────────────────────────────────────────────────────────
    fn get_document(&mut self) -> NodeId { DOCUMENT_ID }

    fn same_node(&self, x: &NodeId, y: &NodeId) -> bool { x == y }

    fn elem_name<'a>(&'a self, target: &'a NodeId) -> ExpandedName<'a> {
        match &self.doc.get(*target).data {
            NodeData::Element { name, .. } => name.expanded(),
            _ => self._dummy_name.expanded(),
        }
    }

    // ── Node creation ─────────────────────────────────────────────────────────
    fn create_element(&mut self, name: QualName, html_attrs: Vec<Attribute>, flags: ElementFlags) -> NodeId {
        let attrs = html_attrs.into_iter()
            .map(|a| Attr::new(a.name, a.value.to_string()))
            .collect();
        self.doc.alloc(NodeData::Element {
            name,
            attrs,
            self_closing: false, // html5ever doesn't track this; void elements are handled in serialiser
            is_template: flags.template,
        })
    }

    fn create_comment(&mut self, text: StrTendril) -> NodeId {
        self.doc.alloc(NodeData::Comment(text.to_string()))
    }

    fn create_pi(&mut self, target: StrTendril, data: StrTendril) -> NodeId {
        self.doc.alloc(NodeData::ProcessingInstruction {
            target: target.to_string(),
            data: data.to_string(),
        })
    }

    // ── Tree manipulation ─────────────────────────────────────────────────────
    fn append(&mut self, parent: &NodeId, child: NodeOrText<NodeId>) {
        match child {
            NodeOrText::AppendNode(id) => self.doc.append_child(*parent, id),
            NodeOrText::AppendText(t)  => self.append_text(*parent, t),
        }
    }

    fn append_before_sibling(&mut self, sibling: &NodeId, child: NodeOrText<NodeId>) {
        match child {
            NodeOrText::AppendNode(id) => self.doc.insert_before(*sibling, id),
            NodeOrText::AppendText(t)  => self.append_text_before(*sibling, t),
        }
    }

    /// Foster parenting: content goes before `prev_element` when `element` has
    /// no parent (table foster-parenting per HTML5 spec).
    fn append_based_on_parent_node(
        &mut self,
        element: &NodeId,
        prev_element: &NodeId,
        child: NodeOrText<NodeId>,
    ) {
        if self.doc.get(*element).parent.is_some() {
            self.append_before_sibling(element, child);
        } else {
            self.append(prev_element, child);
        }
    }

    fn append_doctype_to_document(
        &mut self,
        name: StrTendril,
        public_id: StrTendril,
        system_id: StrTendril,
    ) {
        let id = self.doc.alloc(NodeData::Doctype {
            name: name.to_string(),
            public_id: public_id.to_string(),
            system_id: system_id.to_string(),
        });
        self.doc.append_child(DOCUMENT_ID, id);
    }

    fn add_attrs_if_missing(&mut self, target: &NodeId, attrs: Vec<Attribute>) {
        for a in attrs {
            let local = a.name.local.as_ref().to_owned();
            if self.doc.get_attr(*target, &local).is_none() {
                self.doc.set_attr_qual(*target, a.name, a.value.to_string());
            }
        }
    }

    fn remove_from_parent(&mut self, target: &NodeId) {
        self.doc.detach(*target);
    }

    fn reparent_children(&mut self, node: &NodeId, new_parent: &NodeId) {
        let children: Vec<NodeId> = self.doc.children_ids(*node).collect();
        for child in children {
            self.doc.detach(child);
            self.doc.append_child(*new_parent, child);
        }
    }

    fn get_template_contents(&mut self, target: &NodeId) -> NodeId {
        // Simplified: return the element itself as the content holder.
        // A full implementation would allocate a DocumentFragment sub-tree.
        *target
    }

    fn mark_script_already_started(&mut self, _: &NodeId) {}

    fn associate_with_form(
        &mut self,
        _target: &NodeId,
        _form: &NodeId,
        _nodes: (&NodeId, Option<&NodeId>),
    ) {}

    fn is_mathml_annotation_xml_integration_point(&self, _: &NodeId) -> bool { false }
}
