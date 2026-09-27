//! Node types for the WhiskeySour DOM tree.
//!
//! Flat Vec<Node> arena; NodeId is a u32 index. No Rc/Box/pointer indirection.

use markup5ever::QualName;

/// Index into `Document::nodes`. Node 0 is always the document root.
pub type NodeId = u32;
pub const DOCUMENT_ID: NodeId = 0;

/// A single attribute on an element.
#[derive(Debug, Clone, PartialEq)]
pub struct Attr {
    /// Qualified name (carries namespace + local name atoms from html5ever).
    pub name: QualName,
    /// The attribute value, already unescaped by the parser.
    pub value: String,
}

impl Attr {
    pub fn new(name: QualName, value: impl Into<String>) -> Self {
        Self {
            name,
            value: value.into(),
        }
    }

    /// The local (unqualified) name as a plain &str, e.g. "class", "href".
    #[inline]
    pub fn local_name(&self) -> &str {
        self.name.local.as_ref()
    }
}

/// Payload of an element node. Boxed inside [`NodeData::Element`] so that
/// text and comment nodes (the majority of a typical tree) don't pay for it.
#[derive(Debug, Clone)]
pub struct ElementData {
    /// Qualified name (holds the interned LocalName atom + Namespace).
    pub name: QualName,
    /// Attributes in source order. Stored out of line: most elements have
    /// 0–2 attributes, so inline slots would mostly be wasted space.
    pub attrs: Vec<Attr>,
    /// True when parsed in XML mode with a self-closing slash.
    pub self_closing: bool,
    /// Whether this element is a template (content is in a separate subtree).
    pub is_template: bool,
}

/// A `<?target data?>` processing instruction.
#[derive(Debug, Clone)]
pub struct PiData {
    pub target: String,
    pub data: String,
}

/// A `<!DOCTYPE>` declaration.
#[derive(Debug, Clone)]
pub struct DoctypeData {
    pub name: String,
    pub public_id: String,
    pub system_id: String,
}

/// Data payload of a tree node.
///
/// Kept to one `String` plus a tag (32 bytes); bigger or rarer payloads are
/// boxed so text nodes stay small.
#[derive(Debug, Clone)]
pub enum NodeData {
    /// Synthetic root — always NodeId 0.
    Document,

    /// An HTML/XML element.
    Element(Box<ElementData>),

    /// A text node.
    Text(String),

    /// An HTML comment: `<!-- … -->`.
    Comment(String),

    /// A CDATA section (XML only).
    CData(String),

    /// A processing instruction: `<?target data?>`.
    ProcessingInstruction(Box<PiData>),

    /// A `<!DOCTYPE>` declaration.
    Doctype(Box<DoctypeData>),
}

impl NodeData {
    /// A new element node payload.
    pub fn element(
        name: QualName,
        attrs: Vec<Attr>,
        self_closing: bool,
        is_template: bool,
    ) -> Self {
        NodeData::Element(Box::new(ElementData {
            name,
            attrs,
            self_closing,
            is_template,
        }))
    }

    #[inline]
    pub fn is_element(&self) -> bool {
        matches!(self, NodeData::Element(_))
    }

    #[inline]
    pub fn is_text(&self) -> bool {
        matches!(self, NodeData::Text(_))
    }

    /// Returns the element payload, or None.
    #[inline]
    pub fn as_element(&self) -> Option<&ElementData> {
        match self {
            NodeData::Element(e) => Some(e),
            _ => None,
        }
    }

    /// Returns the element's local tag name (e.g. "div"), or None.
    #[inline]
    pub fn element_name(&self) -> Option<&str> {
        self.as_element().map(|e| e.name.local.as_ref())
    }

    /// Returns the element's QualName, or None.
    pub fn qual_name(&self) -> Option<&QualName> {
        self.as_element().map(|e| &e.name)
    }

    /// Returns the attrs slice, or None.
    pub fn attrs(&self) -> Option<&[Attr]> {
        self.as_element().map(|e| e.attrs.as_slice())
    }

    pub fn attrs_mut(&mut self) -> Option<&mut Vec<Attr>> {
        match self {
            NodeData::Element(e) => Some(&mut e.attrs),
            _ => None,
        }
    }
}

/// Sentinel for an absent link. Node ids never reach it: `Document::alloc`
/// refuses to hand it out.
const NO_NODE: u32 = u32::MAX;

#[inline]
fn pack(id: Option<NodeId>) -> u32 {
    id.unwrap_or(NO_NODE)
}

#[inline]
fn unpack(raw: u32) -> Option<NodeId> {
    (raw != NO_NODE).then_some(raw)
}

/// A single node in the flat arena.
///
/// Links are stored as bare `u32`s with a sentinel instead of
/// `Option<NodeId>` (4 bytes each instead of 8); use the accessors.
#[derive(Debug, Clone)]
pub struct Node {
    pub data: NodeData,
    parent: u32,
    first_child: u32,
    last_child: u32,
    prev_sibling: u32,
    next_sibling: u32,
}

impl Node {
    pub fn new(data: NodeData) -> Self {
        Node {
            data,
            parent: NO_NODE,
            first_child: NO_NODE,
            last_child: NO_NODE,
            prev_sibling: NO_NODE,
            next_sibling: NO_NODE,
        }
    }

    #[inline]
    pub fn tag_name(&self) -> Option<&str> {
        self.data.element_name()
    }

    #[inline]
    pub fn parent(&self) -> Option<NodeId> {
        unpack(self.parent)
    }
    #[inline]
    pub fn first_child(&self) -> Option<NodeId> {
        unpack(self.first_child)
    }
    #[inline]
    pub fn last_child(&self) -> Option<NodeId> {
        unpack(self.last_child)
    }
    #[inline]
    pub fn prev_sibling(&self) -> Option<NodeId> {
        unpack(self.prev_sibling)
    }
    #[inline]
    pub fn next_sibling(&self) -> Option<NodeId> {
        unpack(self.next_sibling)
    }

    #[inline]
    pub(crate) fn set_parent(&mut self, id: Option<NodeId>) {
        self.parent = pack(id);
    }
    #[inline]
    pub(crate) fn set_first_child(&mut self, id: Option<NodeId>) {
        self.first_child = pack(id);
    }
    #[inline]
    pub(crate) fn set_last_child(&mut self, id: Option<NodeId>) {
        self.last_child = pack(id);
    }
    #[inline]
    pub(crate) fn set_prev_sibling(&mut self, id: Option<NodeId>) {
        self.prev_sibling = pack(id);
    }
    #[inline]
    pub(crate) fn set_next_sibling(&mut self, id: Option<NodeId>) {
        self.next_sibling = pack(id);
    }

    #[inline]
    pub(crate) fn take_parent(&mut self) -> Option<NodeId> {
        unpack(std::mem::replace(&mut self.parent, NO_NODE))
    }
    #[inline]
    pub(crate) fn take_prev_sibling(&mut self) -> Option<NodeId> {
        unpack(std::mem::replace(&mut self.prev_sibling, NO_NODE))
    }
    #[inline]
    pub(crate) fn take_next_sibling(&mut self) -> Option<NodeId> {
        unpack(std::mem::replace(&mut self.next_sibling, NO_NODE))
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn node_layout_stays_compact() {
        // NodeData: a String plus the discriminant (element payload boxed);
        // Node: that plus five 4-byte links. Was 232 / 272 bytes before boxing.
        assert_eq!(std::mem::size_of::<NodeData>(), 32);
        assert_eq!(std::mem::size_of::<Node>(), 56);
    }
}
