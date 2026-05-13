from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
from collections.abc import Sequence
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import TimeoutError as FutureTimeout
from pathlib import Path
from typing import Any, BinaryIO


class LspCollectorError(RuntimeError):
    """Raised when an LSP server cannot return a usable references response."""


LANGUAGE_IDS_BY_SUFFIX = {
    ".cjs": "javascript",
    ".cts": "typescript",
    ".js": "javascript",
    ".jsx": "javascriptreact",
    ".mjs": "javascript",
    ".mts": "typescript",
    ".py": "python",
    ".ts": "typescript",
    ".tsx": "typescriptreact",
}


def split_server_command(command: str) -> list[str]:
    parts = shlex.split(command, posix=os.name != "nt")
    return [part.strip('"') for part in parts if part.strip('"')]


def detect_language_id(path: Path, override: str | None = None) -> str:
    if override:
        return override
    return LANGUAGE_IDS_BY_SUFFIX.get(path.suffix.lower(), "plaintext")


def path_to_file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def collect_lsp_references(
    server_command: Sequence[str],
    *,
    workspace_root: Path,
    file_path: Path,
    line: int,
    character: int = 0,
    target_qualified_name: str | None = None,
    provider: str | None = None,
    language_id: str | None = None,
    timeout_seconds: float = 10.0,
    include_declaration: bool = True,
) -> dict[str, Any]:
    if not server_command:
        raise LspCollectorError("server_command is required")
    if line < 1:
        raise LspCollectorError("line must be 1-based and greater than zero")
    if character < 0:
        raise LspCollectorError("character must be zero or greater")

    root = workspace_root.resolve()
    source = file_path if file_path.is_absolute() else root / file_path
    source = source.resolve()
    text = source.read_text(encoding="utf-8")
    source_uri = path_to_file_uri(source)
    root_uri = path_to_file_uri(root)

    process = subprocess.Popen(
        list(server_command),
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )
    client = LspStdioClient(process, timeout_seconds=timeout_seconds)
    try:
        client.request(
            "initialize",
            {
                "processId": os.getpid(),
                "rootUri": root_uri,
                "capabilities": {},
                "workspaceFolders": [{"uri": root_uri, "name": root.name or "workspace"}],
            },
        )
        client.notify("initialized", {})
        client.notify(
            "textDocument/didOpen",
            {
                "textDocument": {
                    "uri": source_uri,
                    "languageId": detect_language_id(source, language_id),
                    "version": 1,
                    "text": text,
                }
            },
        )
        result = client.request(
            "textDocument/references",
            {
                "textDocument": {"uri": source_uri},
                "position": {"line": line - 1, "character": character},
                "context": {"includeDeclaration": include_declaration},
            },
        )
        client.notify("textDocument/didClose", {"textDocument": {"uri": source_uri}})
        client.shutdown()
    finally:
        client.close()

    if result is None:
        locations: list[Any] = []
    elif isinstance(result, list):
        locations = result
    else:
        raise LspCollectorError("textDocument/references did not return a Location array")

    payload: dict[str, Any] = {
        "provider": provider or f"lsp:{Path(server_command[0]).name}",
        "workspace_root": root_uri,
        "target": {
            "uri": source_uri,
            "file_path": source.relative_to(root).as_posix()
            if source.is_relative_to(root)
            else source.as_posix(),
            "line": line,
            "character": character,
        },
        "locations": locations,
    }
    if target_qualified_name:
        payload["target_qualified_name"] = target_qualified_name
    return payload


class LspStdioClient:
    def __init__(self, process: subprocess.Popen[bytes], timeout_seconds: float) -> None:
        if process.stdin is None or process.stdout is None:
            raise LspCollectorError("LSP process did not expose stdin/stdout")
        self.process = process
        self.stdin = process.stdin
        self.stdout = process.stdout
        self.timeout_seconds = timeout_seconds
        self.next_id = 1
        self.executor = ThreadPoolExecutor(max_workers=1)
        self.closed = False

    def request(self, method: str, params: Any) -> Any:
        request_id = self.next_id
        self.next_id += 1
        write_lsp_message(
            self.stdin,
            {"jsonrpc": "2.0", "id": request_id, "method": method, "params": params},
        )
        deadline = time.monotonic() + self.timeout_seconds
        while True:
            remaining = max(0.01, deadline - time.monotonic())
            if remaining <= 0.01 and time.monotonic() >= deadline:
                raise LspCollectorError(f"Timed out waiting for LSP response to {method}")
            message = self.read_message(remaining)
            if message.get("id") != request_id:
                continue
            if "error" in message:
                raise LspCollectorError(f"LSP request {method} failed: {message['error']}")
            return message.get("result")

    def notify(self, method: str, params: Any) -> None:
        write_lsp_message(
            self.stdin,
            {"jsonrpc": "2.0", "method": method, "params": params},
        )

    def read_message(self, timeout_seconds: float) -> dict[str, Any]:
        future = self.executor.submit(read_lsp_message, self.stdout)
        try:
            return future.result(timeout=timeout_seconds)
        except FutureTimeout as exc:
            self.terminate_process()
            raise LspCollectorError("Timed out reading from LSP server") from exc
        except EOFError as exc:
            raise LspCollectorError("LSP server closed stdout before responding") from exc

    def shutdown(self) -> None:
        if self.process.poll() is not None:
            return
        try:
            self.request("shutdown", None)
            self.notify("exit", None)
        except LspCollectorError:
            self.terminate_process()

    def close(self) -> None:
        if self.closed:
            return
        self.closed = True
        self.terminate_process()
        self.executor.shutdown(wait=False, cancel_futures=True)

    def terminate_process(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=2)


def write_lsp_message(stream: BinaryIO, payload: dict[str, Any]) -> None:
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    stream.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    stream.flush()


def read_lsp_message(stream: BinaryIO) -> dict[str, Any]:
    header = bytearray()
    while b"\r\n\r\n" not in header:
        chunk = stream.read(1)
        if not chunk:
            raise EOFError
        header.extend(chunk)
    header_text = bytes(header).decode("ascii", errors="replace")
    content_length = None
    for line in header_text.split("\r\n"):
        if line.lower().startswith("content-length:"):
            content_length = int(line.split(":", 1)[1].strip())
            break
    if content_length is None:
        raise LspCollectorError("LSP message is missing Content-Length")
    body = stream.read(content_length)
    if len(body) != content_length:
        raise EOFError
    message = json.loads(body.decode("utf-8"))
    if not isinstance(message, dict):
        raise LspCollectorError("LSP message body is not a JSON object")
    return message
