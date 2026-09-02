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
