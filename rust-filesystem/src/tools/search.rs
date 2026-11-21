use std::{
    collections::VecDeque,
    fs::File,
    io::{BufRead, BufReader},
    path::Path,
};

use globset::Glob;
use regex::Regex;
use serde::Serialize;
use walkdir::WalkDir;

use crate::{
    error::{AppError, AppResult},
    workspace::ExistingPath,
};

#[derive(Debug, Serialize)]
pub struct SearchResult {
    pub root: String,
    pub query: String,
    pub match_count: usize,
    pub truncated: bool,
    pub matches: Vec<FileMatches>,
}

#[derive(Debug, Serialize)]
pub struct FileMatches {
    pub file: String,
    pub hits: Vec<SearchHit>,
}

#[derive(Debug, Serialize)]
pub struct SearchHit {
    pub line: usize,
    pub text: String,
    pub is_match: bool,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub context_before: Vec<ContextLine>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub context_after: Vec<ContextLine>,
}

#[derive(Debug, Clone, Serialize)]
pub struct ContextLine {
    pub line: usize,
    pub text: String,
}

pub struct SearchOptions<'a> {
    pub regex: bool,
    pub include_globs: &'a [String],
    pub max_results: usize,
    pub context_lines: usize,
    pub max_file_bytes: u64,
    pub max_output_bytes: usize,
}

pub fn grep_search(
    root: &ExistingPath,
    query: &str,
    options: &SearchOptions<'_>,
) -> AppResult<SearchResult> {
    if options.max_results == 0 {
        return Err(AppError::invalid_argument(
            "max_results",
            "must be at least 1",
        ));
    }
    if query.trim().is_empty() {
        return Ok(empty_result(root, query));
    }
    let escaped;
    let pattern = if options.regex {
        query
    } else {
        escaped = regex::escape(query);
        &escaped
    };
    let expression = Regex::new(pattern)
        .map_err(|error| AppError::invalid_argument("query", error.to_string()))?;
    let glob_matchers = options
        .include_globs
        .iter()
        .map(|pattern| {
            Glob::new(pattern)
                .map(|glob| glob.compile_matcher())
                .map_err(|error| AppError::invalid_argument("include_globs", error.to_string()))
        })
        .collect::<AppResult<Vec<_>>>()?;

    let mut groups = Vec::new();
    let mut total = 0usize;
    let mut output_bytes = 0usize;
    let mut output_truncated = false;
    for entry in WalkDir::new(root.path())
        .follow_links(false)
        .max_depth(8)
        .sort_by_file_name()
        .into_iter()
        .filter_entry(|entry| !skip_directory(entry.path(), root.path()))
    {
        if total >= options.max_results {
            break;
        }
        let Ok(entry) = entry else { continue };
        if !entry.file_type().is_file() || entry.file_type().is_symlink() {
            continue;
        }
        let Some(name) = entry.file_name().to_str() else {
            continue;
        };
        if !glob_matchers.is_empty() && !glob_matchers.iter().any(|glob| glob.is_match(name)) {
            continue;
        }
        let metadata = match entry.metadata() {
            Ok(metadata) if metadata.len() <= options.max_file_bytes => metadata,
            _ => continue,
        };
        if metadata.len() == 0 {
            continue;
        }
        let remaining_results = options.max_results - total;
        let remaining_bytes = options.max_output_bytes.saturating_sub(output_bytes);
        let searched = search_file(
            entry.path(),
            &expression,
            remaining_results,
            options.context_lines,
            remaining_bytes,
        );
        let hits = searched.hits;
        output_bytes = output_bytes.saturating_add(searched.output_bytes);
        output_truncated |= searched.truncated;
        if hits.is_empty() {
            if output_truncated {
                break;
            }
            continue;
        }
        total += hits.len();
        let relative = entry
            .path()
            .strip_prefix(root.path())
            .unwrap_or(entry.path())
            .display()
            .to_string();
        groups.push(FileMatches {
            file: relative,
            hits,
        });
        if output_truncated {
            break;
        }
    }
    Ok(SearchResult {
        root: root.path().display().to_string(),
        query: query.to_owned(),
        match_count: total,
        truncated: total >= options.max_results || output_truncated,
        matches: groups,
    })
}

fn empty_result(root: &ExistingPath, query: &str) -> SearchResult {
    SearchResult {
        root: root.path().display().to_string(),
        query: query.to_owned(),
        match_count: 0,
        truncated: false,
        matches: Vec::new(),
    }
}

struct SearchFileResult {
    hits: Vec<SearchHit>,
    output_bytes: usize,
    truncated: bool,
}

fn search_file(
    path: &Path,
    expression: &Regex,
    cap: usize,
    context: usize,
    output_budget: usize,
) -> SearchFileResult {
    let Ok(file) = File::open(path) else {
        return SearchFileResult {
            hits: Vec::new(),
            output_bytes: 0,
            truncated: false,
        };
    };
    let mut lines = Vec::new();
    for (index, line) in BufReader::new(file).split(b'\n').enumerate() {
        let Ok(bytes) = line else {
            return SearchFileResult {
                hits: Vec::new(),
                output_bytes: 0,
                truncated: false,
            };
        };
        let Ok(text) = String::from_utf8(bytes) else {
            return SearchFileResult {
                hits: Vec::new(),
                output_bytes: 0,
                truncated: false,
            };
        };
        lines.push(ContextLine {
            line: index + 1,
            text: text.trim_end_matches('\r').to_owned(),
        });
    }
    let mut hits = Vec::new();
    let mut before = VecDeque::with_capacity(context);
    let mut remaining_bytes = output_budget;
    let mut truncated = false;
    for (index, line) in lines.iter().enumerate() {
        if expression.is_match(&line.text) {
            let Some(text) = take_bounded_text(&line.text, &mut remaining_bytes, 64) else {
                truncated = true;
                break;
            };
            let context_before = before
                .iter()
                .filter_map(|line| take_context_line(line, &mut remaining_bytes))
                .collect();
            let context_after = lines
                .iter()
                .skip(index + 1)
                .take(context)
                .filter_map(|line| take_context_line(line, &mut remaining_bytes))
                .collect();
            hits.push(SearchHit {
                line: line.line,
                text,
                is_match: true,
                context_before,
                context_after,
            });
            if hits.len() >= cap {
                break;
            }
        }
        if context > 0 {
            if before.len() == context {
                before.pop_front();
            }
            before.push_back(line.clone());
        }
    }
    SearchFileResult {
        hits,
        output_bytes: output_budget.saturating_sub(remaining_bytes),
        truncated,
    }
}

fn take_context_line(line: &ContextLine, remaining: &mut usize) -> Option<ContextLine> {
    take_bounded_text(&line.text, remaining, 48).map(|text| ContextLine {
        line: line.line,
        text,
    })
}

fn take_bounded_text(value: &str, remaining: &mut usize, overhead: usize) -> Option<String> {
    if *remaining <= overhead {
        return None;
    }
    let budget = (*remaining - overhead).min(value.len());
    let mut end = budget;
    while end > 0 && !value.is_char_boundary(end) {
        end -= 1;
    }
    let text = value[..end].to_owned();
    *remaining = remaining.saturating_sub(overhead + text.len());
    Some(text)
}

fn skip_directory(path: &Path, root: &Path) -> bool {
    if path == root {
        return false;
    }
    path.file_name()
        .and_then(|name| name.to_str())
        .is_some_and(|name| name.starts_with('.') || name == "__pycache__")
}
