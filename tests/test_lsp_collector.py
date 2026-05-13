from __future__ import annotations

import sys
from pathlib import Path

import pytest

from geond.code_graph.lsp_collector import (
    LspCollectorError,
    collect_lsp_references,
    detect_lsp_server_profile,
    list_lsp_server_profiles,
    resolve_lsp_server_command,
    split_server_command,
)
from geond.code_graph.lsp_references import normalize_lsp_references

FAKE_LSP_SERVER = r"""
import json
import sys


def read_message():
    header = bytearray()
    while b"\r\n\r\n" not in header:
        chunk = sys.stdin.buffer.read(1)
        if not chunk:
            return None
        header.extend(chunk)
    length = None
    for line in bytes(header).decode("ascii").split("\r\n"):
        if line.lower().startswith("content-length:"):
            length = int(line.split(":", 1)[1].strip())
    if length is None:
        return None
    return json.loads(sys.stdin.buffer.read(length).decode("utf-8"))


def write_message(payload):
    body = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    sys.stdout.buffer.write(f"Content-Length: {len(body)}\r\n\r\n".encode("ascii") + body)
    sys.stdout.buffer.flush()


while True:
    message = read_message()
    if message is None:
        break
    method = message.get("method")
    if method == "initialize":
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": {"capabilities": {"referencesProvider": True}},
            }
        )
    elif method == "textDocument/references":
        uri = message["params"]["textDocument"]["uri"]
        write_message(
            {
                "jsonrpc": "2.0",
                "id": message["id"],
                "result": [
                    {
                        "uri": uri,
                        "range": {
                            "start": {"line": 3, "character": 11},
                            "end": {"line": 3, "character": 23},
                        },
                    }
                ],
            }
        )
    elif method == "shutdown":
        write_message({"jsonrpc": "2.0", "id": message["id"], "result": None})
    elif method == "exit":
        break
"""


def test_collect_lsp_references_from_stdio_server(tmp_path: Path) -> None:
    source = tmp_path / "service.py"
    source.write_text(
        """
def build_answer(prompt):
    return prompt.strip()

def use_answer(prompt):
    return build_answer(prompt)
""".strip()
        + "\n",
        encoding="utf-8",
    )
    server = tmp_path / "fake_lsp.py"
    server.write_text(FAKE_LSP_SERVER, encoding="utf-8")

    payload = collect_lsp_references(
        [sys.executable, str(server)],
        workspace_root=tmp_path,
        file_path=source,
        line=4,
        character=11,
        target_qualified_name="service.build_answer",
        provider="fake-lsp",
        server_profile="pyright",
        timeout_seconds=5,
    )

    assert payload["provider"] == "fake-lsp"
    assert payload["server_profile"] == "pyright"
    assert payload["target_qualified_name"] == "service.build_answer"
    assert payload["target"]["file_path"] == "service.py"
    assert len(payload["locations"]) == 1

    references = normalize_lsp_references(payload)
    assert references == [
        {
            "target_qualified_name": "service.build_answer",
            "provider": "fake-lsp",
            "reference": {
                "file_path": "service.py",
                "start_line": 4,
                "start_character": 11,
                "end_line": 4,
                "end_character": 23,
            },
            "metadata": {
                "lsp": {
                    "uri": source.resolve().as_uri(),
                    "range": {
                        "start": {"line": 3, "character": 11},
                        "end": {"line": 3, "character": 23},
                    },
                }
            },
        }
    ]


def test_split_server_command_strips_windows_style_quotes() -> None:
    command = f'"{sys.executable}" "some server.py" --stdio'

    assert split_server_command(command) == [sys.executable, "some server.py", "--stdio"]


def test_lsp_server_profile_resolution() -> None:
    assert detect_lsp_server_profile(Path("service.py")) == "pyright"
    assert detect_lsp_server_profile(Path("client.tsx")) == "typescript"

    command, profile = resolve_lsp_server_command(None, "auto", Path("service.py"))
    assert command == ["pyright-langserver", "--stdio"]
    assert profile == "pyright"

    command, profile = resolve_lsp_server_command(None, "typescript", Path("service.py"))
    assert command == ["typescript-language-server", "--stdio"]
    assert profile == "typescript"

    command, profile = resolve_lsp_server_command("custom-lsp --stdio", "auto", Path("x.txt"))
    assert command == ["custom-lsp", "--stdio"]
    assert profile is None

    with pytest.raises(LspCollectorError, match="Could not infer"):
        resolve_lsp_server_command(None, "auto", Path("README.md"))


def test_list_lsp_server_profiles_includes_auto() -> None:
    profiles = {item["name"]: item for item in list_lsp_server_profiles()}

    assert {"auto", "pyright", "typescript"}.issubset(profiles)
    assert profiles["pyright"]["command"] == ["pyright-langserver", "--stdio"]
