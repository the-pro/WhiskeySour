//! Query benchmarks: find_all, traversal, CSS select and serialisation on a 500-row table.

use criterion::{black_box, criterion_group, criterion_main, Criterion};
use whiskysour_core::document::Document;
use whiskysour_core::node::DOCUMENT_ID;
use whiskysour_core::parser::{parse_html, ParseOptions};
use whiskysour_core::query::{
    find_all, find_one, select, select_limit, select_one, AttrFilter, AttrValueFilter, FindOptions,
    NameFilter,
};
use whiskysour_core::serialize::{prettify_node, serialize_node};
use whiskysour_core::traversal::DescendantsPreOrder;

fn make_doc() -> Document {
    let mut html = String::from("<html><body><table><tbody>");
    for i in 0..500 {
        let cls = if i % 2 == 0 { "even" } else { "odd" };
        html.push_str(&format!(
            "<tr class=\"{cls}\" id=\"row-{i}\"><td class=\"id\">{i}</td>\
             <td class=\"name\">Item &amp; {i}</td><td><a href=\"/p/{i}\">link</a></td></tr>"
        ));
    }
    html.push_str("</tbody></table></body></html>");
    parse_html(&html, ParseOptions::default())
}

fn bench_find_all(c: &mut Criterion) {
    let doc = make_doc();
    c.bench_function("find_all_td", |b| {
        let opts = FindOptions {
            name: Some(NameFilter::Exact("td".into())),
            ..Default::default()
        };
        b.iter(|| find_all(black_box(&doc), DOCUMENT_ID, &opts))
    });
    c.bench_function("find_all_class", |b| {
        let opts = FindOptions {
            name: Some(NameFilter::Exact("tr".into())),
            attrs: vec![AttrFilter {
                name: "class".into(),
                value: AttrValueFilter::ContainsToken("odd".into()),
            }],
            ..Default::default()
        };
        b.iter(|| find_all(black_box(&doc), DOCUMENT_ID, &opts))
    });
}

fn bench_traversal(c: &mut Criterion) {
    let doc = make_doc();
    // Allocation-free pre-order walk over the compact 56-byte nodes.
    c.bench_function("descendants_count", |b| {
        b.iter(|| DescendantsPreOrder::new(black_box(&doc), DOCUMENT_ID).count())
    });
    c.bench_function("find_one_last_row", |b| {
        let opts = FindOptions {
            attrs: vec![AttrFilter {
                name: "id".into(),
                value: AttrValueFilter::Exact("row-499".into()),
            }],
            ..Default::default()
        };
        b.iter(|| find_one(black_box(&doc), DOCUMENT_ID, &opts))
    });
    c.bench_function("get_text", |b| {
        b.iter(|| black_box(&doc).get_text(DOCUMENT_ID))
    });
}

fn bench_select(c: &mut Criterion) {
    let doc = make_doc();
    for css in [
        "tr.odd > td",
        "tbody td a",
        "tr:nth-child(2n)",
        "tr:nth-last-child(odd)",
    ] {
        c.bench_function(&format!("select `{css}`"), |b| {
            b.iter(|| select(black_box(&doc), DOCUMENT_ID, css))
        });
    }
    c.bench_function("select_limit `td` x5", |b| {
        b.iter(|| select_limit(black_box(&doc), DOCUMENT_ID, "td", 5))
    });
    c.bench_function("select_one `#row-499`", |b| {
        b.iter(|| select_one(black_box(&doc), DOCUMENT_ID, "#row-499"))
    });
}

fn bench_serialize(c: &mut Criterion) {
    let doc = make_doc();
    c.bench_function("serialize", |b| {
        b.iter(|| serialize_node(black_box(&doc), DOCUMENT_ID))
    });
    c.bench_function("prettify", |b| {
        b.iter(|| prettify_node(black_box(&doc), DOCUMENT_ID, 2))
    });
}

criterion_group!(
    benches,
    bench_find_all,
    bench_traversal,
    bench_select,
    bench_serialize
);
criterion_main!(benches);
