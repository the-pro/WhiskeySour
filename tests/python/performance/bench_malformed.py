"""
Malformed HTML comparison: WhiskeySour vs BeautifulSoup.

Runs the same broken markup through both parsers and compares:
  1. Whether they crash or recover
  2. What DOM they produce
  3. How fast they parse it

Usage:
    source .venv/bin/activate
    python tests/python/performance/bench_malformed.py
"""

import time
from textwrap import indent

from bs4 import BeautifulSoup
from whiskeysour import WhiskeySour


# ---------------------------------------------------------------------------
# Test cases: (label, html)
# ---------------------------------------------------------------------------

CASES = [
    ("Unclosed tags",
     "<div><p>first<p>second<p>third</div>"),

    ("Misnested bold/italic",
     "<b><i>hello</b></i> world"),

    ("Stray end tags",
     "</div></p></b>actual content<p>ok</p>"),

    ("Bare text, no structure",
     "just some text, no tags at all"),

    ("Void elements with close tags",
     "a<br></br>b<hr></hr>c<img src=x></img>d"),

    ("Duplicate attributes",
     '<span id="first" id="second" class="a" class="b">text</span>'),

    ("Table foster parenting",
     "<table>oops<tr><td>cell</td></tr></table>"),

    ("Unquoted / bare attributes",
     '<input type=text value=hello checked disabled>'),

    ("Block inside inline",
     "<span><div>block in span</div></span>"),

    ("<p> inside <p>",
     "<p>outer<p>inner</p>"),

    ("Form inside form",
     '<form id="a"><form id="b"><input></form></form>'),

    ("Script with angle brackets",
     '<script>if (a < b && c > d) { x = 1; }</script><p>after</p>'),

    ("Unclosed comment",
     "before<!-- oops<p>gone</p>"),

    ("Missing html/head/body",
     "<title>hi</title><p>content</p>"),

    ("Deep nesting (200 levels)",
     "<div>" * 200 + "deep" + "</div>" * 200),

    ("Multiple bodies",
     "<html><body>first</body><body>second</body></html>"),

    ("Mixed case tags",
     "<DIV><P CLASS='X'>hello</P></DIV>"),

    ("Real-world email tables",
     "<table><tr><td><table><tr><td><table><tr><td>cell</td></tr></table></td></tr></table></td></tr></table>"),

    ("Entities: named, numeric, broken",
     "<p>&amp; &lt; &#65; &#x42; &foobar; &amp hello</p>"),

    ("Null bytes",
     "<p>hel\x00lo wor\x00ld</p>"),

    ("MS Word garbage",
     "<p class=MsoNormal><b><span style='font-size:12pt'>Title</span></b></p>"),

    ("Extremely long attribute",
     '<div data-x="' + "A" * 5000 + '">text</div>'),

    ("Select inside select",
     '<select><option>a<select><option>b</select></select>'),

    ("Text after </html>",
     "<html><body>inside</body></html>trailing text"),

    ("Completely empty",
     ""),
]


def compare(label, html):
    """Parse with both, compare results, time them."""
    # --- WhiskeySour ---
    ws_err = None
    t0 = time.perf_counter()
    try:
        ws = WhiskeySour(html)
        ws_text = ws.get_text(strip=True)
        ws_html = str(ws)
    except Exception as e:
        ws_err = str(e)
        ws_text = ws_html = ""
    ws_time = time.perf_counter() - t0

    # --- BeautifulSoup (html.parser) ---
    bs_err = None
    t0 = time.perf_counter()
    try:
        bs = BeautifulSoup(html, "html.parser")
        bs_text = bs.get_text(strip=True)
        bs_html = str(bs)
    except Exception as e:
        bs_err = str(e)
        bs_text = bs_html = ""
    bs_time = time.perf_counter() - t0

    # --- Report ---
    speedup = bs_time / ws_time if ws_time > 0 else float("inf")
    text_match = ws_text == bs_text

    print(f"\n{'=' * 70}")
    print(f"  {label}")
    print(f"{'=' * 70}")
    print(f"  Input:  {html[:80]}{'...' if len(html) > 80 else ''}")

    if ws_err:
        print(f"  WS ERROR: {ws_err}")
    if bs_err:
        print(f"  BS4 ERROR: {bs_err}")

    if not ws_err and not bs_err:
        print(f"  Text match: {'YES' if text_match else 'NO'}")
        if not text_match:
            print(f"    WS  text: {ws_text[:100]}")
            print(f"    BS4 text: {bs_text[:100]}")

        ws_out = ws_html[:120]
        bs_out = bs_html[:120]
        if ws_html != bs_html:
            print(f"  HTML differs:")
            print(f"    WS:  {ws_out}{'...' if len(ws_html) > 120 else ''}")
            print(f"    BS4: {bs_out}{'...' if len(bs_html) > 120 else ''}")
        else:
            print(f"  HTML: identical")

    print(f"  Time:  WS {ws_time*1e6:8.1f} µs  |  BS4 {bs_time*1e6:8.1f} µs  |  {speedup:.1f}x faster")

    return ws_time, bs_time, ws_err, bs_err, text_match


def main():
    print("Malformed HTML Comparison: WhiskeySour vs BeautifulSoup (html.parser)")
    print("=" * 70)

    total_ws = 0
    total_bs = 0
    results = []

    for label, html in CASES:
        ws_t, bs_t, ws_err, bs_err, text_match = compare(label, html)
        total_ws += ws_t
        total_bs += bs_t
        results.append((label, ws_t, bs_t, ws_err, bs_err, text_match))

    # --- Summary ---
    print(f"\n\n{'=' * 70}")
    print("  SUMMARY")
    print(f"{'=' * 70}")

    ws_crashes = sum(1 for r in results if r[3] is not None)
    bs_crashes = sum(1 for r in results if r[4] is not None)
    mismatches = sum(1 for r in results if not r[5] and r[3] is None and r[4] is None)

    print(f"  Total cases:     {len(CASES)}")
    print(f"  WS crashes:      {ws_crashes}")
    print(f"  BS4 crashes:     {bs_crashes}")
    print(f"  Text mismatches: {mismatches} (different recovery, not necessarily wrong)")
    print(f"  Total WS time:   {total_ws*1e6:.1f} µs")
    print(f"  Total BS4 time:  {total_bs*1e6:.1f} µs")
    print(f"  Overall speedup: {total_bs/total_ws:.1f}x")


if __name__ == "__main__":
    main()
