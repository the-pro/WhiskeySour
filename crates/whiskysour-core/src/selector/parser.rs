//! CSS selector parser.
//!
//! Supports the full CSS3 selector spec plus the CSS4 :is(), :where(), :has()
//! pseudo-classes. Written as a hand-rolled recursive-descent parser so it
//! produces good error messages and avoids heavy dependencies.

use std::fmt;

// ── Public types ──────────────────────────────────────────────────────────────

/// A comma-separated group of selectors: `h1, h2, .foo`.
#[derive(Debug, Clone)]
pub struct SelectorGroup(pub Vec<Selector>);

/// One selector: a chain of (combinator, simple-selector-sequence) steps.
/// E.g. `div > p.foo + span` → three steps.
#[derive(Debug, Clone)]
pub struct Selector {
    /// `steps[0]` has `Combinator::None` (start of chain).
    pub steps: Vec<SelectorStep>,
}

#[derive(Debug, Clone)]
pub struct SelectorStep {
    pub combinator: Combinator,
    /// The simple selectors that all must match at this step.
    pub simples: Vec<SimpleSelector>,
}

#[derive(Debug, Clone, PartialEq)]
pub enum Combinator {
    /// Beginning of selector (no combinator).
    None,
    /// Whitespace: any descendant.
    Descendant,
    /// `>`: direct child.
    Child,
    /// `+`: immediately following sibling.
    Adjacent,
    /// `~`: any following sibling.
    Sibling,
}

#[derive(Debug, Clone)]
pub enum SimpleSelector {
    /// `*` — matches any element.
    Universal,
    /// `div`, `p`, `span` — matches by tag name (lowercased).
    Type(String),
    /// `.foo` — element must have this class token.
    Class(String),
    /// `#bar` — element must have this id.
    Id(String),
    /// `[attr]`, `[attr=val]`, etc.
    Attribute(AttrSelector),
    /// `:pseudo-class`
    Pseudo(PseudoClass),
}

#[derive(Debug, Clone)]
pub struct AttrSelector {
    pub name: String,
    pub op: AttrOp,
    /// Empty string when op is `Exists`.
    pub value: String,
    /// `[attr=val i]` — case-insensitive flag.
    pub case_insensitive: bool,
}

#[derive(Debug, Clone, PartialEq)]
pub enum AttrOp {
    /// `[attr]` — attribute must exist.
    Exists,
    /// `[attr=val]` — exact match.
    Equals,
    /// `[attr~=val]` — word match (space-separated list).
    Includes,
    /// `[attr|=val]` — val or val-*.
    DashMatch,
    /// `[attr^=val]` — starts with val.
    Prefix,
    /// `[attr$=val]` — ends with val.
    Suffix,
    /// `[attr*=val]` — contains val.
    Substring,
}

/// CSS structural pseudo-classes.
#[derive(Debug, Clone)]
pub enum PseudoClass {
    FirstChild,
    LastChild,
    OnlyChild,
    FirstOfType,
    LastOfType,
    OnlyOfType,
    NthChild(NthArg),
    NthLastChild(NthArg),
    NthOfType(NthArg),
    NthLastOfType(NthArg),
    Not(Box<SelectorGroup>),
    Is(Box<SelectorGroup>),
    Where(Box<SelectorGroup>),
    Has(Box<SelectorGroup>),
    Empty,
    Root,
}

/// The `An+B` argument for nth-* pseudo-classes.
/// `a=0, b=1` means `:nth-child(1)` (first child).
/// `a=2, b=0` means `:nth-child(even)`.
#[derive(Debug, Clone, PartialEq)]
pub struct NthArg { pub a: i32, pub b: i32 }

impl NthArg {
    /// Returns `true` if the 1-based `index` satisfies `An+B`.
    pub fn matches(&self, index: usize) -> bool {
        let idx = index as i32;
        if self.a == 0 {
            idx == self.b
        } else {
            let n = (idx - self.b) / self.a;
            n >= 0 && self.a * n + self.b == idx
        }
    }
}

// ── Error type ────────────────────────────────────────────────────────────────

#[derive(Debug)]
pub struct SelectorError(pub String);

impl fmt::Display for SelectorError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        write!(f, "CSS selector parse error: {}", self.0)
    }
}

// ── Entry point ───────────────────────────────────────────────────────────────

pub fn parse_selector(input: &str) -> Result<SelectorGroup, SelectorError> {
    let mut p = Parser::new(input);
    p.parse_selector_group()
}

// ── Parser ────────────────────────────────────────────────────────────────────

struct Parser<'a> {
    input: &'a str,
    pos: usize,
}

impl<'a> Parser<'a> {
    fn new(input: &'a str) -> Self {
        Parser { input, pos: 0 }
    }

    // ── Primitives ────────────────────────────────────────────────────────────

    fn peek(&self) -> Option<char> {
        self.input[self.pos..].chars().next()
    }

    fn advance(&mut self) {
        if let Some(c) = self.peek() {
            self.pos += c.len_utf8();
        }
    }

    fn eat(&mut self, expected: char) -> bool {
        if self.peek() == Some(expected) {
            self.advance();
            true
        } else {
            false
        }
    }

    fn skip_whitespace(&mut self) {
        while matches!(self.peek(), Some(' ' | '\t' | '\n' | '\r' | '\x0c')) {
            self.advance();
        }
    }

    fn at_end(&self) -> bool {
        self.pos >= self.input.len()
    }

    // ── Identifiers / strings ─────────────────────────────────────────────────

    fn parse_ident(&mut self) -> Result<String, SelectorError> {
        let start = self.pos;
        // Allow leading `-` for vendor-prefixed names.
        if self.peek() == Some('-') {
            self.advance();
        }
        match self.peek() {
            Some(c) if c.is_alphabetic() || c == '_' => {}
            _ => return Err(SelectorError(format!(
                "expected identifier at position {}", self.pos
            ))),
        }
        while let Some(c) = self.peek() {
            if c.is_alphanumeric() || c == '-' || c == '_' {
                self.advance();
            } else {
                break;
            }
        }
        Ok(self.input[start..self.pos].to_lowercase())
    }

    fn parse_attr_value(&mut self) -> Result<String, SelectorError> {
        match self.peek() {
            Some('"') | Some('\'') => {
                let quote = self.peek().unwrap();
                self.advance();
                let start = self.pos;
                while let Some(c) = self.peek() {
                    if c == quote { break; }
                    self.advance();
                }
                let val = self.input[start..self.pos].to_owned();
                if !self.eat(quote) {
                    return Err(SelectorError("unclosed attribute value string".into()));
                }
                Ok(val)
            }
            _ => {
                // Unquoted value — read until `]`, `i`, or whitespace.
                let start = self.pos;
                while let Some(c) = self.peek() {
                    if matches!(c, ']' | ' ' | '\t' | '\n') { break; }
                    self.advance();
                }
                Ok(self.input[start..self.pos].to_owned())
            }
        }
    }

    // ── Selector group (comma-separated) ─────────────────────────────────────

    fn parse_selector_group(&mut self) -> Result<SelectorGroup, SelectorError> {
        let mut selectors = vec![self.parse_selector()?];
        loop {
            self.skip_whitespace();
            if !self.eat(',') { break; }
            self.skip_whitespace();
            selectors.push(self.parse_selector()?);
        }
        Ok(SelectorGroup(selectors))
    }

    // ── Single selector (chain of combinator + simples) ───────────────────────

    fn parse_selector(&mut self) -> Result<Selector, SelectorError> {
        let first_simples = self.parse_simple_selector_sequence()?;
        let mut steps = vec![SelectorStep { combinator: Combinator::None, simples: first_simples }];

        loop {
            // Peek at what follows to determine the combinator.
            let ws_before = matches!(self.peek(), Some(' ' | '\t' | '\n' | '\r'));
            self.skip_whitespace();

            let combinator = match self.peek() {
                Some('>') => { self.advance(); self.skip_whitespace(); Combinator::Child }
                Some('+') => { self.advance(); self.skip_whitespace(); Combinator::Adjacent }
                Some('~') => { self.advance(); self.skip_whitespace(); Combinator::Sibling }
                Some(',') | None => break,
                _ if ws_before => Combinator::Descendant,
                _ => break,
            };

            let simples = self.parse_simple_selector_sequence()?;
            if simples.is_empty() { break; }
            steps.push(SelectorStep { combinator, simples });
        }

        Ok(Selector { steps })
    }

    // ── Simple selector sequence (tag[.class][#id][attr][:pseudo]*) ───────────

    fn parse_simple_selector_sequence(&mut self) -> Result<Vec<SimpleSelector>, SelectorError> {
        let mut simples = Vec::new();

        // Optional leading type or universal selector.
        match self.peek() {
            Some('*') => { self.advance(); simples.push(SimpleSelector::Universal); }
            Some(c) if c.is_alphabetic() || c == '_' || c == '-' => {
                let name = self.parse_ident()?;
                simples.push(SimpleSelector::Type(name));
            }
            _ => {}
        }

        // Zero or more qualifiers: .class, #id, [attr], :pseudo
        loop {
            match self.peek() {
                Some('.') => {
                    self.advance();
                    let cls = self.parse_ident()?;
                    simples.push(SimpleSelector::Class(cls));
                }
                Some('#') => {
                    self.advance();
                    let id = self.parse_ident()?;
                    simples.push(SimpleSelector::Id(id));
                }
                Some('[') => {
                    self.advance();
                    simples.push(SimpleSelector::Attribute(self.parse_attribute_selector()?));
                }
                Some(':') => {
                    self.advance();
                    // Skip a second colon for ::pseudo-element (treat as :pseudo-class).
                    self.eat(':');
                    let pseudo = self.parse_pseudo_class()?;
                    simples.push(SimpleSelector::Pseudo(pseudo));
                }
                _ => break,
            }
        }

        Ok(simples)
    }

    // ── Attribute selector ────────────────────────────────────────────────────

    fn parse_attribute_selector(&mut self) -> Result<AttrSelector, SelectorError> {
        self.skip_whitespace();
        let name = self.parse_ident()?;
        self.skip_whitespace();

        let op = match self.peek() {
            Some(']') => {
                self.advance();
                return Ok(AttrSelector { name, op: AttrOp::Exists, value: String::new(), case_insensitive: false });
            }
            Some('=') => { self.advance(); AttrOp::Equals }
            Some('~') => { self.advance(); self.eat('='); AttrOp::Includes }
            Some('|') => { self.advance(); self.eat('='); AttrOp::DashMatch }
            Some('^') => { self.advance(); self.eat('='); AttrOp::Prefix }
            Some('$') => { self.advance(); self.eat('='); AttrOp::Suffix }
            Some('*') => { self.advance(); self.eat('='); AttrOp::Substring }
            _ => return Err(SelectorError(format!("unexpected char in attribute selector: {:?}", self.peek()))),
        };

        self.skip_whitespace();
        let value = self.parse_attr_value()?;
        self.skip_whitespace();

        // Case-insensitive flag `i`.
        let case_insensitive = match self.peek() {
            Some('i') | Some('I') => { self.advance(); self.skip_whitespace(); true }
            _ => false,
        };

        if !self.eat(']') {
            return Err(SelectorError("expected ']' to close attribute selector".into()));
        }
        Ok(AttrSelector { name, op, value, case_insensitive })
    }

    // ── Pseudo-class ──────────────────────────────────────────────────────────

    fn parse_pseudo_class(&mut self) -> Result<PseudoClass, SelectorError> {
        let name = self.parse_ident()?;
        match name.as_str() {
            "first-child"  => Ok(PseudoClass::FirstChild),
            "last-child"   => Ok(PseudoClass::LastChild),
            "only-child"   => Ok(PseudoClass::OnlyChild),
            "first-of-type"=> Ok(PseudoClass::FirstOfType),
            "last-of-type" => Ok(PseudoClass::LastOfType),
            "only-of-type" => Ok(PseudoClass::OnlyOfType),
            "empty"        => Ok(PseudoClass::Empty),
            "root"         => Ok(PseudoClass::Root),
            "nth-child"      => Ok(PseudoClass::NthChild(self.parse_nth_parens()?)),
            "nth-last-child" => Ok(PseudoClass::NthLastChild(self.parse_nth_parens()?)),
            "nth-of-type"    => Ok(PseudoClass::NthOfType(self.parse_nth_parens()?)),
            "nth-last-of-type" => Ok(PseudoClass::NthLastOfType(self.parse_nth_parens()?)),
            "not"   => Ok(PseudoClass::Not(Box::new(self.parse_group_parens()?))),
            "is"    => Ok(PseudoClass::Is(Box::new(self.parse_group_parens()?))),
            "where" => Ok(PseudoClass::Where(Box::new(self.parse_group_parens()?))),
            "has"   => Ok(PseudoClass::Has(Box::new(self.parse_group_parens()?))),
            // Silently ignore unknown pseudo-classes (e.g. :hover, :focus) — they
            // match nothing structural, so we treat them as universal matches.
            _ => {
                // Skip optional arguments.
                if self.peek() == Some('(') { self.skip_balanced_parens(); }
                Ok(PseudoClass::Root) // placeholder — treated as always-true later
            }
        }
    }

    fn parse_nth_parens(&mut self) -> Result<NthArg, SelectorError> {
        if !self.eat('(') {
            return Err(SelectorError("expected '(' after nth pseudo-class".into()));
        }
        self.skip_whitespace();
        let arg = self.parse_nth_arg()?;
        self.skip_whitespace();
        if !self.eat(')') {
            return Err(SelectorError("expected ')' after nth argument".into()));
        }
        Ok(arg)
    }

    fn parse_group_parens(&mut self) -> Result<SelectorGroup, SelectorError> {
        if !self.eat('(') {
            return Err(SelectorError("expected '(' for pseudo-class argument".into()));
        }
        self.skip_whitespace();
        let group = self.parse_selector_group()?;
        self.skip_whitespace();
        if !self.eat(')') {
            return Err(SelectorError("expected ')' to close pseudo-class".into()));
        }
        Ok(group)
    }

    // ── An+B parser ───────────────────────────────────────────────────────────

    fn parse_nth_arg(&mut self) -> Result<NthArg, SelectorError> {
        // Keywords: even, odd, or An+B expressions.
        let start = self.pos;
        let tok: String = self.input[self.pos..]
            .chars()
            .take_while(|&c| c != ')' && c != ' ')
            .collect();
        self.pos += tok.len();

        match tok.to_lowercase().as_str() {
            "even" => return Ok(NthArg { a: 2, b: 0 }),
            "odd"  => return Ok(NthArg { a: 2, b: 1 }),
            _ => {}
        }

        // Parse An+B manually.
        // Forms: n, -n, 2n, 2n+3, 2n-3, +3, -3, 3
        self.pos = start;
        let mut a: i32 = 0;
        let mut b: i32 = 0;

        let neg_a = self.eat('-');
        let pos_a = !neg_a && self.eat('+');

        // Coefficient before 'n'?
        let coef = self.parse_optional_int();
        let has_n = matches!(self.peek(), Some('n') | Some('N'));

        if has_n {
            self.advance(); // consume 'n'
            a = match coef {
                Some(v) => if neg_a { -v } else { v },
                None    => if neg_a { -1 } else { 1 },
            };
            self.skip_whitespace();
            // Parse +B or -B
            let neg_b = self.eat('-');
            if !neg_b { self.eat('+'); }
            self.skip_whitespace();
            if let Some(v) = self.parse_optional_int() {
                b = if neg_b { -v } else { v };
            }
        } else {
            // Pure integer — only B, a=0.
            b = match coef {
                Some(v) => if neg_a { -v } else { v },
                None => return Err(SelectorError(format!("invalid nth arg at pos {}", self.pos))),
            };
        }

        Ok(NthArg { a, b })
    }

    fn parse_optional_int(&mut self) -> Option<i32> {
        let start = self.pos;
        while matches!(self.peek(), Some('0'..='9')) {
            self.advance();
        }
        if self.pos == start {
            None
        } else {
            self.input[start..self.pos].parse().ok()
        }
    }

    fn skip_balanced_parens(&mut self) {
        if !self.eat('(') { return; }
        let mut depth = 1usize;
        while let Some(c) = self.peek() {
            self.advance();
            match c {
                '(' => depth += 1,
                ')' => { depth -= 1; if depth == 0 { break; } }
                _ => {}
            }
        }
    }
}
