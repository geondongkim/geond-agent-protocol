# Workspace Identity And Search

This note answers two operational questions that matter once Geond is used by
multiple local agents over time:

1. What happens when a project folder is renamed or moved?
2. How should search stay fast and accurate across English, Korean, code, and
	 agent-specific terminology?

## Folder Moves And Workspace Identity

Geond should not treat a folder path as the durable identity of a project. A
path is an observation; the durable identity is `workspaces.id`.

Current behavior:

- `workspaces.root_uri` remains the primary URI shown to users.
- `workspace_aliases` records additional URIs for the same workspace, such as a
	renamed folder or a moved checkout.
- `register_workspace_alias` lets a CLI or MCP client attach a new URI to an
	existing workspace.
- `upsert_workspace` reuses the original workspace when called with a registered
	alias URI, so future imports from the moved folder do not create a duplicate
	workspace.
- `search_dev_memory`, vector search, workspace resources, purge, and benchmark
	reports resolve workspace URIs through aliases before filtering.
- `workspace_fingerprints` stores durable hints such as sanitized git remote URL
	and first commit, so Geond can suggest likely aliases before merging anything.

MCP clients should handle a folder move like this:

```text
register_workspace_alias(
	workspace_id_or_uri="file:///old/path/project",
	alias_uri="file:///new/path/project",
	reason="folder-move"
)
```

After that, queries scoped to either URI resolve to the same workspace. Session
names and external session IDs can still differ by agent and by tool, but they
remain attached to the same `workspace_id`. The uniqueness boundary is
`(workspace_id, source, external_id)`, not folder name.

Important limitation: if a project appears at a completely new path with no
registered alias, no git metadata, and no persisted fingerprint, Geond should not
guess automatically. Automatic merging should stay conservative; fingerprint
matches produce suggestions, and `--register-best` only registers a single
high-confidence candidate.

CLI flow for a moved git checkout:

```bash
uv run geond fingerprint-workspace "file:///old/path/project" "/old/path/project"
uv run geond suggest-workspace-aliases "/new/path/project" --register-best
```

MCP clients can use `record_workspace_fingerprints` and
`suggest_workspace_aliases` when they already know the durable repository
fingerprints. The git remote fingerprint is sanitized before storage so URL
credentials are not persisted.

## Search Strategy

Postgres already gives Geond a practical local-first search stack:

- `to_tsvector('simple', ...)` + GIN is an inverted index for token search.
- `pg_trgm` + GIN accelerates substring and fuzzy-ish matching, which helps with
	Korean text, file paths, symbols, partial terms, and mixed code/chat strings.
- pgvector HNSW supports semantic retrieval when embeddings are available.
- Hybrid search merges lexical and vector candidates, then Geond can expand via
	symbols, changesets, and call graph evidence.

For the current OSS MVP, this is the right default: fewer moving pieces, local
privacy, one database backup story, and good enough quality for agent memory.

Elasticsearch or OpenSearch plus CDC becomes attractive when these signals show
up:

- Search traffic needs independent scaling from the write database.
- The corpus grows beyond what local Postgres can comfortably index and vacuum.
- Rich language analyzers, query-time synonyms, or per-field BM25 tuning become
	must-have rather than nice-to-have.
- Multi-tenant operations need shard allocation, index lifecycle management, and
	operational dashboards.

CDC is useful only when an external index is worth operating. It adds failure
modes: lag, replay, mapping migrations, duplicate delivery, and backfill logic.
Until those costs are justified, Geond should prefer Postgres projections and
idempotent rebuilds.

## Multilingual And Morphological Analysis

Korean morphological analysis can improve lexical precision, but it should be an
optional later layer rather than the first dependency.

Recommended order:

1. Keep `simple` full-text search for portable token indexing.
2. Add `pg_trgm` for multilingual partial matching and typo tolerance.
3. Use embeddings for semantic cross-lingual recall.
4. Add reranking over top candidates.
5. Add language-specific analyzers only when benchmark judgments show lexical
	 failures that trigram + embeddings cannot cover.

If Geond later adopts Elasticsearch/OpenSearch, Korean analyzers such as Nori can
be tested as an optional indexing backend. The protocol should keep evidence IDs
and retrieval response shapes stable so clients do not care which backend found
the candidate.

## Practical Query Pipeline

The target retrieval pipeline is:

1. Resolve workspace aliases to `workspace_id`.
2. Pull lexical candidates from GIN full-text and trigram indexes.
3. Pull semantic candidates from pgvector when embeddings exist.
4. Merge with reciprocal-rank or weighted scoring.
5. Expand candidates through changesets, symbols, file paths, sessions, and call
	 graph edges.
6. Return `geond.evidence.v1` refs and optional deterministic narratives.

This keeps Geond fast locally while leaving room for a pluggable search backend
when the project has enough data to justify it.
