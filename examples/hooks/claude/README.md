# Claude Hook Adapter

This example records lightweight Claude Code lifecycle events into Geond.

It intentionally avoids raw prompts, secrets, full stdout, and full stderr.
Use summaries and explicit validation commands instead.

```bash
export GEOND_WORKSPACE="file:///absolute/path/to/repo"
export GEOND_AGENT_SESSION_ID="claude-session-1"

./record-hook.sh session_start
./record-hook.sh heartbeat

GEOND_RUN_ID="run-id" \
GEOND_TASK_ID="task-id" \
GEOND_VALIDATION_COMMAND="uv run pytest" \
GEOND_VALIDATION_EXIT_CODE="0" \
./record-hook.sh validation
```
