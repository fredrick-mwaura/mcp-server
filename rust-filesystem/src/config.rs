use std::{env, fmt, fs, net::IpAddr, path::PathBuf, str::FromStr};

use sha2::{Digest, Sha256};
use subtle::ConstantTimeEq;

use crate::error::{AppError, AppResult};

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    ReadOnly,
    ReadWrite,
}

impl FromStr for Mode {
    type Err = AppError;

    fn from_str(value: &str) -> AppResult<Self> {
        match value {
            "read-only" => Ok(Self::ReadOnly),
            "read-write" => Ok(Self::ReadWrite),
            _ => Err(AppError::invalid_argument(
                "MCP_FS_MODE",
                "must be 'read-only' or 'read-write'",
            )),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Transport {
    Stdio,
    StreamableHttp,
}

impl FromStr for Transport {
    type Err = AppError;

    fn from_str(value: &str) -> AppResult<Self> {
        match value {
            "stdio" => Ok(Self::Stdio),
            "streamable-http" => Ok(Self::StreamableHttp),
            _ => Err(AppError::invalid_argument(
                "MCP_FS_TRANSPORT",
                "must be 'stdio' or 'streamable-http'",
            )),
        }
    }
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum LogFormat {
    Json,
    Text,
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum AuthMode {
    Auto,
    Bearer,
    None,
}

impl FromStr for AuthMode {
    type Err = AppError;

    fn from_str(value: &str) -> AppResult<Self> {
        match value {
            "auto" => Ok(Self::Auto),
            "bearer" => Ok(Self::Bearer),
            "none" => Ok(Self::None),
            _ => Err(AppError::invalid_argument(
                "MCP_FS_AUTH_MODE",
                "must be 'auto', 'bearer', or 'none'",
            )),
        }
    }
}

#[derive(Clone)]
pub struct SecretToken([u8; 32]);

impl SecretToken {
    fn new(value: &str) -> AppResult<Self> {
        if value.len() < 16 {
            return Err(AppError::invalid_argument(
                "MCP_FS_AUTH_TOKEN",
                "must contain at least 16 bytes",
            ));
        }
        Ok(Self(Sha256::digest(value.as_bytes()).into()))
    }

    pub fn matches(&self, candidate: &str) -> bool {
        let candidate: [u8; 32] = Sha256::digest(candidate.as_bytes()).into();
        bool::from(self.0.ct_eq(&candidate))
    }
}

impl fmt::Debug for SecretToken {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str("<redacted>")
    }
}

impl FromStr for LogFormat {
    type Err = AppError;

    fn from_str(value: &str) -> AppResult<Self> {
        match value {
            "json" => Ok(Self::Json),
            "text" => Ok(Self::Text),
            _ => Err(AppError::invalid_argument(
                "MCP_FS_LOG_FORMAT",
                "must be 'json' or 'text'",
            )),
        }
    }
}

#[derive(Debug, Clone)]
pub struct Settings {
    pub roots: Vec<PathBuf>,
    pub mode: Mode,
    pub transport: Transport,
    pub log_level: String,
    pub log_format: LogFormat,
    pub max_read_lines: usize,
    pub max_read_bytes: u64,
    pub max_output_bytes: usize,
    pub max_write_bytes: usize,
    pub allow_exec: bool,
    pub auth_mode: AuthMode,
    pub auth_token: Option<SecretToken>,
    pub allow_unsafe_remote: bool,
    pub allowed_hosts: Vec<String>,
    pub allowed_origins: Vec<String>,
    pub max_http_body_bytes: usize,
    pub host: String,
    pub port: u16,
}

impl Settings {
    pub fn from_env() -> AppResult<Self> {
        let roots = match env::var("MCP_FS_ROOTS") {
            Ok(raw) if !raw.trim().is_empty() => parse_roots(&raw)?,
            _ => vec![env::current_dir().map_err(AppError::io)?],
        };

        if roots.is_empty() {
            return Err(AppError::invalid_argument(
                "MCP_FS_ROOTS",
                "must contain at least one path",
            ));
        }

        let auth_mode = env_value("MCP_FS_AUTH_MODE", "auto").parse()?;
        let settings = Self {
            roots,
            mode: env_value("MCP_FS_MODE", "read-only").parse()?,
            transport: env_value("MCP_FS_TRANSPORT", "stdio").parse()?,
            log_level: env_value("MCP_FS_LOG_LEVEL", "INFO"),
            log_format: env_value("MCP_FS_LOG_FORMAT", "json").parse()?,
            max_read_lines: parse_positive("MCP_FS_MAX_READ_LINES", "200")?,
            max_read_bytes: parse_positive("MCP_FS_MAX_READ_BYTES", "1000000")?,
            max_output_bytes: parse_positive("MCP_FS_MAX_OUTPUT_BYTES", "1000000")?,
            max_write_bytes: parse_positive("MCP_FS_MAX_WRITE_BYTES", "5000000")?,
            allow_exec: parse_bool("MCP_FS_ALLOW_EXEC", "false")?,
            auth_mode,
            auth_token: read_auth_token()?,
            allow_unsafe_remote: parse_bool("MCP_FS_ALLOW_UNSAFE_REMOTE", "false")?,
            allowed_hosts: parse_optional_list("MCP_FS_ALLOWED_HOSTS")?,
            allowed_origins: parse_optional_list("MCP_FS_ALLOWED_ORIGINS")?,
            max_http_body_bytes: parse_positive("MCP_FS_MAX_HTTP_BODY_BYTES", "6000000")?,
            host: env_value("MCP_FS_HOST", "127.0.0.1"),
            port: parse_positive("MCP_FS_PORT", "8000")?,
        };
        settings.validate_security()?;
        Ok(settings)
    }

    pub fn effective_auth_mode(&self) -> AuthMode {
        match self.auth_mode {
            AuthMode::Auto
                if self.transport == Transport::StreamableHttp && !self.is_loopback_host() =>
            {
                AuthMode::Bearer
            }
            AuthMode::Auto => AuthMode::None,
            explicit => explicit,
        }
    }

    pub fn is_loopback_host(&self) -> bool {
        self.host.eq_ignore_ascii_case("localhost")
            || self
                .host
                .parse::<IpAddr>()
                .is_ok_and(|address| address.is_loopback())
    }

    pub fn effective_allowed_hosts(&self) -> Vec<String> {
        if !self.allowed_hosts.is_empty() {
            return self.allowed_hosts.clone();
        }
        let mut hosts = vec![
            self.host.clone(),
            "localhost".to_owned(),
            "127.0.0.1".to_owned(),
            "::1".to_owned(),
        ];
        hosts.sort();
        hosts.dedup();
        hosts
    }

    fn validate_security(&self) -> AppResult<()> {
        if self.transport != Transport::StreamableHttp {
            return Ok(());
        }
        let remote = !self.is_loopback_host();
        let auth = self.effective_auth_mode();
        if auth == AuthMode::Bearer && self.auth_token.is_none() {
            return Err(AppError::invalid_argument(
                "MCP_FS_AUTH_TOKEN",
                "bearer authentication requires MCP_FS_AUTH_TOKEN or MCP_FS_AUTH_TOKEN_FILE",
            ));
        }
        if remote && auth == AuthMode::None && !self.allow_unsafe_remote {
            return Err(AppError::invalid_argument(
                "MCP_FS_AUTH_MODE",
                "non-loopback HTTP requires bearer authentication; set a token or explicitly enable MCP_FS_ALLOW_UNSAFE_REMOTE=true for development only",
            ));
        }
        if remote && self.allow_exec && auth != AuthMode::Bearer {
            return Err(AppError::invalid_argument(
                "MCP_FS_ALLOW_EXEC",
                "remote execution requires bearer-authenticated HTTP",
            ));
        }
        Ok(())
    }
}

fn read_auth_token() -> AppResult<Option<SecretToken>> {
    let direct = env::var("MCP_FS_AUTH_TOKEN")
        .ok()
        .filter(|value| !value.trim().is_empty());
    let file = env::var("MCP_FS_AUTH_TOKEN_FILE")
        .ok()
        .filter(|value| !value.trim().is_empty());
    if direct.is_some() && file.is_some() {
        return Err(AppError::invalid_argument(
            "MCP_FS_AUTH_TOKEN",
            "set either MCP_FS_AUTH_TOKEN or MCP_FS_AUTH_TOKEN_FILE, not both",
        ));
    }
    let value = if let Some(path) = file {
        let metadata = fs::metadata(&path).map_err(AppError::io)?;
        if metadata.len() > 64 * 1024 {
            return Err(AppError::invalid_argument(
                "MCP_FS_AUTH_TOKEN_FILE",
                "token file exceeds 64 KiB",
            ));
        }
        Some(fs::read_to_string(path).map_err(AppError::io)?)
    } else {
        direct
    };
    value
        .map(|value| SecretToken::new(value.trim()))
        .transpose()
}

fn parse_optional_list(name: &str) -> AppResult<Vec<String>> {
    match env::var(name) {
        Ok(value) if !value.trim().is_empty() => parse_string_list(name, &value),
        _ => Ok(Vec::new()),
    }
}

fn parse_bool(name: &str, default: &str) -> AppResult<bool> {
    match env_value(name, default).to_ascii_lowercase().as_str() {
        "1" | "true" | "yes" | "on" => Ok(true),
        "0" | "false" | "no" | "off" => Ok(false),
        _ => Err(AppError::invalid_argument(name, "must be true or false")),
    }
}

fn env_value(name: &str, default: &str) -> String {
    env::var(name).unwrap_or_else(|_| default.to_owned())
}

fn parse_positive<T>(name: &str, default: &str) -> AppResult<T>
where
    T: FromStr + PartialOrd + From<u8> + Copy,
{
    let raw = env_value(name, default);
    let parsed = raw
        .parse::<T>()
        .map_err(|_| AppError::invalid_argument(name, "must be a positive integer"))?;
    if parsed < T::from(1) {
        return Err(AppError::invalid_argument(
            name,
            "must be a positive integer",
        ));
    }
    Ok(parsed)
}

fn parse_roots(raw: &str) -> AppResult<Vec<PathBuf>> {
    Ok(parse_string_list("MCP_FS_ROOTS", raw)?
        .into_iter()
        .map(PathBuf::from)
        .collect())
}

fn parse_string_list(name: &str, raw: &str) -> AppResult<Vec<String>> {
    let trimmed = raw.trim();
    if trimmed.starts_with('[') {
        return serde_json::from_str(trimmed).map_err(|error| {
            AppError::invalid_argument(name, format!("invalid JSON array: {error}"))
        });
    }
    Ok(trimmed
        .split(',')
        .map(str::trim)
        .filter(|item| !item.is_empty())
        .map(str::to_owned)
        .collect())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn parses_comma_separated_roots() {
        assert_eq!(
            parse_roots("/one, /two").expect("valid roots"),
            vec![PathBuf::from("/one"), PathBuf::from("/two")]
        );
    }

    #[test]
    fn parses_json_roots() {
        assert_eq!(
            parse_roots(r#"["/one", "/two"]"#).expect("valid roots"),
            vec![PathBuf::from("/one"), PathBuf::from("/two")]
        );
    }
}
