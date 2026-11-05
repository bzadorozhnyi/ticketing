mod common;

use std::io::{self, Write};
use std::sync::{Arc, Mutex, MutexGuard};

use common::db::dead_pool;
use common::service::{client, spawn_service};
use seat_hold::core::logging::init_with_writer;
use seat_hold::core::settings::LoggingSettings;
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

/// One test: a process has a single global subscriber.
#[tokio::test]
async fn continues_a_valid_trace_and_replaces_an_invalid_one() {
    let buffer = Buffer::default();
    init_with_writer(&LoggingSettings::default(), buffer.clone()).unwrap();
    let service = spawn_service(dead_pool().await).await;
    let trace_id = "4bf92f3577b34da6a3ce929d0e0e4736";

    client()
        .get(format!("{service}/health"))
        .header("traceparent", format!("00-{trace_id}-00f067aa0ba902b7-01"))
        .send()
        .await
        .unwrap();
    client()
        .get(format!("{service}/health"))
        .header("traceparent", "garbage-injected")
        .send()
        .await
        .unwrap();

    let logs = buffer.text();
    let lines: Vec<&str> = logs
        .lines()
        .filter(|line| line.contains("Request finished"))
        .collect();
    assert_eq!(lines.len(), 2, "{logs}");
    assert!(
        lines[0].contains(&format!(r#""trace_id":"{trace_id}""#)),
        "{logs}"
    );
    assert!(lines[1].contains(r#""trace_id":""#), "{logs}");
    assert!(!logs.contains("garbage-injected"), "{logs}");
}
