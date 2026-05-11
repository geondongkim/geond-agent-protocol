from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from geond.adapters.vscode_copilot import CHAT_INDEX_KEY, parse_storage, read_chat_index

FIXTURE_ROOT = Path(__file__).parent / "fixtures" / "vscode_copilot"


def create_state_db(storage_path: Path) -> None:
    db_path = storage_path / "state.vscdb"
    conn = sqlite3.connect(db_path)
    try:
        conn.execute("CREATE TABLE ItemTable (key TEXT PRIMARY KEY, value TEXT)")
        conn.execute(
            "INSERT INTO ItemTable (key, value) VALUES (?, ?)",
            (
                CHAT_INDEX_KEY,
                json.dumps(
                    {
                        "entries": {
                            "vscode-session-1": {
                                "sessionId": "vscode-session-1",
                                "title": "VS Code fixture session",
                            }
                        }
                    }
                ),
            ),
        )
        conn.commit()
    finally:
        conn.close()


def copy_fixture_tree(tmp_path: Path) -> Path:
    storage_path = tmp_path / "workspaceStorage"
    for source in FIXTURE_ROOT.rglob("*"):
        if source.is_dir():
            continue
        target = storage_path / source.relative_to(FIXTURE_ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(source.read_bytes())
    create_state_db(storage_path)
    return storage_path


def test_read_chat_index_from_state_db(tmp_path: Path) -> None:
    storage_path = copy_fixture_tree(tmp_path)

    index = read_chat_index(storage_path)

    assert index["vscode-session-1"]["title"] == "VS Code fixture session"


def test_parse_vscode_storage_extracts_chat_transcript_and_editing_context(
    tmp_path: Path,
) -> None:
    storage_path = copy_fixture_tree(tmp_path)

    sessions = parse_storage(storage_path)

    assert len(sessions) == 1
    session = sessions[0]
    assert session.session_id == "vscode-session-1"
    assert session.title == "VS Code fixture session"
    assert len(session.chat_lines) == 3
    assert "Flask application context" in session.chat_lines[0].content
    assert len(session.transcript_events) == 2
    assert session.has_editing_context
    assert session.editing_session.content_count == 1
    assert session.editing_session.content_hashes == ["sha256-app-initial"]
