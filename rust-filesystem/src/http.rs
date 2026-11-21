use std::sync::Arc;

use anyhow::Context;
use axum::{
    Router,
    body::Body,
    http::{Request, StatusCode, header},
    middleware::{self, Next},
    response::{IntoResponse, Response},
    routing::get,
};
use rmcp::transport::streamable_http_server::{
    StreamableHttpServerConfig, StreamableHttpService, session::local::LocalSessionManager,
};
use tokio_util::sync::CancellationToken;

use crate::{
    FileSystemServer, Settings,
    config::{AuthMode, SecretToken},
};

#[derive(Clone)]
struct AuthState {
    token: Arc<SecretToken>,
}

pub async fn serve(settings: &Settings, server: FileSystemServer) -> anyhow::Result<()> {
    let cancellation = CancellationToken::new();
    let mut http_config = StreamableHttpServerConfig::default()
        .with_cancellation_token(cancellation.child_token())
        .with_allowed_hosts(settings.effective_allowed_hosts())
        .with_max_request_body_bytes(settings.max_http_body_bytes);
    http_config = if settings.allowed_origins.is_empty() {
        http_config.enforce_origin_validation()
    } else {
        http_config.with_allowed_origins(settings.allowed_origins.clone())
    };

    let mcp_service: StreamableHttpService<FileSystemServer, LocalSessionManager> =
        StreamableHttpService::new(
            move || Ok(server.clone()),
            LocalSessionManager::default().into(),
            http_config,
        );
    let mut protected = Router::new().nest_service("/mcp", mcp_service);
    if settings.effective_auth_mode() == AuthMode::Bearer {
        let token = settings.auth_token.clone().ok_or_else(|| {
            anyhow::anyhow!("validated bearer authentication has no configured token")
        })?;
        protected = protected.layer(middleware::from_fn_with_state(
            AuthState {
                token: Arc::new(token),
            },
            authenticate,
        ));
    }
    let application = Router::new().route("/health", get(health)).merge(protected);
    let listener = tokio::net::TcpListener::bind((settings.host.as_str(), settings.port))
        .await
        .with_context(|| format!("failed to bind {}:{}", settings.host, settings.port))?;
    tracing::info!(
        transport = "streamable-http",
        host = %settings.host,
        port = settings.port,
        auth = ?settings.effective_auth_mode(),
        "server starting"
    );
    if !settings.is_loopback_host() && settings.effective_auth_mode() == AuthMode::None {
        tracing::error!(
            "UNSAFE REMOTE HTTP ENABLED: requests are not authenticated because MCP_FS_ALLOW_UNSAFE_REMOTE=true"
        );
    }

    axum::serve(listener, application)
        .with_graceful_shutdown({
            let cancellation = cancellation.clone();
            async move {
                if let Err(error) = tokio::signal::ctrl_c().await {
                    tracing::error!(%error, "failed to install shutdown signal handler");
                }
                cancellation.cancel();
            }
        })
        .await
        .context("streamable HTTP server failed")
}

async fn authenticate(
    axum::extract::State(state): axum::extract::State<AuthState>,
    request: Request<Body>,
    next: Next,
) -> Response {
    let authorized = request
        .headers()
        .get(header::AUTHORIZATION)
        .and_then(|value| value.to_str().ok())
        .and_then(|value| value.strip_prefix("Bearer "))
        .is_some_and(|candidate| state.token.matches(candidate));
    if authorized {
        next.run(request).await
    } else {
        let mut response = (StatusCode::UNAUTHORIZED, "Unauthorized").into_response();
        response.headers_mut().insert(
            header::WWW_AUTHENTICATE,
            header::HeaderValue::from_static("Bearer"),
        );
        response
    }
}

async fn health() -> &'static str {
    "OK"
}
