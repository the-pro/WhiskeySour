use criterion::{black_box, criterion_group, criterion_main, Criterion};
use whiskysour_core::parser::html::{parse_html, ParseOptions};

fn bench_parse_small(c: &mut Criterion) {
    let html = "<html><head><title>Test</title></head><body><p class=\"intro\">Hello, world!</p></body></html>";
    c.bench_function("parse_small", |b| {
        b.iter(|| parse_html(black_box(html), ParseOptions::default()))
    });
}

fn bench_parse_medium(c: &mut Criterion) {
    let html = "<html><body>".to_owned()
        + &"<div class=\"item\"><p>Text</p></div>".repeat(1000)
        + "</body></html>";
    c.bench_function("parse_medium", |b| {
        b.iter(|| parse_html(black_box(&html), ParseOptions::default()))
    });
}

criterion_group!(benches, bench_parse_small, bench_parse_medium);
criterion_main!(benches);
