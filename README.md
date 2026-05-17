# blockguard

A Python CLI agent that turns natural language instructions into block-level edits on a Notion workspace, validates every proposed edit against the page's actual schema, and logs every attempt to Postgres.

## Setup

```
cp .env.example .env
# Fill in NOTION_TOKEN, OPENROUTER_API_KEY, DATABASE_URL

docker compose up -d        # start Postgres
pip install -r requirements.txt
python main.py setup        # create tables, verify Notion connection
```

## CLI Commands

```
python main.py apply "<instruction>" --page-id <id>
```

## Guardrails

The validation layer performs five explicit checks:

1. **Schema validation** — property exists and value matches the type
2. **Case-insensitive select matching** — case mismatches normalized to canonical option
3. **Block existence** — referenced block IDs exist in the fetched block tree
4. **Multi-intent detection** — conjunctions with multiple action clauses are rejected
5. **Destructive action guardrail** — delete/archive operations require explicit keywords

## Operations

| Operation | Description |
|---|---|
| `update_property` | Update a database entry property |
| `append_block` | Append a new block under a parent |
| `update_block_text` | Replace text content of an existing block |
| `archive_block` | Archive (delete) a block |
| `clarify` | Request clarification when ambiguous |
| `reject` | Reject invalid/unsafe instructions |
