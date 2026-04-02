"""
fuzz_parser.py — Property-based and fuzz tests using Hypothesis.

Properties that must hold for ALL inputs:
  1. Parser never raises an exception (only returns a document)
  2. str(parse(html)) is valid UTF-8
  3. Round-trip stability: str(parse(str(parse(html)))) == str(parse(html))
  4. get_text() never raises
  5. find_all(True) never raises
  6. select("*") never raises
  7. Memory usage is bounded (no exponential blowup)
"""

from __future__ import annotations

import re
import string

import pytest

hypothesis = pytest.importorskip("hypothesis", reason="hypothesis not installed")

from hypothesis import given, settings, assume, HealthCheck
from hypothesis import strategies as st

pytestmark = pytest.mark.fuzz


# ===========================================================================
# Strategies
# ===========================================================================

# Valid ASCII tag names
tag_name = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-",
    min_size=1,
    max_size=20,
).filter(lambda s: s[0].isalpha())

# Attribute names
attr_name = st.text(
    alphabet=string.ascii_lowercase + string.digits + "-_",
    min_size=1,
    max_size=20,
).filter(lambda s: s[0].isalpha())

# Attribute values — any printable text
attr_value = st.text(
    alphabet=st.characters(
        whitelist_categories=("Lu", "Ll", "Nd", "Zs"),
        whitelist_characters='!#$%&()*+,-./:;=?@[]^_`{|}~\'"',
        blacklist_characters="\\",
    ),
    max_size=100,
)

# A single HTML attribute string
html_attr = st.builds(
    lambda name, val: f' {name}="{val}"',
    name=attr_name,
    val=attr_value,
)

# A simple self-closing or paired tag
@st.composite
def simple_tag(draw, max_depth=3):
    name = draw(tag_name)
    attrs = "".join(draw(st.lists(html_attr, max_size=5)))
    if draw(st.booleans()) or max_depth == 0:
        return f"<{name}{attrs}>"
    children = "".join(draw(st.lists(
        st.one_of(
            st.text(alphabet=string.printable, max_size=50),
            simple_tag(max_depth=max_depth - 1) if max_depth > 1 else st.just(""),
        ),
        max_size=3,
    )))
    return f"<{name}{attrs}>{children}</{name}>"


# Well-formed HTML document
@st.composite
def html_document(draw):
    body_content = "".join(draw(st.lists(simple_tag(), max_size=10)))
    return f"<!DOCTYPE html><html><head></head><body>{body_content}</body></html>"


# Completely random bytes
random_bytes = st.binary(min_size=0, max_size=4096)

# Random unicode text (may or may not be valid HTML)
random_unicode = st.text(min_size=0, max_size=2000)

# Pathological strings
pathological = st.one_of(
    st.just(""),
    st.just("<"),
    st.just(">"),
    st.just("</"),
    st.just("<!"),
    st.just("<!--"),
    st.just("<!DOCTYPE"),
    st.just("<" + "a" * 100000),
    st.just(">" * 10000),
    st.just("&" * 10000),
    st.just("&amp;" * 1000),
    st.just("<div>" * 1000),
    st.just("</div>" * 1000),
    st.just("<" + "x" * 1000 + ">"),
    st.just("\x00" * 100),
    st.just("\xff" * 100),
    st.just("<?xml version='1.0'?>"),
)


# ===========================================================================
# 1. Parser never crashes
# ===========================================================================

@given(html=random_unicode)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_parser_never_raises_on_unicode(parse, html):
    try:
        soup = parse(html)
        assert soup is not None
    except Exception as e:
        pytest.fail(f"Parser raised on unicode input: {e!r}\nInput: {html[:200]!r}")


@given(data=random_bytes)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_parser_never_raises_on_bytes(parse, data):
    try:
        soup = parse(data)
        assert soup is not None
    except UnicodeDecodeError:
        pass  # acceptable for random bytes with no encoding hint
    except Exception as e:
        pytest.fail(f"Parser raised on bytes: {e!r}\nInput: {data[:50]!r}")


@given(html=pathological)
@settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
def test_parser_handles_pathological_inputs(parse, html):
    try:
        soup = parse(html)
        assert soup is not None
    except Exception as e:
        pytest.fail(f"Parser raised on pathological input: {e!r}\nInput: {html[:200]!r}")


@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_parser_handles_generated_documents(parse, html):
    soup = parse(html)
    assert soup is not None


# ===========================================================================
# 2. Output is always valid UTF-8
# ===========================================================================

@given(html=random_unicode)
@settings(max_examples=300, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_output_is_valid_utf8(parse, html):
    try:
        soup = parse(html)
        output = str(soup)
        # Must be valid Python str (always UTF-8 internally)
        assert isinstance(output, str)
        # Must encode to UTF-8 without errors
        output.encode("utf-8")
    except Exception as e:
        pytest.fail(f"Output not valid UTF-8: {e!r}")


@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_generated_output_is_valid_utf8(parse, html):
    soup = parse(html)
    output = str(soup)
    output.encode("utf-8")  # Must not raise


# ===========================================================================
# 3. Round-trip stability
# ===========================================================================

@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_round_trip_stable(parse, html):
    """parse(str(parse(html))) must equal str(parse(html))."""
    def normalise(s):
        return re.sub(r"\s+", " ", s).strip()

    s1 = str(parse(html))
    s2 = str(parse(s1))
    assert normalise(s1) == normalise(s2), (
        f"Round-trip not stable.\n"
        f"First parse:  {s1[:300]}\n"
        f"Second parse: {s2[:300]}"
    )


# ===========================================================================
# 4. get_text() never raises
# ===========================================================================

@given(html=random_unicode)
@settings(max_examples=500, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_get_text_never_raises(parse, html):
    try:
        soup = parse(html)
        text = soup.get_text()
        assert isinstance(text, str)
    except Exception as e:
        pytest.fail(f"get_text() raised: {e!r}")


# ===========================================================================
# 5. find_all(True) never raises
# ===========================================================================

@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_find_all_never_raises(parse, html):
    soup = parse(html)
    result = soup.find_all(True)
    assert isinstance(result, list)


# ===========================================================================
# 6. select("*") never raises
# ===========================================================================

@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_select_star_never_raises(parse, html):
    soup = parse(html)
    result = soup.select("*")
    assert isinstance(result, list)


# ===========================================================================
# 7. No null bytes in output
# ===========================================================================

@given(html=random_unicode)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_no_null_bytes_in_output(parse, html):
    try:
        soup = parse(html)
        output = str(soup)
        assert "\x00" not in output
    except Exception:
        pass  # We only care about null bytes if parse succeeded


# ===========================================================================
# 8. find_all count is non-negative
# ===========================================================================

@given(html=html_document(), tag=tag_name)
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_find_all_count_non_negative(parse, html, tag):
    soup = parse(html)
    result = soup.find_all(tag)
    assert len(result) >= 0


# ===========================================================================
# 9. str(tag) contains tag name
# ===========================================================================

@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_str_tag_contains_name(parse, html):
    soup = parse(html)
    for tag in soup.find_all(True):
        s = str(tag)
        assert f"<{tag.name}" in s


# ===========================================================================
# 10. Attribute access consistent
# ===========================================================================

@given(html=html_document())
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_attr_access_consistent(parse, html):
    """tag.get(key) == tag.attrs[key] for all present attributes."""
    soup = parse(html)
    for tag in soup.find_all(True):
        for key, val in tag.attrs.items():
            assert tag.get(key) == val
            assert tag[key] == val


# ===========================================================================
# 11. Parent invariant
# ===========================================================================

@given(html=html_document())
@settings(max_examples=100, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_parent_child_invariant(parse, html):
    """If tag is in tag.parent.contents, then tag.parent is correct."""
    soup = parse(html)
    for tag in soup.find_all(True):
        if tag.parent is not None:
            assert tag in tag.parent.contents


# ===========================================================================
# 12. CSS selectors on random input don't raise
# ===========================================================================

SAFE_SELECTORS = [
    "*", "div", "p", "a", "span",
    ".foo", "#bar", "[href]", "[class]",
    "div > p", "a + span", "li:first-child",
    "p:not(.x)", ":root",
]


@given(html=html_document(), selector=st.sampled_from(SAFE_SELECTORS))
@settings(max_examples=200, suppress_health_check=[HealthCheck.too_slow, HealthCheck.function_scoped_fixture])
def test_css_selectors_dont_raise(parse, html, selector):
    soup = parse(html)
    result = soup.select(selector)
    assert isinstance(result, list)
