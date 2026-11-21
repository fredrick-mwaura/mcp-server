use std::{
    io::{self, Read},
    path::Path,
    process::{Command, ExitStatus, Stdio},
    thread,
    time::{Duration, Instant},
};

use command_group::CommandGroup;

use crate::error::{AppError, AppResult};

#[derive(Debug)]
pub struct ProcessOutput {
    pub status: ExitStatus,
    pub stdout: String,
    pub stderr: String,
    pub stdout_truncated: bool,
    pub stderr_truncated: bool,
    pub stdout_total_bytes: u64,
    pub stderr_total_bytes: u64,
    pub stdout_total_lines: usize,
    pub stderr_total_lines: usize,
    pub timed_out: bool,
}

pub fn run_bounded(
    program: &str,
    arguments: &[String],
    cwd: &Path,
    timeout: Duration,
    max_stream_bytes: usize,
    environment: &[(&str, &str)],
) -> AppResult<ProcessOutput> {
    let mut command = Command::new(program);
    command
        .args(arguments)
        .current_dir(cwd)
        .stdin(Stdio::null())
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .env_remove("MCP_FS_AUTH_TOKEN")
        .env_remove("MCP_FS_AUTH_TOKEN_FILE");
    for (key, value) in environment {
        command.env(key, value);
    }

    let mut child = command.group_spawn().map_err(AppError::io)?;
    let stdout = child
        .inner()
        .stdout
        .take()
        .ok_or_else(|| AppError::io(io::Error::other("child stdout was not captured")))?;
    let stderr = child
        .inner()
        .stderr
        .take()
        .ok_or_else(|| AppError::io(io::Error::other("child stderr was not captured")))?;
    let stdout_thread = thread::spawn(move || capture(stdout, max_stream_bytes));
    let stderr_thread = thread::spawn(move || capture(stderr, max_stream_bytes));

    let deadline = Instant::now() + timeout;
    let mut timed_out = false;
    let status = loop {
        if let Some(status) = child.try_wait().map_err(AppError::io)? {
            break status;
        }
        if Instant::now() >= deadline {
            timed_out = true;
            child.kill().map_err(AppError::io)?;
            break child.wait().map_err(AppError::io)?;
        }
        thread::sleep(Duration::from_millis(10));
    };

    let stdout_capture = stdout_thread
        .join()
        .map_err(|_| AppError::io(io::Error::other("stdout reader thread panicked")))?
        .map_err(AppError::io)?;
    let stderr_capture = stderr_thread
        .join()
        .map_err(|_| AppError::io(io::Error::other("stderr reader thread panicked")))?
        .map_err(AppError::io)?;

    Ok(ProcessOutput {
        status,
        stdout: String::from_utf8_lossy(&stdout_capture.kept).into_owned(),
        stderr: String::from_utf8_lossy(&stderr_capture.kept).into_owned(),
        stdout_truncated: stdout_capture.truncated,
        stderr_truncated: stderr_capture.truncated,
        stdout_total_bytes: stdout_capture.total_bytes,
        stderr_total_bytes: stderr_capture.total_bytes,
        stdout_total_lines: stdout_capture.total_lines(),
        stderr_total_lines: stderr_capture.total_lines(),
        timed_out,
    })
}

struct Capture {
    kept: Vec<u8>,
    truncated: bool,
    total_bytes: u64,
    newline_count: usize,
    last_byte: Option<u8>,
}

impl Capture {
    fn total_lines(&self) -> usize {
        self.newline_count + usize::from(matches!(self.last_byte, Some(byte) if byte != b'\n'))
    }
}

fn capture(mut reader: impl Read, limit: usize) -> io::Result<Capture> {
    let mut kept = Vec::with_capacity(limit.min(64 * 1024));
    let mut buffer = [0u8; 8192];
    let mut truncated = false;
    let mut total_bytes = 0u64;
    let mut newline_count = 0usize;
    let mut last_byte = None;
    loop {
        let count = reader.read(&mut buffer)?;
        if count == 0 {
            break;
        }
        total_bytes = total_bytes.saturating_add(count as u64);
        newline_count = newline_count.saturating_add(bytecount::count(&buffer[..count], b'\n'));
        last_byte = buffer.get(count - 1).copied();
        let available = limit.saturating_sub(kept.len());
        let selected = count.min(available);
        kept.extend_from_slice(&buffer[..selected]);
        truncated |= selected < count;
    }
    Ok(Capture {
        kept,
        truncated,
        total_bytes,
        newline_count,
        last_byte,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[cfg(unix)]
    #[test]
    fn bounds_output_while_draining_process() {
        let result = run_bounded(
            "sh",
            &["-c".to_owned(), "printf 1234567890".to_owned()],
            Path::new("."),
            Duration::from_secs(2),
            4,
            &[],
        )
        .expect("process result");
        assert_eq!(result.stdout, "1234");
        assert!(result.stdout_truncated);
    }

    #[cfg(unix)]
    #[test]
    fn kills_process_group_at_timeout() {
        let result = run_bounded(
            "sh",
            &["-c".to_owned(), "sleep 10".to_owned()],
            Path::new("."),
            Duration::from_millis(30),
            1024,
            &[],
        )
        .expect("process result");
        assert!(result.timed_out);
    }
}
