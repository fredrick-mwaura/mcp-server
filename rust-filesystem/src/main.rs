use std::process::ExitCode;

use anyhow::Context;
use mcp_filesystem_server::{FileSystemServer, Settings, config::LogFormat};
use rmcp::{ServiceExt, transport::stdio};
use tracing_subscriber::EnvFilter;

#[tokio::main]
async fn main() -> ExitCode {
    let arguments = std::env::args().skip(1).collect::<Vec<_>>();
    if arguments.as_slice() == ["--version"] {
        println!("mcp-filesystem-server {}", env!("CARGO_PKG_VERSION"));
        return ExitCode::SUCCESS;
    }
    let check_config = arguments.as_slice() == ["--check-config"];
    if !arguments.is_empty() && !check_config {
        eprintln!("Usage: mcp-filesystem-server [--version|--check-config]");
        return ExitCode::from(2);
    }
    let settings = match Settings::from_env() {
        Ok(settings) => settings,
        Err(error) => {
            eprintln!("Configuration error: {error}");
            return ExitCode::from(2);
        }
    };
    if check_config {
        println!("Configuration valid.");
        return ExitCode::SUCCESS;
    }
    configure_logging(&settings);
    let server = match FileSystemServer::new(settings.clone()) {
        Ok(server) => server,
        Err(error) => {
            eprintln!("Configuration error: {error}");
            return ExitCode::from(2);
        }
    };
    match run(settings, server).await {
        Ok(()) => ExitCode::SUCCESS,
        Err(error) => {
            eprintln!("{error:#}");
            ExitCode::FAILURE
        }
    }
}

async fn run(settings: Settings, server: FileSystemServer) -> anyhow::Result<()> {
    match settings.transport {
        mcp_filesystem_server::config::Transport::Stdio => {
            tracing::info!(transport = "stdio", "server starting");
            let service = server
                .serve(stdio())
                .await
                .context("failed to serve stdio")?;
            service.waiting().await.context("stdio service failed")?;
        }
        mcp_filesystem_server::config::Transport::StreamableHttp => {
            mcp_filesystem_server::http::serve(&settings, server).await?;
        }
    }
    Ok(())
}

fn configure_logging(settings: &Settings) {
    let filter = EnvFilter::try_new(&settings.log_level).unwrap_or_else(|_| EnvFilter::new("info"));
    let builder = tracing_subscriber::fmt()
        .with_env_filter(filter)
        .with_writer(std::io::stderr)
        .with_ansi(false);
    match settings.log_format {
        LogFormat::Json => builder.json().init(),
        LogFormat::Text => builder.init(),
    }
}
