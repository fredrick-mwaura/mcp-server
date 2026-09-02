"""Typed errors for the filesystem server.

LESSON: Lesson 1 returned `{"error": "..."}` strings from tools. That works,
but production servers benefit from *structured* errors: a stable `error_code`
the AI model (and any automated caller) can branch on, plus a human message.

The flow:
    vfs.py / security/  ->  raise a FileSystemError subclass
    tool handler        ->  catch FileSystemError, return .to_result()
    MCP client / the agent ->  reads {"error_code": "path_not_allowed", ...}

Each subclass exists because a different recovery action is possible:
    - path_not_allowed      -> the model should pick a path under an allowed root
    - path_not_found        -> the model should check the path first
    - path_not_a_directory  -> the model should pass a directory, not a file
"""

from __future__ import annotations


class FileSystemError(Exception):
    """Base class for all expected failures. Never raised directly."""

    code = "filesystem_error"  # stable, machine-readable identifier

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def to_result(self) -> dict[str, str]:
        """Convert to the dictionary shape returned by every tool handler."""
        return {"error_code": self.code, "error": self.message}


class PathNotAllowedError(FileSystemError):
    """The requested path resolves OUTSIDE every allowed root.

    This is the security error. It should be impossible to reach vfs.py with
    such a path - security/canonical_path.py raises this before any FS call.
    """

    code = "path_not_allowed"

    def __init__(self, requested: str, resolved: str, allowed_roots: list[str]) -> None:
        self.requested = requested
        self.resolved = resolved
        self.allowed_roots = allowed_roots
        super().__init__(
            f"Path '{requested}' (resolves to '{resolved}') is outside the "
            f"allowed root(s): {', '.join(allowed_roots)}"
        )


class PathNotFoundError(FileSystemError):
    """The path does not exist on disk (checked with os.path.lexists)."""

    code = "path_not_found"

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Path does not exist: {path}")


class PathNotADirectoryError(FileSystemError):
    """The path exists but is a file (or symlink to one), not a directory."""

    code = "path_not_a_directory"

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Path is not a directory: {path}")


class PathNotAFileError(FileSystemError):
    """The path exists but is a directory, not a file.

    Phase 2: tools like read_file need to refuse directory paths with a
    specific error the agent can understand and recover from.
    """

    code = "path_not_a_file"

    def __init__(self, path: str) -> None:
        self.path = path
        super().__init__(f"Path is a directory, not a file: {path}")


class FileTooLargeError(FileSystemError):
    """The file exceeds the configured byte cap for reading.

    Phase 2: prevents a 40 GB log file from flooding the model's context
    window and running up massive API costs. The agent gets the file size
    and the cap so it can request a specific line range instead.
    """

    code = "file_too_large"

    def __init__(self, path: str, size_bytes: int, max_bytes: int) -> None:
        self.path = path
        self.size_bytes = size_bytes
        self.max_bytes = max_bytes
        super().__init__(
            f"File '{path}' is {size_bytes:,} bytes, exceeding the "
            f"{max_bytes:,} byte limit. Use offset/limit to read a slice."
        )


class ReadOnlyModeError(FileSystemError):
    """A write operation was attempted while the server is in read-only mode.

    Phase 3: the mode setting (MCP_FS_MODE) is the master switch for write
    operations. This error tells the agent to stop trying to write — it is
    not a per-path issue but a server configuration constraint.
    """

    code = "read_only_mode"

    def __init__(self, tool: str) -> None:
        self.tool = tool
        super().__init__(
            f"Tool '{tool}' requires read-write mode, but the server is "
            f"configured as read-only (MCP_FS_MODE=read-only)."
        )


class EditTargetNotFoundError(FileSystemError):
    """The target content for an edit_block was not found or not unique.

    Phase 3: edit_block needs to replace a unique string in a file. If the
    target appears 0 times or >1 times, we refuse the edit with a clear
    explanation so the agent can refine its target string.
    """

    code = "edit_target_error"

    def __init__(self, path: str, occurrences: int) -> None:
        self.path = path
        self.occurrences = occurrences
        if occurrences == 0:
            msg = (
                f"Target content not found in '{path}'. "
                f"The text to replace does not exist in the file."
            )
        else:
            msg = (
                f"Target content found {occurrences} times in '{path}'. "
                f"edit_block requires a unique match (exactly 1 occurrence). "
                f"Provide a more specific target string."
            )
        super().__init__(msg)


class SyntaxValidationError(FileSystemError):
    """The proposed code change would introduce a syntax error.

    Phase 3: before committing any write/edit to a Python file, we parse
    the result with ast.parse(). If it fails, the disk stays untouched and
    this error tells the agent exactly which line has the problem — saving
    3-5 wasted retry turns.
    """

    code = "syntax_error"

    def __init__(self, path: str, line: int | None, detail: str) -> None:
        self.path = path
        self.error_line = line
        self.detail = detail
        loc = f" at line {line}" if line else ""
        super().__init__(
            f"Syntax error{loc} in '{path}': {detail}. "
            f"The file was NOT modified. Fix the syntax and retry."
        )
