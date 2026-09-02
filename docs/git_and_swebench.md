# Git & SWE-Bench Lifecycle Integration

## 1. The SWE-Bench Autonomous Workflow

In autonomous SWE benchmarks (such as [SWE-bench](https://www.swebench.com/)), an AI agent is placed inside a repository with a problem statement and must:
1. **Explore the codebase** to locate the defect.
2. **Modify the source code** surgically without introducing syntax regressions.
3. **Verify the modification** against unit tests.
4. **Export a clean unified patch** (`.patch`) for evaluation against hidden validation tests.
5. **Roll back failed attempts** without polluting the git history.

Standard MCP file servers force agents to fall back to bash terminals for all Git operations, resulting in unstructured command outputs, escaped paths, and fragile scripts. `mcp_fs` provides a first-class, structured Git surface.

---

## 2. Tools & Algorithmic Design

### 2.1 `git_status`: Machine-Readable Porcelain Parsing

`git_status` executes `git status --porcelain=v1 -b` inside the verified repository root and parses the structured output into typed dictionaries:

```python
# Output Format
{
  "repo_root": "/project",
  "branch": "main",
  "upstream": "origin/main",
  "ahead": 1,
  "behind": 0,
  "is_clean": False,
  "counts": {
    "staged": 1,
    "unstaged": 1,
    "untracked": 2
  },
  "staged": [{"path": "src/fix.py", "status": "M"}],
  "unstaged": [{"path": "tests/test_fix.py", "status": "M"}],
  "untracked": ["scratch.py"]
}
```

#### Parsing Rules:
* **Branch Header**: Decodes `## main...origin/main [ahead 1, behind 2]`, `## Initial commit on main`, or `## No commits yet on main`.
* **XY Status Codes**:
  * $X$ (index column): Categorized into `staged` (e.g. `M`, `A`, `D`, `R`).
  * $Y$ (worktree column): Categorized into `unstaged` (e.g. `M`, `D`).
  * `??`: Categorized into `untracked`.
  * Renames (`R  old -> new`): Parsed to extract target destination.

---

### 2.2 `git_diff`: Token-Conscious Unified Diffs

Git diffs on large commits can span tens of thousands of lines. If dumped directly into the model context, they exhaust token limits and degrade reasoning performance.

#### The Capping & Filtering Algorithm:
1. **Target Routing**: Supports `HEAD`, index (`cached=True`), or specific commit/branch targets.
2. **Path Scoping**: Restricts diff computation to explicit file lists via `git diff -- <paths>`.
3. **Line Capping**: If the diff exceeds `max_lines` (default 500 lines):
   * Content is sliced at line 500.
   * A structured truncation note is appended:
     ```
     [... Diff truncated: 1,420 total lines exceed 500 limit. Filter by specific paths or use read_file on changed files ...]
     ```
   * Flagged with `"truncated": true` in the JSON response.
4. **Summary Stats**: Gathers insertion and deletion metrics from `git diff --stat`.

---

### 2.3 `export_swebench_patch`: Benchmark Submission Pipeline

In SWE-bench evaluation harnesses, the benchmark runner executes:
```bash
git apply <model_patch_file>.patch
pytest -rA <eval_test_spec>
```

#### Key Invariants of `export_swebench_patch`:
1. **Full Working Tree Capture**: Generates `git diff HEAD`, capturing both staged and unstaged modifications relative to the base commit.
2. **Untracked File Auditing**: Git diffs against `HEAD` omit newly created files unless they are tracked (`git add -N`). `export_swebench_patch` audits `status --porcelain` and returns an explicit warning if untracked files exist:
   ```json
   {
     "is_empty": false,
     "files_changed": 1,
     "untracked_warning": "There are 1 untracked file(s) that are NOT included in the patch: new_feature.py. Use write_file or git add to track them if required.",
     "patch": "diff --git a/src/app.py b/src/app.py\n..."
   }
   ```
   This prevents the agent from submitting a patch that fails in the evaluation container because new files were omitted.

---

### 2.4 `revert_file`: Instant Autonomous Rollback

When an autonomous agent explores a hypothesis that fails (e.g. tests fail, syntax breaks), it needs to cleanly revert the file to its initial state without issuing destructive terminal commands.

#### Algorithm:
1. **Check Tracking State**: Queries `git ls-files <path>`.
2. **Tracked Files**: Executes `git checkout HEAD -- <rel_path>`, restoring the clean working directory and index state in one call.
3. **Untracked Files**: If the file is untracked (a temporary file created by the agent), it is **safely moved to the repository's `.trash/` buffer** rather than permanently deleted with `rm`, maintaining full auditability and preventing accidental data loss.
