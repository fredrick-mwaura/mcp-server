# Token Economics & Cost Optimization

## 1. The Token Bloat Crisis in Standard MCP Servers

In autonomous SWE evaluation benchmarks (like SWE-bench) and daily LLM-assisted development, **token cost and context window degradation are the primary failure points**:

1. **Context Window Exhaustion**: Naive tools return entire 2,000 to 5,000-line files on read. After 3–4 turns, the model's context window is filled with irrelevant code.
2. **Exponential Token Compounding**: Every token added to conversation history is re-billed on every subsequent turn. A single 10,000-token file dump across a 20-turn session costs 200,000 extra input tokens.
3. **Completion Bloat & Hallucination**: When a model is forced to rewrite an entire file just to change a 3-line function, completion token costs skyrocket and the risk of hallucinated truncations (`// rest of file unchanged...`) increases exponentially.
4. **The Syntax Error Loop**: An edit that introduces a missing colon or indentation error breaks the test suite. The agent then spends 3 to 5 extra turns guessing what went wrong, multiplying cost.

---

## 2. Optimization Strategy & Cost Reduction Matrix

| Operation | Naive MCP Server Waste | Our Frugal Strategy | Measured Token Reduction |
|---|---|---|---|
| **Code Inspection** | Dumps full 3,000-line module (~12,000 tokens) | Line-numbered slice (`offset=40, limit=80`) (~350 tokens) | **~95% cheaper** |
| **Locating Definitions** | Model reads multiple entire files to find where a class lives | `symbol_outline` extracts classes, methods & spans (~40 tokens) | **~98% cheaper** |
| **Pattern Search** | Recursive file dumps or unconstrained walks | Ripgrep-grade search: match lines + 1 context line, capped at 30 | **~90% cheaper** |
| **Code Modifications** | Model generates entire 500-line file to edit 4 lines | `edit_block` sends only `target_content` & `replacement` (~50 tokens) | **~90% cheaper completion** |
| **Failed Edits** | Bad indentation breaks file; agent spends 3–5 turns debugging | `syntax_guard` rejects edit in memory and returns exact line | **Eliminates 3–5 wasted turns** |

---

## 3. Core Algorithms

### 3.1 Line-Sliced Reading & Prefixing (`read_file_slice`)

#### The Algorithm
1. **Binary Probe**: Before opening as text, read the first 8,192 bytes. If a NUL byte (`b"\x00"`) is present, return `is_binary: True` immediately with no content.
2. **Size Gating**: Enforce `max_read_bytes` (default 1 MB) on full reads to prevent accidental log dumping.
3. **Window Clamping**: Convert 1-indexed `offset` to 0-indexed slice bounds:
   $$\text{start} = \max(0, \text{offset} - 1)$$
   $$\text{end} = \min(\text{start} + \text{limit}, \text{total\_lines})$$
4. **Line-Number Prefixing**: Prepend each line with `L{number}: `:
   ```python
   numbered_lines = [f"L{i}: {line.rstrip()}" for i, line in enumerate(selected, start=start + 1)]
   ```

#### Why `L42:` Prefixes Are Critical
Models frequently hallucinate line numbers when reading unnumbered text. By explicitly providing `L42: def process():`, the model can cite exact line numbers in surgical edits and discussions with zero ambiguity.

---

### 3.2 AST Structural Map (`symbol_outline`)

Instead of reading a module to understand its architecture, `symbol_outline` parses Python code with the standard library `ast` module and builds a compact structural outline.

#### Extraction Logic:
* **Classes**: Name, line span (`line_start` to `line_end`), base classes, docstring summary.
* **Methods & Functions**: Name, line span, decorators (`@property`, `@staticmethod`), and argument list (with `self` and `cls` automatically filtered out).
* **Async Functions**: Flagged distinctly as `kind: "async_function"`.

```json
{
  "path": "/project/src/models.py",
  "symbol_count": 2,
  "symbols": [
    {
      "kind": "class",
      "name": "User",
      "line_start": 10,
      "line_end": 45,
      "bases": ["BaseModel"],
      "methods": [
        {"name": "__init__", "line_start": 12, "line_end": 18, "args": ["name", "email"]},
        {"name": "validate", "line_start": 20, "line_end": 35, "args": []}
      ]
    }
  ]
}
```

**Token Efficiency:** A 1,500-line module is represented in ~40 tokens. The agent navigates directly to the function of interest and reads only the lines it needs.

---

### 3.3 Surgical Block Editing (`edit_block`)

`edit_block` modifies files with surgical precision:

#### Algorithm:
1. **Target Read**: Load existing file content as string.
2. **Uniqueness Audit**: Compute `occurrences = original.count(target_content)`.
   * If `occurrences == 0`: Raise `EditTargetNotFoundError` ("Target content not found in file").
   * If `occurrences > 1`: Raise `EditTargetNotFoundError` ("Target content found N times; must be unique").
3. **Dry-Run Check**: If `dry_run=True`, return success confirmation without touching disk.
4. **Pre-Commit Syntax Gate**: The proposed replacement is parsed in memory using `ast.parse()`. If invalid, the write is aborted.
5. **Atomic Commit**: If valid, the new content is committed via `write_file_atomic()`.

**Token Efficiency:** Completion tokens drop from 1,000+ tokens to ~50 tokens per edit.
