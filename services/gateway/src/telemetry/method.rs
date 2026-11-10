use axum::http::Method;

/// `http.request.method` as OpenTelemetry names it: a known method as is, anything else as
/// `_OTHER`, so a client cannot add labels by inventing methods. Static, so a request
/// allocates nothing for it.
#[must_use]
pub fn method_name(method: &Method) -> &'static str {
    match *method {
        Method::GET => "GET",
        Method::HEAD => "HEAD",
        Method::POST => "POST",
        Method::PUT => "PUT",
        Method::DELETE => "DELETE",
        Method::PATCH => "PATCH",
        Method::OPTIONS => "OPTIONS",
        Method::CONNECT => "CONNECT",
        Method::TRACE => "TRACE",
        _ => "_OTHER",
    }
}
