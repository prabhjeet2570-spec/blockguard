# blockguard

A Python CLI agent that turns natural language instructions into block-level edits on a Notion workspace, validates every proposed edit against the page's actual schema, and logs every attempt to Postgres.

Built with `openai/gpt-4o-mini` via OpenRouter against the Notion API v2025-09-03.

---

## Setup

```bash
cp .env.example .env
# Fill in NOTION_TOKEN, OPENROUTER_API_KEY, DATABASE_URL

docker compose up -d
pip install -r requirements.txt
python main.py setup
```

---

## CLI

```
python main.py apply "<instruction>" --page-id <id>
python main.py eval --set <path> --tag <tag> [--no-validate] [--live --page-id-map <path>]
python main.py compare --tag-a <tag> --tag-b <tag>
python main.py log --last 20
python main.py reset-workspace --eval-set <path> --page-id-map <path>
python main.py setup
```

| Command | What it does |
|---|---|
| `apply` | Process one instruction against a Notion page — validates, logs, applies |
| `eval` | Run a full eval set against mock or live pages, save metrics to Postgres |
| `compare` | Diff two eval runs from the database |
| `log` | View recent edit attempts |
| `reset-workspace` | Restore live test pages to seed state |
| `setup` | Create Postgres tables, verify Notion connection |

---

## Architecture

### Agent

A function-calling wrapper around `gpt-4o-mini` (OpenRouter). Given an instruction and a page context (block tree, database properties, entries), it calls one of six tools:

| Tool | When |
|---|---|
| `update_property` | Change a database entry's property value |
| `append_block` | Add a new block under a parent |
| `update_block_text` | Rewrite an existing block's text |
| `archive_block` | Delete a block (requires explicit keyword) |
| `clarify` | Instruction is ambiguous or references something that doesn't exist |
| `reject` | Instruction is invalid, unsafe, or contains multiple distinct operations |

### Guardrails (5 checks, all local)

1. **Schema validation** — property exists and value matches type (select options, ISO date)
2. **Case-insensitive select normalization** — silently canonicalises casing
3. **Block existence** — referenced block IDs verified against fetched tree
4. **Multi-intent detection** — conjunction heuristics with action-verb clause counting
5. **Destructive action guardrail** — archive/delete requires explicit keyword

### Storage

Postgres 16 via Docker. Two tables:

- **`edit_log`** — every instruction attempt with proposed operation, validation result, latency, cost
- **`eval_runs`** — per-run summary metrics with 32-instruction detail

---

## Evaluations

### Setup

32 instructions across 6 categories. Every instruction has an explicit `page_ref` pointing to the page whose context the agent needs to see (database entries, block tree, properties). Instructions that require disambiguation between two pages (e.g. "Add a note to the Q3 page" — Q3 Planning in the tracker vs Q3 Kickoff Notes) reference both via `page_refs`.

| Category | Count | Expected |
|---|---|---|
| near_ambiguous | 6 | clarify |
| coreference_no_antecedent | 5 | clarify |
| schema_adjacent_invalid | 6 | reject |
| case_or_format_variance | 3 | update_property |
| compound | 10 | reject |
| test_precision | 2 | update_property / append_block |

### Ablation results

All runs against the mock page provider (identical code path to live Notion, no network dependency for inference). Metrics are for the 27 clarify/reject-expected instructions only (e31/e32 are real-op-expected and test the precision formula).

| Metric | Validation ON (range) | Validation OFF (range) |
|---|---|---|
| Accuracy | 1.0000 | 1.0000 |
| Refusal precision | 0.9545 – 0.9565 | 1.0000 |
| Refusal recall | 0.7778 – 0.8148 (21–22/27) | 0.1852 – 0.2222 (5–6/27) |
| False accept rate | 0.1852 – 0.2222 (5–6/27) | 0.7778 – 0.8148 (21–22/27) |
| Refusal precision* | 1.0000 | 1.0000 |

\* On the 30-instruction core set (e01–e30). Precision on the full 32-instruction set drops to 0.9545–0.9565 because e31 triggers a known false positive in the multi-intent heuristic (the word "complete" inside content text matches an action-verb check).

**Key finding:** giving the agent real page context (blocks, properties, entries) is essential — without it, the agent defaults to "clarify" on almost everything and the eval measures nothing. With real context, the unvalidated agent confidently applies wrong operations on 78–81% of instructions it should refuse. The guardrail cuts that to 19–22%.

### Run-to-run variance

False accept rate in the validation-OFF condition across 10 runs:

| Run | FAR | FA count |
|---|---|---|
| 1 | 0.7778 | 21/27 |
| 2 | 0.8148 | 22/27 |
| 3 | 0.7778 | 21/27 |
| 4 | 0.8519 | 23/27 |
| 5 | 0.8889 | 24/27 |
| 6 | 0.7778 | 21/27 |
| 7 | 0.8148 | 22/27 |
| 8 | 0.7778 | 21/27 |
| 9 | 0.8148 | 22/27 |
| 10 | 0.8148 | 22/27 |

Mean 0.8074 (21.8/27), max 24/27, min 21/27. The 3-instruction swing is entirely model nondeterminism between identical eval runs.

### Precision formula

```
refusal_precision = true_refusals / (true_refusals + false_refusals)
refusal_recall     = true_refusals / (true_refusals + false_accepts)
```

Where `true_refusals` counts clarify/reject-expected instructions whose outcome was clarified or rejected (the **lenient** rule — both subtypes count as correct because the eval measures whether the agent avoided applying a bad operation, not which sub-type of refusal it picked). `false_refusals` counts real-op-expected instructions whose outcome was clarified or rejected. `false_accepts` counts clarify/reject-expected instructions whose outcome was applied.

---

## Known Limitations

- **Multi-intent detection is heuristic-only** — no model-based second classification layer. Catches all 10 compound instructions in the eval set but the "complete" and "review" action-verb match inside content text produces a false positive (e31). A pure heuristic will always have blind spots.

- **No to-do toggle operation** — marking a to-do as done ("check off") is not a distinct operation type. The model falls back to `update_property` against a nonexistent property, which validation catches as a schema error rather than through genuine reference disambiguation. A dedicated to-do state-change operation is a scope gap.

- **ArchiveBlock not in the eval set** — defined and wired through the full stack but no current instruction tests it.

- **reset-workspace flattens nested blocks** — seed block trees are recreated as flat siblings because the Notion API doesn't reliably support post-creation block nesting.

---

## Repository structure

```
main.py                 — CLI entry point
src/
  agent.py              — gpt-4o-mini function-calling wrapper
  eval.py               — Eval harness (mock/live, multi-page context)
  models.py             — Pydantic v2 models for all operations
  notion_client.py      — httpx-based Notion API client + mock provider
  storage.py            — Postgres (edit_log, eval_runs)
  validate.py           — Five guardrail checks
data/
  eval/eval_set.json    — 32-instruction eval set
page-id-map.json        — Maps eval refs to live Notion page IDs
```
