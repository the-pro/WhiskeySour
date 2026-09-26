mod matcher;
mod parser;

use std::cell::RefCell;
use std::collections::HashMap;
use std::rc::Rc;

pub use matcher::matches_selector_group;
pub(crate) use matcher::{matches_group, MatchContext};
pub use parser::{
    parse_selector, AttrOp, Combinator, NthArg, Selector, SelectorError, SelectorGroup,
    SimpleSelector,
};

/// Maximum number of distinct selectors kept per thread before the cache resets.
const SELECTOR_CACHE_CAP: usize = 256;

thread_local! {
    /// Per-thread cache of parsed selectors. Thread-local, so it needs no lock and
    /// never serialises selector access across threads.
    static SELECTOR_CACHE: RefCell<HashMap<String, Rc<SelectorGroup>>> =
        RefCell::new(HashMap::new());
}

/// Parse `css`, reusing an earlier parse of the same string when possible.
///
/// Scraping loops typically run the same handful of selectors against many
/// elements; this skips re-parsing on every call. Parse errors are not cached.
pub fn parse_selector_cached(css: &str) -> Result<Rc<SelectorGroup>, SelectorError> {
    if let Some(hit) = SELECTOR_CACHE.with(|c| c.borrow().get(css).cloned()) {
        return Ok(hit);
    }
    let group = Rc::new(parse_selector(css)?);
    SELECTOR_CACHE.with(|c| {
        let mut cache = c.borrow_mut();
        if cache.len() >= SELECTOR_CACHE_CAP {
            cache.clear();
        }
        cache.insert(css.to_owned(), Rc::clone(&group));
    });
    Ok(group)
}
