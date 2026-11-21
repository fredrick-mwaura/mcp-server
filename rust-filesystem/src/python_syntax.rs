use ruff_python_ast::{Expr, Stmt, StmtClassDef, StmtFunctionDef};
use ruff_python_parser::parse_module;
use ruff_text_size::Ranged;
use serde::Serialize;

use crate::error::{AppError, AppResult};

#[derive(Debug, Serialize)]
pub struct SymbolOutline {
    pub path: String,
    pub language: String,
    pub supported: bool,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub symbol_count: Option<usize>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub parse_error: Option<String>,
    #[serde(skip_serializing_if = "Option::is_none")]
    pub note: Option<String>,
    pub symbols: Vec<Symbol>,
}

#[derive(Debug, Serialize)]
#[serde(untagged)]
pub enum Symbol {
    Function(FunctionSymbol),
    Class(ClassSymbol),
}

#[derive(Debug, Serialize)]
pub struct FunctionSymbol {
    pub kind: &'static str,
    pub name: String,
    pub line_start: usize,
    pub line_end: usize,
    pub args: Vec<String>,
    pub docstring: Option<String>,
    #[serde(skip_serializing_if = "Vec::is_empty")]
    pub decorators: Vec<String>,
}

#[derive(Debug, Serialize)]
pub struct ClassSymbol {
    pub kind: &'static str,
    pub name: String,
    pub line_start: usize,
    pub line_end: usize,
    pub bases: Vec<String>,
    pub docstring: Option<String>,
    pub methods: Vec<FunctionSymbol>,
}

pub fn outline(source: &str, path: &str) -> SymbolOutline {
    if !path.to_ascii_lowercase().ends_with(".py") {
        let language = std::path::Path::new(path)
            .extension()
            .and_then(|value| value.to_str())
            .unwrap_or("unknown")
            .to_owned();
        return SymbolOutline {
            path: path.to_owned(),
            language,
            supported: false,
            symbol_count: None,
            parse_error: None,
            note: Some("symbol_outline currently supports Python (.py) files only.".to_owned()),
            symbols: Vec::new(),
        };
    }

    let module = match parse_module(source) {
        Ok(module) => module,
        Err(error) => {
            return SymbolOutline {
                path: path.to_owned(),
                language: "python".to_owned(),
                supported: true,
                symbol_count: None,
                parse_error: Some(format!("SyntaxError: {error}")),
                note: None,
                symbols: Vec::new(),
            };
        }
    };

    let symbols = module
        .suite()
        .iter()
        .filter_map(|statement| match statement {
            Stmt::FunctionDef(function) => {
                Some(Symbol::Function(function_symbol(function, source)))
            }
            Stmt::ClassDef(class) => Some(Symbol::Class(class_symbol(class, source))),
            _ => None,
        })
        .collect::<Vec<_>>();
    SymbolOutline {
        path: path.to_owned(),
        language: "python".to_owned(),
        supported: true,
        symbol_count: Some(symbols.len()),
        parse_error: None,
        note: None,
        symbols,
    }
}

pub fn validate(source: &str, path: &str) -> AppResult<()> {
    if !path.to_ascii_lowercase().ends_with(".py") && !path.to_ascii_lowercase().ends_with(".pyw") {
        return Ok(());
    }
    parse_module(source).map_err(|error| AppError::Syntax {
        path: path.to_owned(),
        detail: error.to_string(),
    })?;
    Ok(())
}

fn class_symbol(class: &StmtClassDef, source: &str) -> ClassSymbol {
    let methods = class
        .body
        .iter()
        .filter_map(|statement| match statement {
            Stmt::FunctionDef(function) => Some(function_symbol(function, source)),
            _ => None,
        })
        .collect();
    let bases = class.arguments.as_ref().map_or_else(Vec::new, |arguments| {
        arguments
            .args
            .iter()
            .map(|expression| source_slice(source, expression).to_owned())
            .collect()
    });
    ClassSymbol {
        kind: "class",
        name: class.name.to_string(),
        line_start: line_at(source, class.start().to_usize()),
        line_end: line_at_end(source, class.end().to_usize()),
        bases,
        docstring: docstring(&class.body),
        methods,
    }
}

fn function_symbol(function: &StmtFunctionDef, source: &str) -> FunctionSymbol {
    let args = function
        .parameters
        .iter()
        .filter_map(|parameter| {
            let name = parameter.name().as_str();
            (!matches!(name, "self" | "cls")).then(|| name.to_owned())
        })
        .collect();
    let decorators = function
        .decorator_list
        .iter()
        .map(|decorator| source_slice(source, &decorator.expression).to_owned())
        .collect();
    FunctionSymbol {
        kind: if function.is_async {
            "async_function"
        } else {
            "function"
        },
        name: function.name.to_string(),
        line_start: line_at(source, function.start().to_usize()),
        line_end: line_at_end(source, function.end().to_usize()),
        args,
        docstring: docstring(&function.body),
        decorators,
    }
}

fn docstring(body: &[Stmt]) -> Option<String> {
    let Stmt::Expr(expression_statement) = body.first()? else {
        return None;
    };
    let Expr::StringLiteral(literal) = &*expression_statement.value else {
        return None;
    };
    Some(literal.value.to_str().trim().to_owned())
}

fn source_slice<'a>(source: &'a str, value: &impl Ranged) -> &'a str {
    let range = value.range();
    source
        .get(range.start().to_usize()..range.end().to_usize())
        .unwrap_or("?")
        .trim()
}

fn line_at(source: &str, byte_offset: usize) -> usize {
    source
        .as_bytes()
        .iter()
        .take(byte_offset.min(source.len()))
        .filter(|byte| **byte == b'\n')
        .count()
        + 1
}

fn line_at_end(source: &str, byte_offset: usize) -> usize {
    let adjusted = byte_offset.saturating_sub(1);
    line_at(source, adjusted)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn extracts_class_and_function() {
        let source = r#"class Greeter(Base):
    """Greets."""
    def greet(self, name):
        return name

async def fetch(url):
    return url
"#;
        let result = outline(source, "sample.py");
        assert_eq!(result.symbol_count, Some(2));
        let Symbol::Class(class) = &result.symbols[0] else {
            panic!("expected class");
        };
        assert_eq!(class.name, "Greeter");
        assert_eq!(class.bases, ["Base"]);
        assert_eq!(class.methods[0].args, ["name"]);
        let Symbol::Function(function) = &result.symbols[1] else {
            panic!("expected function");
        };
        assert_eq!(function.kind, "async_function");
    }

    #[test]
    fn reports_parse_error() {
        let result = outline("def broken(:\n", "broken.py");
        assert!(result.parse_error.is_some());
        assert!(result.symbols.is_empty());
    }
}
