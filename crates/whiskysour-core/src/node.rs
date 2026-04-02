//! Node types for the WhiskeySour DOM tree.
//!
//! Flat Vec<Node> arena; NodeId is a u32 index. No Rc/Box/pointer indirection.

use markup5ever::QualName;
use smallvec::SmallVec;

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
        Self { name, value: value.into() }
    }

    /// The local (unqualified) name as a plain &str, e.g. "class", "href".
    #[inline]
    pub fn local_name(&self) -> &str {
        self.name.local.as_ref()
    }
}

/// Data payload of a tree node.
#[derive(Debug, Clone)]
pub enum NodeData {
    /// Synthetic root — always NodeId 0.
    Document,

    /// An HTML/XML element.
    Element {
        /// Qualified name (holds the interned LocalName atom + Namespace).
        name: QualName,
        /// Attributes in source order. SmallVec avoids a heap alloc for ≤4 attrs.
        attrs: SmallVec<[Attr; 4]>,
        /// True when parsed in XML mode with a self-closing slash.
        self_closing: bool,
        /// Whether this element is a template (content is in a separate subtree).
        is_template: bool,
    },

    /// A text node.
    Text(String),

    /// An HTML comment: `<!-- … -->`.
    Comment(String),

    /// A CDATA section (XML only).
    CData(String),

    /// A processing instruction: `<?target data?>`.
    ProcessingInstruction { target: String, data: String },

    /// A `<!DOCTYPE>` declaration.
    Doctype { name: String, public_id: String, system_id: String },
}

impl NodeData {
    #[inline]
    pub fn is_element(&self) -> bool {
        matches!(self, NodeData::Element { .. })
    }

    #[inline]
    pub fn is_text(&self) -> bool {
        matches!(self, NodeData::Text(_))
    }

    /// Returns the element's local tag name (e.g. "div"), or None.
    #[inline]
    pub fn element_name(&self) -> Option<&str> {
        match self {
            NodeData::Element { name, .. } => Some(name.local.as_ref()),
            _ => None,
        }
    }

    /// Returns the element's QualName, or None.
    pub fn qual_name(&self) -> Option<&QualName> {
        match self {
            NodeData::Element { name, .. } => Some(name),
            _ => None,
        }
    }

    /// Returns the attrs slice, or None.
    pub fn attrs(&self) -> Option<&SmallVec<[Attr; 4]>> {
        match self {
            NodeData::Element { attrs, .. } => Some(attrs),
            _ => None,
        }
    }

    pub fn attrs_mut(&mut self) -> Option<&mut SmallVec<[Attr; 4]>> {
        match self {
            NodeData::Element { attrs, .. } => Some(attrs),
            _ => None,
        }
    }
}

/// A single node in the flat arena.
#[derive(Debug, Clone)]
pub struct Node {
    pub data: NodeData,
    pub parent: Option<NodeId>,
    pub first_child: Option<NodeId>,
    pub last_child: Option<NodeId>,
    pub prev_sibling: Option<NodeId>,
    pub next_sibling: Option<NodeId>,
}

impl Node {
    pub fn new(data: NodeData) -> Self {
        Node { data, parent: None, first_child: None, last_child: None,
               prev_sibling: None, next_sibling: None }
    }

    #[inline]
    pub fn tag_name(&self) -> Option<&str> {
        self.data.element_name()
    }
}
