mod matcher;
mod parser;

pub use matcher::matches_selector_group;
pub use parser::{
    parse_selector, AttrOp, Combinator, NthArg, Selector, SelectorGroup, SimpleSelector,
};
