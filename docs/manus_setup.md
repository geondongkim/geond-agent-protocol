# Manus Integration Setup

## Prerequisites

- Geond installed and configured (`geond --help` works)
- PostgreSQL running with schema applied (`uv run geond init-schema --workspace-uri <uri>`)
- Manus API key from [manus.ai](https://manus.ai)

## 1. Configure API Key

```bash
export MANUS_API_KEY=your_key_here
```

The key is read from the environment at runtime. It is never written to disk, logs, or the database.

## 2. Import a Single Task

```bash
geond import-manus-task task-abc123 \
  --workspace-uri file:///path/to/project
```

Message attachments are imported as metadata-only file artifacts when they are
present in `task.listMessages`.

Dry-run (no DB writes):

```bash
geond import-manus-task task-abc123 --dry-run
```

Using a local fixture (no API key required):

```bash
geond import-manus-task --fixture tests/fixtures/manus/task_detail_completed.json \
  --fixture-messages tests/fixtures/manus/task_messages_completed.json \
  --workspace-uri file:///path/to/project \
  --dry-run
```

## 3. Bulk Import

```bash
geond import-manus-tasks \
  --workspace-uri file:///path/to/project \
  --limit 50 \
  --status stopped
```

## 4. List Tasks

```bash
# Table format (default)
geond list-manus-tasks

# JSON format, show private URLs
geond list-manus-tasks --format json --show-private-url
```

## 5. Download a File Artifact

```bash
geond manus-get-file \
  --task-id task-abc123 \
  --file-id file-xyz789 \
  --output ./output.pdf
```

Without `--output`, raw bytes are written to stdout. This command is best-effort:
current Manus public docs expose message attachment URLs and file upload/detail
endpoints, but not a general task file download endpoint for every attachment.

## 6. View Stored Task Dashboard

```bash
geond manus-dashboard --workspace-uri file:///path/to/project
```

JSON format:

```bash
geond manus-dashboard --workspace-uri file:///path/to/project --format json
```

## 7. Search Imported Content

```bash
geond search "authentication middleware" \
  --workspace-uri file:///path/to/project \
  --source manus
```

## Redaction

Before any data is written to the database, the redaction pipeline scans all message content and metadata for patterns that look like secrets (API keys, tokens, passwords). Findings are stored separately in `redaction_findings` and the original values are replaced with `[REDACTED]`.

**Redaction is applied automatically** — no user action required. To audit what was redacted:

```sql
SELECT source_id, pattern_type, field_path
FROM redaction_findings
WHERE source = 'manus'
ORDER BY created_at DESC
LIMIT 50;
```

## Privacy and URL Handling

- `task_url` and `share_url` are stored in session metadata **only if `share_visibility = "public"`**.
- Private URLs are never persisted to the database.
- The `list-manus-tasks` command masks URLs in output unless `--show-private-url` is passed.

## Known Limitations

- **API drift**: The Manus API v2 shape is reverse-engineered from live responses. Field names may change without notice. The adapter normalises both `id`/`task_id` and `title`/`task_title` formats to handle fixture/legacy shapes.
- **Connector permissions**: Connector UUIDs are intentionally not stored. Only the count (`connector_count`) is kept in session metadata, as connector IDs may identify third-party integrations.
- **File content**: Files are metadata-only by default. Message attachment URLs are stored after redaction. Content download via `manus-get-file` is capped at 10 MB (`MAX_FILE_DOWNLOAD_BYTES`) when the backing Manus endpoint is available. Binary files are not embedded in messages.
- **Rate limits**: The API client retries up to 3 times with exponential backoff on `429` responses.
- **No streaming**: Message pagination is handled automatically, but real-time task streaming is not supported.
- **Blocked tasks**: Tasks with current API status `waiting`, or legacy/imported statuses `needs_input`, `waiting_for_input`, `blocked`, `paused`, `input_required`, or `waiting_for_user`, have `is_blocked=True` in session metadata. They are fully imported but no special action is taken automatically.
