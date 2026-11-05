mod common;

use std::io::{self, Write};
use std::panic::catch_unwind;
use std::sync::{Arc, Mutex, MutexGuard};

use axum::Router;
use axum::routing::get;
use common::app::{spawn_gateway, test_settings};
use common::client::client;
use common::upstream::spawn_upstream;
use gateway::logging::init_with_writer;
use tracing_subscriber::fmt::MakeWriter;

#[derive(Clone, Default)]
struct Buffer(Arc<Mutex<Vec<u8>>>);

impl Buffer {
    fn lock(&self) -> MutexGuard<'_, Vec<u8>> {
        self.0.lock().expect("buffer lock is poisoned")
    }

    fn text(&self) -> String {
        String::from_utf8(self.lock().clone()).expect("logs are UTF-8")
    }
}

impl Write for Buffer {
    fn write(&mut self, bytes: &[u8]) -> io::Result<usize> {
        self.lock().extend_from_slice(bytes);
        Ok(bytes.len())
    }

    fn flush(&mut self) -> io::Result<()> {
        Ok(())
    }
}

impl MakeWriter<'_> for Buffer {
    type Writer = Self;

    fn make_writer(&self) -> Self::Writer {
        self.clone()
    }
}

/// One test: a process has a single global subscriber and panic hook.
#[tokio::test]
async fn writes_json_logs_with_the_trace_id_sent_upstream() {
    let routes = Router::new().route("/api/v1/events", get(async || "ok"));
    let upstream = spawn_upstream(routes).await;
    let settings = test_settings(&upstream.url);
    let buffer = Buffer::default();
    init_with_writer(&settings.logging, buffer.clone()).unwrap();
    let gateway = spawn_gateway(&settings).await;

    client()
        .get(format!("{gateway}/api/v1/events?secret=1"))
        .send()
        .await
        .unwrap();

    let received = upstream.received();
    let traceparent = received[0].headers["traceparent"].to_str().unwrap();
    let trace_id = traceparent.split('-').nth(1).unwrap();
    let logs = buffer.text();
    let line = logs
        .lines()
        .find(|line| line.contains("Request finished"))
        .unwrap();
    assert!(line.starts_with('{'), "{line}");
    assert!(
        line.contains(&format!(r#""trace_id":"{trace_id}""#)),
        "{line}"
    );
    assert!(line.contains(r#""path":"/api/v1/events""#), "{line}");
    assert!(line.contains(r#""status":200"#), "{line}");
    assert!(!line.contains(r#""duration_ms":""#), "{line}");
    assert!(!line.contains("secret"), "{line}");

    client()
        .get(format!("{gateway}/api/v1/events"))
        .header("authorization", "Bearer forged.token.value")
        .send()
        .await
        .unwrap();

    let logs = buffer.text();
    let line = logs
        .lines()
        .find(|line| line.contains("Token rejected"))
        .unwrap();
    assert!(line.contains(r#""reason":"#), "{line}");
    assert!(line.contains(r#""trace_id":"#), "{line}");
    assert!(!logs.contains("forged.token.value"), "{logs}");

    let span = tracing::info_span!("request", trace_id = "panicking-trace");
    let result = span.in_scope(|| catch_unwind(|| panic!("boom")));

    assert!(result.is_err());
    let logs = buffer.text();
    let line = logs.lines().find(|line| line.contains("boom")).unwrap();
    assert!(line.contains(r#""level":"ERROR""#), "{line}");
    assert!(line.contains(r#""trace_id":"panicking-trace""#), "{line}");
    assert!(line.contains("tests/logs.rs"), "{line}");
}
