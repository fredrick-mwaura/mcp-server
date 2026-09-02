# Security Invariants & Virtual File System (VFS)

## 1. Threat Model & Single-Gate Security (`canonical_path.py`)

Every tool in the server receives paths as raw strings from the AI agent. The AI model is treated as an untrusted client that may produce adversarial or hallucinated paths.

### 1.1 Defended Attack Vectors

| Attack Vector | Malicious Example | How We Defend |
|---|---|---|
| **Directory Traversal** | `../../../../etc/shadow` | Canonicalization resolves all `..` segments and strictly verifies root containment. |
| **Sibling Prefix Confusion** | `/dataevil/pwned.txt` vs allowed `/data` | Path containment uses `os.path.commonpath()` and equality checks, never string `.startswith()`. |
| **Symlink Escape** | `/workspace/link_to_root` $\to$ `/` | `os.path.realpath()` dereferences all symlinks before boundary validation. |
| **NUL Byte Smuggling** | `/allowed/file.txt\x00.exe` | Explicit check rejects `\x00` characters before any filesystem syscall. |
| **CWD Spoofing** | Relative `./foo` resolving unexpectedly | Paths resolve against verified server CWD, followed by canonical containment check. |

---

### 1.2 Canonical Path Resolution Algorithm

```
Raw Path String (from tool call)
       │
       ▼
1. Reject NUL bytes ('\x00') ──► Raise PathNotAllowedError
       │
       ▼
2. Expand '~' (os.path.expanduser)
       │
       ▼
3. Convert to absolute path (os.path.abspath)
       │
       ▼
4. Resolve all symlinks (os.path.realpath)
       │
       ▼
5. Normalize separators & redundant slashes (os.path.normpath)
       │
       ▼
6. Verify containment within at least one allowed root:
   _is_within(resolved_candidate, allowed_root)
       ├── True  ──► Return approved Path object
       └── False ──► Raise PathNotAllowedError (names allowed roots)
```

#### The `_is_within` Algorithm
```python
def _is_within(candidate: Path, root: Path) -> bool:
    try:
        common = os.path.commonpath([str(candidate), str(root)])
        return os.path.realpath(common) == str(root)
    except ValueError:
        # Occurs on Windows when paths are on different drive letters (e.g. C: vs D:)
        return False
```
Using `commonpath()` guarantees that `/dataevil` is recognized as a sibling, not a child of `/data`.

---

## 2. Crash-Safe Atomic Writes (`write_file_atomic`)

A major hazard in software engineering agents is **mid-stream crash corruption**. If an agent writes to a file with `open(path, "w")` and the process is interrupted, the original file is truncated to 0 bytes or left in a corrupt state.

### The Atomic Swap Algorithm

```python
def write_file_atomic(canonical_path: Path, content: str) -> dict[str, object]:
    parent = canonical_path.parent
    tmp_name = f".{canonical_path.name}.mcpfs-tmp-{uuid.uuid4().hex[:8]}"
    tmp_path = parent / tmp_name

    try:
        with open(tmp_path, "w", encoding="utf-8", newline="") as fh:
            fh.write(content)
            fh.flush()
            os.fsync(fh.fileno())  # 1. Force hardware write to physical disk

        # 2. Atomic filesystem rename
        os.replace(tmp_path, canonical_path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
```

### Key Invariants:
1. **Same-Volume Placement**: The temp file is written to the target file's sibling directory. This ensures the temporary file resides on the exact same filesystem volume, guaranteeing that `os.replace` executes as an atomic inode pointer update rather than a cross-device copy.
2. **`os.fsync()` Before Rename**: Flushes kernel buffers to physical non-volatile storage *before* the directory entry is swapped.
3. **Zero Corruption**: If execution fails at any point prior to `os.replace`, the original target file remains 100% intact.

---

## 3. Scoped Trash Buffer (`.trash/`) vs. `send2trash`

`mcp_fs` deliberately uses a scoped `<root>/.trash/` buffer instead of the third-party `send2trash` library.

### Architectural Decision Comparison

```
┌──────────────────────────────────────┬──────────────────────────────────────┐
│        Standard send2trash           │      Our Scoped <root>/.trash/       │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ Moves files to host OS recycle bin:  │ Moves files to isolated buffer:      │
│ - macOS: ~/.Trash                    │ - <root>/.trash/<name>.<uuid>        │
│ - Linux: ~/.local/share/Trash        │                                      │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ ❌ Breaches Security Boundary:       │ ✅ Preserves Security Boundary:      │
│ Moving files to ~/.Trash violates    │ Files remain strictly inside the     │
│ root containment invariants.         │ allowed root boundary.               │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ ❌ Host Privacy Leak:                │ ✅ Quarantined to Workspace:         │
│ If an agent requests trash recovery, │ The agent can only see items deleted │
│ opening ~/.Trash exposes the user's  │ within this specific workspace.      │
│ entire personal deleted file history.│                                      │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ ❌ Fails in Headless / Docker CI:    │ ✅ 100% Portable Everywhere:         │
│ CI runners and containers lack       │ Works universally across Docker, CI, │
│ desktop FreeDesktop trash daemons.   │ Linux, macOS, and Windows.           │
├──────────────────────────────────────┼──────────────────────────────────────┤
│ ❌ External Dependency:              │ ✅ Zero External Dependencies:       │
│ Requires extra third-party package.  │ Pure standard library (pathlib/shutil│
└──────────────────────────────────────┴──────────────────────────────────────┘
```

### Collision Defense
When files of the same name are deleted multiple times, `.trash/` appends an 8-character UUID suffix:
```
<root>/.trash/broken_module.py.3f9a12bc
<root>/.trash/broken_module.py.e8d4a991
```
This guarantees that prior deletions are never overwritten in the trash buffer.
