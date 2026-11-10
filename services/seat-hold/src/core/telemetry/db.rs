use opentelemetry::global::BoxedSpan;
use opentelemetry::trace::{SpanKind, Tracer};
use opentelemetry::{Context, KeyValue};

use crate::core::telemetry::init::tracer;

/// A client span for one statement, a child of the request's span. It ends when it is
/// dropped, so a repository holds it until its query returns:
/// `let _span = db_span("INSERT", "holds");`.
#[must_use]
pub fn db_span(operation: &'static str, table: &'static str) -> BoxedSpan {
    tracer()
        .span_builder(format!("{operation} {table}"))
        .with_kind(SpanKind::Client)
        .with_attributes([
            KeyValue::new("db.system.name", "postgresql"),
            KeyValue::new("db.operation.name", operation),
            KeyValue::new("db.collection.name", table),
        ])
        .start_with_context(tracer(), &Context::current())
}
