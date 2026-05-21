# Prompt for DeepSeek: Build/finish blockguard

Copy everything below into DeepSeek as one message.

---

I'm building **blockguard**, a Python CLI agent that turns natural language instructions into block-level edits on a real Notion workspace, validates every proposed edit against the page's actual schema before applying it, and logs every attempt to Postgres for evaluation. I need you to help me build this out completely and correctly. Read this whole spec before writing any code.

## What it does

Input: a natural language instruction plus a target Notion page or database ID.
Output: either an applied edit, a clarification request (instruction is ambiguous), or a rejection (instruction is invalid, unsafe, or malformed), always logged to Postgres regardless of outcome.

## Model configuration — use gpt-4o-mini only

All LLM calls go through OpenRouter using `openai/gpt-4o-mini` exclusively. No other model paths, no provider abstraction beyond what's needed to call OpenRouter's `/v1/chat/completions`-compatible endpoint with an API key from `OPENROUTER_API_KEY` in `.env`. Use OpenAI-style function calling / structured output (`response_format` with a JSON schema, or tool calling if that's more reliable through OpenRouter for this model) to force the model to return one of these operation types:

```python
class UpdateProperty(BaseModel):
    operation: Literal["update_property"]
    page_ref: str
    property_name: str
    property_value: str

class AppendBlock(BaseModel):
    operation: Literal["append_block"]
    page_ref: str
    parent_block_id: str
    block_type: Literal["paragraph", "to_do", "callout", "bulleted_list_item", "numbered_list_item", "heading_1", "heading_2", "heading_3"]
    content: str

class UpdateBlockText(BaseModel):
    operation: Literal["update_block_text"]
    page_ref: str
    block_id: str
    new_text: str

class Clarify(BaseModel):
    operation: Literal["clarify"]
    reason: str

class Reject(BaseModel):
    operation: Literal["reject"]
    reason: str
```

## Guardrail / validation layer — must do real, structural work

This is the most important part. Do not build a validation layer that only incidentally catches bad operations through unrelated checks (e.g. only catching a bad instruction because it happened to reference a nonexistent block ID). Build these as explicit, named checks, each independently testable.

Structure this as a `validate(operation, page_state) -> ValidationResult` function with one clearly named sub-check per failure mode, so an ablation test (running with validation disabled) is a trivial flag, not a rewrite.

## Notion API integration

Use Notion API version `2025-09-03`. This version splits databases from data sources:
- `GET /v1/pages/{page_id}` and `GET /v1/blocks/{page_id}/children` for regular pages
- `GET /v1/databases/{database_id}` for database metadata/schema only
- `POST /v1/data_sources/{data_source_id}/query` for querying actual database rows — the data source ID comes from the `data_sources` array in the database GET response, it is not the same as the database ID

Write a thin `notion_client.py` wrapping these operations.

## Postgres logging

Two tables: `edit_log` and `eval_runs`. Every single instruction processed writes one row to `edit_log`. Eval runs additionally write one summary row to `eval_runs`.

## Eval harness requirements

Reads `eval_set.json` (30 instructions). Each instruction has: `id`, `category`, `instruction`, and `expected`. Score using one consistent denominator for false accept rate and refusal recall.

CLI commands needed: `setup`, `apply`, `eval`, `compare`, `reset-workspace`.

## Deliverables

Give me the full repo: `main.py`, `src/notion_client.py`, `src/agent.py`, `src/validate.py`, `src/storage.py`, `src/eval.py`, `requirements.txt`, `docker-compose.yml` for Postgres, `.env.example`, and a short `README.md`.
