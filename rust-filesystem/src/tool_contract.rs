use serde_json::{Map, Value, json};

/// Return the frozen Python-compatible input schema for a registered tool.
///
/// # Panics
///
/// Panics when called with a tool name that is not part of the 14-tool
/// compatibility contract. Registration uses only compile-time literals.
pub fn input_schema(tool: &str) -> Map<String, Value> {
    let (properties, required) = match tool {
        "list_directory" | "git_status" | "export_swebench_patch" => (
            json!({"path": string("Path", Some(json!(".")))}),
            Vec::new(),
        ),
        "read_file" => (
            json!({
                "path": string("Path", None),
                "offset": integer("Offset", Some(1)),
                "limit": integer("Limit", Some(200)),
            }),
            vec!["path"],
        ),
        "find_files" => (
            json!({
                "pattern": string("Pattern", Some(json!("*"))),
                "path": string("Path", Some(json!("."))),
                "max_results": integer("Max Results", Some(100)),
            }),
            Vec::new(),
        ),
        "grep_search" => (
            json!({
                "query": string("Query", None),
                "path": string("Path", Some(json!("."))),
                "regex": boolean("Regex", Some(false)),
                "include_globs": optional_string_array("Include Globs"),
                "max_results": integer("Max Results", Some(30)),
                "context_lines": integer("Context Lines", Some(1)),
            }),
            vec!["query"],
        ),
        "symbol_outline" | "delete_entry" | "revert_file" => {
            (json!({"path": string("Path", None)}), vec!["path"])
        }
        "edit_block" => (
            json!({
                "path": string("Path", None),
                "target_content": string("Target Content", None),
                "replacement_content": string("Replacement Content", None),
                "dry_run": boolean("Dry Run", Some(false)),
            }),
            vec!["path", "target_content", "replacement_content"],
        ),
        "write_file" => (
            json!({
                "path": string("Path", None),
                "content": string("Content", None),
            }),
            vec!["path", "content"],
        ),
        "apply_patch" => (
            json!({
                "patch": string("Patch", None),
                "path": string("Path", Some(json!("."))),
            }),
            vec!["patch"],
        ),
        "git_diff" => (
            json!({
                "path": string("Path", Some(json!("."))),
                "cached": boolean("Cached", Some(false)),
                "target": optional_string("Target"),
                "paths": optional_string_array("Paths"),
                "max_lines": integer("Max Lines", Some(500)),
            }),
            Vec::new(),
        ),
        "run_test" => (
            json!({
                "command": string("Command", None),
                "path": string("Path", Some(json!("."))),
                "timeout": number("Timeout", Some(90.0)),
                "distill": boolean("Distill", Some(true)),
            }),
            vec!["command"],
        ),
        _ => panic!("unknown tool contract: {tool}"),
    };
    let mut schema = Map::new();
    schema.insert("properties".to_owned(), properties);
    if !required.is_empty() {
        schema.insert("required".to_owned(), json!(required));
    }
    schema.insert(
        "title".to_owned(),
        Value::String(format!("{tool}Arguments")),
    );
    schema.insert("type".to_owned(), Value::String("object".to_owned()));
    schema
}

fn string(title: &str, default: Option<Value>) -> Value {
    property("string", title, default)
}

fn integer(title: &str, default: Option<i64>) -> Value {
    property("integer", title, default.map(Value::from))
}

fn number(title: &str, default: Option<f64>) -> Value {
    property("number", title, default.map(Value::from))
}

fn boolean(title: &str, default: Option<bool>) -> Value {
    property("boolean", title, default.map(Value::from))
}

fn property(kind: &str, title: &str, default: Option<Value>) -> Value {
    let mut property = Map::new();
    if let Some(default) = default {
        property.insert("default".to_owned(), default);
    }
    property.insert("title".to_owned(), Value::String(title.to_owned()));
    property.insert("type".to_owned(), Value::String(kind.to_owned()));
    Value::Object(property)
}

fn optional_string(title: &str) -> Value {
    json!({
        "anyOf": [{"type": "string"}, {"type": "null"}],
        "default": null,
        "title": title,
    })
}

fn optional_string_array(title: &str) -> Value {
    json!({
        "anyOf": [{"items": {"type": "string"}, "type": "array"}, {"type": "null"}],
        "default": null,
        "title": title,
    })
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn schemas_match_legacy_shape_without_generator_metadata() {
        let schema = input_schema("read_file");
        assert_eq!(schema["title"], "read_fileArguments");
        assert_eq!(schema["required"], json!(["path"]));
        assert!(!schema.contains_key("$schema"));
        assert_eq!(schema["properties"]["offset"]["default"], 1);
    }
}
