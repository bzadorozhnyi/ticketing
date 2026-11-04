use std::time::Duration;

use axum::extract::{Request, State};
use axum::http::header::{AUTHORIZATION, HeaderMap, HeaderValue};
use axum::middleware::Next;
use axum::response::Response;
use jsonwebtoken::errors::Error as TokenError;
use jsonwebtoken::{
    Algorithm, DecodingKey, EncodingKey, Header, Validation, decode, encode, get_current_timestamp,
};
use serde::{Deserialize, Serialize};

use crate::errors::GatewayError;

const ALGORITHM: Algorithm = Algorithm::HS256;

pub const TOKEN_LIFETIME: Duration = Duration::from_mins(15);

#[derive(Deserialize, Serialize)]
pub struct Claims {
    pub sub: String,
    pub exp: u64,
}

#[derive(Clone, Debug)]
pub struct AuthenticatedUser(pub HeaderValue);

#[derive(Clone)]
pub struct TokenVerifier {
    key: DecodingKey,
    validation: Validation,
}

impl TokenVerifier {
    /// The gateway both issues and checks tokens, so clocks cannot drift apart
    /// and the library's default 60 s of leeway after `exp` is not needed.
    #[must_use]
    pub fn new(secret: &str) -> Self {
        let mut validation = Validation::new(ALGORITHM);
        validation.leeway = 0;
        Self {
            key: DecodingKey::from_secret(secret.as_bytes()),
            validation,
        }
    }

    fn verify(&self, token: &str) -> Result<AuthenticatedUser, TokenError> {
        let claims = decode::<Claims>(token, &self.key, &self.validation)?.claims;
        let user_id = HeaderValue::try_from(claims.sub)
            .map_err(|_| TokenError::from(jsonwebtoken::errors::ErrorKind::InvalidSubject))?;
        Ok(AuthenticatedUser(user_id))
    }
}

pub fn issue_token(secret: &str, user_id: &str, lifetime: Duration) -> Result<String, TokenError> {
    let claims = Claims {
        sub: user_id.to_owned(),
        exp: get_current_timestamp() + lifetime.as_secs(),
    };
    let key = EncodingKey::from_secret(secret.as_bytes());
    encode(&Header::new(ALGORITHM), &claims, &key)
}

/// The reason goes to the log only: a client gains nothing from it, an attacker does.
pub async fn authenticate(
    State(verifier): State<TokenVerifier>,
    mut request: Request,
    next: Next,
) -> Result<Response, GatewayError> {
    let token = bearer_token(request.headers()).ok_or(GatewayError::Unauthorized)?;
    let user = verifier.verify(token).map_err(|error| {
        tracing::info!(reason = %error, "Token rejected");
        GatewayError::Unauthorized
    })?;
    request.extensions_mut().insert(user);
    Ok(next.run(request).await)
}

fn bearer_token(headers: &HeaderMap) -> Option<&str> {
    let value = headers.get(AUTHORIZATION)?.to_str().ok()?;
    let (scheme, token) = value.split_once(' ')?;
    scheme.eq_ignore_ascii_case("bearer").then_some(token)
}
