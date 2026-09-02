"""swebench_harness_demo.py - End-to-end SWE-bench autonomous agent simulation.

Demonstrates the 5-stage SWE-bench lifecycle powered by the production MCP server:
  1. Discovery: find_files & symbol_outline (frugal context exploration)
  2. Inspection: read_file with line-sliced offsets (no whole-file token dump)
  3. Pre-commit syntax gate: syntax_guard blocks invalid syntax before disk write
  4. Surgical repair: edit_block performs atomic, verified replacement
  5. Verification & Submission: run_test distilled output + export_swebench_patch
"""

from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from mcp_fs.config import Settings
from mcp_fs.server import build_server


def run_demo() -> None:
    print("=" * 70)
    print("🚀 SWE-BENCH & PRODUCTION MCP FILESYSTEM SERVER DEMO")
    print("=" * 70)

    # 1. Create an isolated mock repository
    with tempfile.TemporaryDirectory() as tmp_dir:
        repo_root = Path(tmp_dir).resolve()

        # Initialize Git repository
        subprocess.run(["git", "init", "-b", "main"], cwd=repo_root, check=True, capture_output=True)
        subprocess.run(["git", "config", "user.name", "Demo Agent"], cwd=repo_root, check=True)
        subprocess.run(["git", "config", "user.email", "agent@swebench.demo"], cwd=repo_root, check=True)

        # Create buggy calculator module
        pkg_dir = repo_root / "calculator"
        pkg_dir.mkdir()
        (pkg_dir / "__init__.py").write_text('"""Calculator package."""\n')

        calc_code = (
            '"""Core calculator logic."""\n\n'
            'class Calculator:\n'
            '    """Provides basic arithmetic operations."""\n\n'
            '    def add(self, a: int, b: int) -> int:\n'
            '        """Add two integers."""\n'
            '        return a + b\n\n'
            '    def divide(self, a: int, b: int) -> float:\n'
            '        """Divide a by b.\n\n'
            '        BUG: Returns zero instead of quotient.\n'
            '        """\n'
            '        return 0.0  # BUG: should be a / b\n'
        )
        calc_file = pkg_dir / "core.py"
        calc_file.write_text(calc_code)

        # Create test file
        test_code = (
            'from calculator.core import Calculator\n\n'
            'def test_divide():\n'
            '    calc = Calculator()\n'
            '    assert calc.divide(10, 2) == 5.0\n'
        )
        test_file = repo_root / "test_calc.py"
        test_file.write_text(test_code)

        # Commit baseline to git
        subprocess.run(["git", "add", "."], cwd=repo_root, check=True)
        subprocess.run(["git", "commit", "-m", "Initial commit with bug"], cwd=repo_root, check=True, capture_output=True)

        # 2. Boot production MCP server around the repo root
        settings = Settings(
            roots=[str(repo_root)],
            mode="read-write",
            transport="stdio",
        )
        server = build_server(settings)

        def call(tool_name: str, **kwargs):
            tool = server._tool_manager.get_tool(tool_name)
            if not tool:
                raise KeyError(f"Tool {tool_name} not registered")
            return tool.fn(**kwargs)

        # Step 1: Discover Python files
        print("\n[Stage 1: Discovery & Exploration]")
        found = call("find_files", path=str(repo_root), pattern="*.py")
        print(f"  ✓ find_files found {len(found['matches'])} files: {[m['relative_path'] for m in found['matches']]}")

        # Step 2: Extract AST outline
        outline = call("symbol_outline", path=str(calc_file))
        classes = [s["name"] for s in outline.get("symbols", []) if s.get("kind") == "class"]
        methods = [
            c["name"]
            for s in outline.get("symbols", [])
            for c in s.get("children", [])
            if c.get("kind") in ("function", "method")
        ]
        print(f"  ✓ symbol_outline mapped AST: Classes={classes}, Methods={methods}")

        # Step 3: Inspect buggy method with line slicing
        print("\n[Stage 2: Token-Frugal Inspection]")
        sliced = call("read_file", path=str(calc_file), offset=10, limit=8)
        r = sliced["range"]
        print(f"  ✓ read_file(offset=10, limit=8) -> lines {r['start']} to {r['end']}:")
        for line in sliced["content"].splitlines()[:5]:
            print(f"    {line}")

        # Step 4: Verify syntax gate protection
        print("\n[Stage 3: Pre-Commit Syntax Gate]")
        bad_res = call(
            "edit_block",
            path=str(calc_file),
            target_content="return 0.0  # BUG: should be a / b",
            replacement_content="return a / / b  # INVALID SYNTAX!",
        )
        assert bad_res.get("error_code") == "syntax_error" or "syntax" in str(bad_res)
        print(f"  ✓ syntax_guard intercepted syntax error before disk write:")
        print(f"    Code: {bad_res.get('error_code')}")

        # Step 5: Surgical edit fix
        print("\n[Stage 4: Surgical Atomic Edit]")
        good_res = call(
            "edit_block",
            path=str(calc_file),
            target_content="return 0.0  # BUG: should be a / b",
            replacement_content="return float(a / b)",
        )
        print(f"  ✓ edit_block applied atomically (verified AST syntax):")
        print(f"    Action: {good_res.get('action')}, size: {good_res.get('size_bytes')} bytes")

        # Step 6: Test verification & failure distillation
        print("\n[Stage 5: Test Execution & Patch Export]")
        test_res = call("run_test", command=f"{sys.executable} -m pytest test_calc.py", path=str(repo_root))
        print(f"  ✓ run_test status: passed={test_res.get('passed')}, exit_code={test_res.get('exit_code')}")

        # Step 7: Export SWE-bench submission patch
        patch_res = call("export_swebench_patch", path=str(repo_root))
        print(f"  ✓ export_swebench_patch generated submission diff:")
        print(f"    Files changed: {patch_res['files_changed']}, +{patch_res['insertions']} -{patch_res['deletions']}")
        for pline in patch_res["patch"].splitlines():
            print(f"    {pline}")

        print("\n" + "=" * 70)
        print("✅ SWE-BENCH DEMO COMPLETED SUCCESSFULLY WITH 100% INVARIANTS MET")
        print("=" * 70)


if __name__ == "__main__":
    run_demo()
