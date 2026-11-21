use std::time::{Duration, Instant};

use regex::Regex;
use serde_json::{Value, json};

use crate::{
    error::{AppError, AppResult},
    process,
    workspace::ExistingPath,
};

pub fn run_test(
    path: &ExistingPath,
    command: &str,
    timeout_seconds: f64,
    distill: bool,
    max_output_bytes: usize,
) -> AppResult<Value> {
    if !path.path().is_dir() {
        return Err(AppError::PathNotDirectory(
            path.path().display().to_string(),
        ));
    }
    let arguments = match shell_words::split(command) {
        Ok(arguments) => arguments,
        Err(error) => {
            return Ok(json!({
                "status": "error",
                "command": command,
                "error": format!("Invalid command quoting: {error}"),
            }));
        }
    };
    let Some((program, arguments)) = arguments.split_first() else {
        return Ok(json!({
            "status": "error",
            "command": command,
            "error": "Command string cannot be empty.",
        }));
    };
    let timeout_seconds = if timeout_seconds.is_finite() {
        timeout_seconds.clamp(1.0, 300.0)
    } else {
        return Err(AppError::invalid_argument(
            "timeout",
            "must be a finite number",
        ));
    };
    let started = Instant::now();
    let output = match process::run_bounded(
        program,
        arguments,
        path.path(),
        Duration::from_secs_f64(timeout_seconds),
        max_output_bytes,
        &[],
    ) {
        Ok(output) => output,
        Err(AppError::Io(error)) if error.kind() == std::io::ErrorKind::NotFound => {
            return Ok(json!({
                "status": "error",
                "command": command,
                "error": format!("Executable '{program}' not found on system PATH."),
            }));
        }
        Err(error) => return Err(error),
    };
    let duration = started.elapsed().as_secs_f64();
    if output.timed_out {
        return Ok(json!({
            "status": "timeout",
            "command": command,
            "timed_out": true,
            "timeout_seconds": timeout_seconds,
            "duration_seconds": rounded_seconds(duration),
            "summary": format!("Test execution timed out after {timeout_seconds}s."),
            "detail": format!(
                "Process group was terminated after exceeding the {timeout_seconds}s timeout limit. Check for infinite loops, deadlocks, or hanging network calls."
            ),
            "truncated": output.stdout_truncated || output.stderr_truncated,
        }));
    }
    let exit_code = output.status.code().unwrap_or(-1);
    let truncated = output.stdout_truncated || output.stderr_truncated;
    if distill {
        let mut result =
            distill_test_output(&output.stdout, &output.stderr, exit_code, duration, 120);
        if let Some(object) = result.as_object_mut() {
            object.insert("command".to_owned(), Value::String(command.to_owned()));
            if truncated {
                object.insert("truncated".to_owned(), Value::Bool(true));
            }
        }
        Ok(result)
    } else {
        Ok(json!({
            "status": if exit_code == 0 { "passed" } else { "failed" },
            "command": command,
            "exit_code": exit_code,
            "duration_seconds": rounded_seconds(duration),
            "stdout": output.stdout,
            "stderr": output.stderr,
            "truncated": truncated,
        }))
    }
}

pub fn distill_test_output(
    stdout: &str,
    stderr: &str,
    return_code: i32,
    duration_seconds: f64,
    max_failure_lines: usize,
) -> Value {
    let combined = if stderr.trim().is_empty() {
        stdout.to_owned()
    } else {
        format!("{stdout}\n{stderr}")
    };
    let summary = extract_summary(&combined);
    let counts = extract_counts(&combined, summary.as_deref(), return_code);
    let success = return_code == 0 && counts.failed == 0 && counts.errors == 0;
    if success {
        return json!({
            "status": "passed",
            "exit_code": 0,
            "duration_seconds": rounded_seconds(duration_seconds),
            "summary": summary.unwrap_or_else(|| "All tests passed successfully.".to_owned()),
            "counts": counts,
            "failures": [],
            "distilled_output": null,
        });
    }

    let mut lines = extract_failure_lines(&combined, max_failure_lines);
    let truncated = lines.len() >= max_failure_lines;
    if truncated {
        lines.push(format!(
            "\n[... Failure trace truncated at {max_failure_lines} lines to preserve tokens. Run with targeted test path to isolate. ...]"
        ));
    }
    json!({
        "status": "failed",
        "exit_code": return_code,
        "duration_seconds": rounded_seconds(duration_seconds),
        "summary": summary.unwrap_or_else(|| format!("Tests failed with exit code {return_code}.")),
        "counts": counts,
        "truncated": truncated,
        "distilled_output": lines.join("\n"),
    })
}

#[derive(Debug, serde::Serialize)]
struct TestCounts {
    passed: usize,
    failed: usize,
    skipped: usize,
    errors: usize,
}

fn extract_summary(output: &str) -> Option<String> {
    let pytest = Regex::new(r"(?m)=+ (.* in [\d.]+s.*) =+").expect("pytest summary regex");
    if let Some(summary) = pytest.captures(output).and_then(|captures| captures.get(1)) {
        return Some(summary.as_str().trim().to_owned());
    }
    let unittest = Regex::new(r"Ran (\d+) tests? in ([\d.]+)s").expect("unittest summary regex");
    unittest.captures(output).map(|captures| {
        format!(
            "Ran {} tests in {}",
            captures.get(1).map_or("0", |value| value.as_str()),
            captures.get(2).map_or("0", |value| value.as_str())
        )
    })
}

fn extract_counts(output: &str, summary: Option<&str>, return_code: i32) -> TestCounts {
    if output.contains("Ran ") {
        let total = capture_count(output, r"Ran (\d+) tests?");
        let failed = capture_count(output, r"failures=(\d+)");
        let errors = capture_count(output, r"errors=(\d+)");
        let skipped = capture_count(output, r"skipped=(\d+)");
        return TestCounts {
            passed: total.saturating_sub(failed + errors + skipped),
            failed,
            skipped,
            errors,
        };
    }
    let value = summary.unwrap_or(output);
    let failed = capture_count(value, r"(\d+)\s+failed");
    let errors = capture_count(value, r"(\d+)\s+error");
    TestCounts {
        passed: capture_count(value, r"(\d+)\s+passed"),
        failed: if failed == 0 && return_code != 0 {
            1
        } else {
            failed
        },
        skipped: capture_count(value, r"(\d+)\s+skipped"),
        errors,
    }
}

fn capture_count(value: &str, pattern: &str) -> usize {
    Regex::new(pattern)
        .expect("count regex")
        .captures(value)
        .and_then(|captures| captures.get(1))
        .and_then(|value| value.as_str().parse().ok())
        .unwrap_or(0)
}

fn extract_failure_lines(output: &str, limit: usize) -> Vec<String> {
    let failure_header = Regex::new(r"=+\s+FAILURES\s+=+").expect("failure header regex");
    if let Some(header) = failure_header.find(output) {
        let footer =
            Regex::new(r"=+\s+(?:short test summary info|warnings summary|\d+ failed)\s+=+")
                .expect("failure footer regex");
        let remainder = &output[header.end()..];
        let body = footer
            .find(remainder)
            .map_or(remainder, |footer| &remainder[..footer.start()]);
        return body.trim().lines().take(limit).map(str::to_owned).collect();
    }

    let mut captured = Vec::new();
    let mut active = false;
    for line in output.lines() {
        if [
            "FAIL:",
            "ERROR:",
            "Traceback (most recent call last):",
            "FAILED ",
        ]
        .iter()
        .any(|marker| line.contains(marker))
        {
            active = true;
        }
        if active && captured.len() < limit {
            captured.push(line.to_owned());
        }
    }
    if captured.is_empty() {
        captured = output
            .lines()
            .filter(|line| !line.trim().is_empty())
            .rev()
            .take(limit)
            .map(str::to_owned)
            .collect::<Vec<_>>();
        captured.reverse();
    }
    captured
}

fn rounded_seconds(value: f64) -> f64 {
    (value * 100.0).round() / 100.0
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn distills_passing_pytest_output() {
        let result = distill_test_output(
            "================ 3 passed, 1 skipped in 0.25s ================",
            "",
            0,
            0.25,
            120,
        );
        assert_eq!(result["status"], "passed");
        assert_eq!(result["counts"]["passed"], 3);
        assert_eq!(result["counts"]["skipped"], 1);
    }

    #[test]
    fn distills_failure_section() {
        let output = "=== FAILURES ===\n___ test_x ___\nE assert 1 == 2\n=== short test summary info ===\n1 failed in 0.1s";
        let result = distill_test_output(output, "", 1, 0.1, 120);
        assert_eq!(result["status"], "failed");
        assert!(
            result["distilled_output"]
                .as_str()
                .unwrap()
                .contains("assert")
        );
    }
}
