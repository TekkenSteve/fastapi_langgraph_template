"""Monty-backed sandbox backend: Python-only script execution, in-process.

Monty (pydantic's Rust Python interpreter) is the lightweight tier of the
execution bridge: microsecond-class startup, zero infrastructure, and a
deny-by-default capability model (no network, no filesystem unless the host
grants it) — the right default for untrusted user-tier skill scripts.

Shape decisions:
- Implements ``SandboxBackendProtocol`` directly (NOT ``BaseSandbox`` —
  BaseSandbox builds file ops on shell commands, and Monty has no shell).
- The file table lives in THIS process (a plain dict); Monty only executes.
  On ``execute``, the virtual FS is materialized into a temp dir and mounted
  read-only, so scripts can read sibling data files. Multi-file Python
  imports (``import helper``) are not supported yet.
- ``execute()`` is Python-only: ``python <path.py>`` runs a file from the
  virtual FS, ``python -c <code>`` runs inline code. Anything else is an
  error — CLI-style scripts belong to the daytona/e2b tier.
- Every run gets ResourceLimits (time + memory) from construction.

Limits of the tier (documented in docs/design/hub.md): Python subset (no full
stdlib), no network/FS unless granted, subprocess-level isolation (weaker
than a VM against a Rust-level escape — evaluate for production).
"""

import asyncio
import re
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING, Any

from deepagents.backends.protocol import (
    DeleteResult,
    EditResult,
    ExecuteResponse,
    FileInfo,
    GlobResult,
    GrepMatch,
    GrepResult,
    LsResult,
    ReadResult,
    SandboxBackendProtocol,
    WriteResult,
)

if TYPE_CHECKING:
    from pydantic_monty import ResourceLimits

_MONTY_ERROR_HINT = "SANDBOX_PROVIDER=monty needs `uv sync --extra sandbox`"


class MontySandboxBackend(SandboxBackendProtocol):
    """Python-only sandbox: virtual FS in-process, execution via Monty."""

    def __init__(self, *, max_duration_secs: float = 30.0, max_memory: int = 128 * 1024 * 1024) -> None:
        self._files: dict[str, str] = {}
        self._max_duration_secs = max_duration_secs
        self._max_memory = max_memory

    @property
    def id(self) -> str:
        return f"monty-{id(self):x}"

    # --- virtual FS (plain dict; paths are virtual POSIX) --------------------

    def write(self, file_path: str, content: str) -> WriteResult:
        self._files[file_path] = content
        return WriteResult(path=file_path)

    def read(self, file_path: str, offset: int = 0, limit: int | None = None) -> ReadResult:
        content = self._files.get(file_path)
        if content is None:
            return ReadResult(error=f"File '{file_path}' not found")
        lines = content.splitlines()
        sliced = lines[offset : (offset + limit) if limit is not None else None]
        return ReadResult(
            file_data={"content": "\n".join(sliced), "encoding": "utf-8"},
            total_lines=len(lines),
            start_line=offset + 1 if sliced else None,
            end_line=offset + len(sliced) if sliced else None,
        )

    def ls(self, path: str) -> LsResult:
        prefix = path.rstrip("/") + "/"
        entries: list[FileInfo] = [
            {"path": p} for p in sorted(self._files) if p.startswith(prefix) and "/" not in p[len(prefix) :]
        ]
        return LsResult(entries=entries)

    def edit(self, file_path: str, old_string: str, new_string: str, replace_all: bool = False) -> EditResult:  # noqa: FBT001, FBT002
        content = self._files.get(file_path)
        if content is None:
            return EditResult(error=f"Error: File '{file_path}' not found")
        count = content.count(old_string)
        if count == 0:
            return EditResult(error=f"Error: String not found in file '{file_path}'")
        self._files[file_path] = (
            content.replace(old_string, new_string) if replace_all else content.replace(old_string, new_string, 1)
        )
        return EditResult(path=file_path, occurrences=count if replace_all else 1)

    def delete(self, file_path: str) -> DeleteResult:
        self._files.pop(file_path, None)
        return DeleteResult(path=file_path)

    def grep(self, pattern: str, path: str | None = None, glob: str | None = None, **kwargs: Any) -> GrepResult:
        regex = re.compile(pattern)
        matches: list[GrepMatch] = [
            {"path": p, "line": i + 1, "text": line}
            for p, content in self._files.items()
            if not path or p.startswith(path)
            for i, line in enumerate(content.splitlines())
            if regex.search(line)
        ]
        return GrepResult(matches=matches)

    def glob(self, pattern: str, path: str | None = None) -> GlobResult:
        import fnmatch

        files: list[FileInfo] = [
            {"path": p} for p in sorted(self._files) if fnmatch.fnmatch(p, pattern) and (not path or p.startswith(path))
        ]
        return GlobResult(matches=files)

    # --- execution (Python only) ----------------------------------------------

    def execute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Run Python from the virtual FS: ``python <path.py>`` or ``python -c <code>``."""
        try:
            code = self._resolve_command(command)
        except ValueError as e:
            return ExecuteResponse(output=f"monty: {e}", exit_code=2)

        from pydantic_monty import CollectString, Monty, MountDir

        limits: ResourceLimits = {
            "max_duration_secs": float(timeout) if timeout is not None else self._max_duration_secs,
            "max_memory": self._max_memory,
        }
        stdout = CollectString()
        with tempfile.TemporaryDirectory(prefix="monty-sandbox-") as tmp:
            self._materialize(Path(tmp))
            try:
                with Monty() as pool, pool.checkout(limits=limits) as session:
                    session.feed_run(
                        code,
                        print_callback=stdout,
                        mount=MountDir(host_path=tmp, virtual_path="/", mode="read-only"),
                    )
                return ExecuteResponse(output=stdout.output, exit_code=0)
            except Exception as e:
                output = f"{stdout.output}\n{type(e).__name__}: {e}".strip()
                return ExecuteResponse(output=output, exit_code=1)

    def _materialize(self, root: Path) -> None:
        """Write the virtual FS to a temp dir for the read-only mount."""
        for path, content in self._files.items():
            target = root / path.lstrip("/")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content)

    def _resolve_command(self, command: str) -> str:
        """Map the shell-shaped command to Python source from the virtual FS."""
        if command.startswith("python -c "):
            return command[len("python -c ") :].strip("'\"")
        match = re.fullmatch(r"python(?:3)?\s+(\S+)(?:\s+.*)?", command.strip())
        if match:
            path = match.group(1)
            content = self._files.get(path)
            if content is None and not path.startswith("/"):
                content = self._files.get(f"/{path.removeprefix('./')}")
            if content is None:
                raise ValueError(f"script not found: {path}")
            return content
        raise ValueError("monty sandbox runs Python only: use `python <script.py>` or `python -c <code>`")

    async def aexecute(self, command: str, *, timeout: int | None = None) -> ExecuteResponse:
        """Async variant: the worker is a subprocess; feed_run blocks the
        calling thread (GIL released), so hand off to a thread."""
        return await asyncio.to_thread(self.execute, command, timeout=timeout)


def make_monty_backend(**kwargs: Any) -> MontySandboxBackend:
    """Construct the backend, with a clear error when the extra is missing."""
    try:
        import pydantic_monty  # noqa: F401
    except ImportError as e:
        raise RuntimeError(_MONTY_ERROR_HINT) from e
    return MontySandboxBackend(**kwargs)
