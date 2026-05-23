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
Process a single instruction against a Notion page/database. Validates before applying; logs every attempt.

```
python main.py eval --set <path> --tag <tag> [--no-validation] [--live --page-id-map <path>]
```
Run an eval set against mocked or live Notion pages. `--no-validation` disables the guardrail layer (ablation). Use `--live` with `--page-id-map` to run against real Notion pages.

```
python main.py compare --tag-a <tag> --tag-b <tag>
```
Diff the summary metrics of two eval runs stored in Postgres.

```
python main.py log --last 20
```
View recent edit log entries.

```
python main.py reset-workspace --eval-set <path> --page-id-map <path>
```
Restore live Notion test pages to the seed state defined in the eval set, using real page IDs from the map.

```
python main.py setup
```
Create Postgres tables (`edit_log`, `eval_runs`) and verify the Notion API connection.

```
python main.py status
```
Show database connection stats and Notion API authentication status.

## Guardrails

The validation layer performs five explicit checks:

1. **Schema validation** — property exists and value matches the type (select options, date format, etc.)
2. **Case-insensitive select matching** — case mismatches are silently normalized to the canonical option
3. **Block existence** — referenced block IDs exist in the fetched block tree
4. **Multi-intent detection** — conjunctions ("and", "then") with multiple action clauses are rejected
5. **Destructive action guardrail** — delete/archive operations require explicit keywords in the instruction.

## Operations

| Operation | Description |
|---|---|
| `update_property` | Update a database entry property (select, date, rich_text, etc.) |
| `append_block` | Append a new block (paragraph, to_do, heading_*, etc.) under a parent |
| `update_block_text` | Replace the text content of an existing block |
| `archive_block` | Archive (delete) a block — requires explicit keyword in instruction |
| `clarify` | Request clarification when instruction is ambiguous |
| `reject` | Reject an invalid, unsafe, or multi-intent instruction |

## Ablation results (gpt-4o-mini via OpenRouter)

30-instruction eval set against a live Notion workspace:

| Metric | Validation ON | Validation OFF | Delta |
|---|---|---|---|
| Accuracy | 1.0000 | 1.0000 | 0 |
| Refusal precision | 1.0000 | 0.8148 | -0.1852 |
| Refusal recall | 1.0000 | 0.8148 | -0.1852 |
| False accept rate | 0.0000 | 0.1852 | +0.1852 |
| Avg latency | 1049 ms | 1143 ms | +94 ms |
| P95 latency | 1488 ms | 1799 ms | +311 ms |

Without validation, 5 of 27 clarify/reject-expected instructions were falsely accepted (all compound instructions that the model processed as single operations). With validation enabled, the multi-intent heuristic caught all 5, driving false accept rate to zero.
