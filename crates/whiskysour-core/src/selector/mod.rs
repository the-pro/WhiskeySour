mod parser;
mod matcher;

pub use parser::{parse_selector, Selector, SelectorGroup, SimpleSelector, Combinator, AttrOp, NthArg};
pub use matcher::matches_selector_group;
