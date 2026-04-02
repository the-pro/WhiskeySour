use criterion::{black_box, criterion_group, criterion_main, Criterion};
use whiskysour_core::parser::html::{parse_html, ParseOptions};
use whiskysour_core::query::find::{find_all, FindOptions, NameFilter};
use whiskysour_core::node::DOCUMENT_ID;

fn make_doc() -> whiskysour_core::document::Document {
    let html = "<html><body>".to_owned()
        + &"<div class=\"item\"><p class=\"text\">Hello</p></div>".repeat(500)
        + "</body></html>";
    parse_html(&html, ParseOptions::default())
}

fn bench_find_all_tag(c: &mut Criterion) {
    let doc = make_doc();
    c.bench_function("find_all_div", |b| {
        b.iter(|| {
            let mut opts = FindOptions::default();
            opts.name = Some(NameFilter::Exact("div".into()));
            find_all(black_box(&doc), DOCUMENT_ID, &opts)
        })
    });
}

criterion_group!(benches, bench_find_all_tag);
criterion_main!(benches);
