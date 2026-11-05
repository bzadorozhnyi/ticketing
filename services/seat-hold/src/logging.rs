use std::error::Error;
use std::io;

use tracing_subscriber::fmt::MakeWriter;

use crate::settings::{LogFormat, LoggingSettings};

pub fn init(settings: &LoggingSettings) -> Result<(), Box<dyn Error + Send + Sync>> {
    init_with_writer(settings, io::stdout)
}

pub fn init_with_writer<W>(
    settings: &LoggingSettings,
    writer: W,
) -> Result<(), Box<dyn Error + Send + Sync>>
where
    W: for<'writer> MakeWriter<'writer> + Send + Sync + 'static,
{
    let builder = tracing_subscriber::fmt()
        .with_max_level(settings.level)
        .with_writer(writer);
    match settings.format {
        LogFormat::Json => builder
            .json()
            .flatten_event(true)
            .with_current_span(true)
            .with_span_list(false)
            .try_init(),
        LogFormat::Pretty => builder.pretty().try_init(),
    }?;
    install_panic_hook();
    Ok(())
}

/// The default hook prints plain text to stderr, outside the JSON log stream.
fn install_panic_hook() {
    std::panic::set_hook(Box::new(|info| {
        let message = info.payload_as_str().unwrap_or("non-string panic payload");
        let location = info.location().map(ToString::to_string);
        tracing::error!(location, "Panic: {message}");
    }));
}
