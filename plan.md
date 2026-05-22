# Project Plan: Block-Level Editing Agent for Notion

## Target role
Notion, Software Engineer, New Grad (AI)

## Why this project
The JD is built around three tracks: AI product engineering (APIs, orchestration), model and systems engineering (guardrails, latency, cost, reliability), and evals and quality (build an eval set, run experiments, analyze results).

This project is scoped against a real gap in Notion's own tooling. Notion's hosted MCP server updates a page by loading the whole page and writing it back, rather than editing a single block. No public tool currently scores an agent's edit correctness or refusal behavior against a held-out set of instructions.

## Working name
**blockguard**

Core idea: an agent that turns a natural language instruction into a targeted, block-level edit on a real Notion page, refuses or asks for clarification when an instruction is ambiguous or would violate the page's structure, and gets scored on how often it makes the right call.

## Build order

### Phase 1: Notion workspace and API connection
- Create a Notion integration token, connect it to a test workspace
- Build a small set of real pages to operate against
- Write a thin client wrapper around the Notion SDK

### Phase 2: Core agent
- Take a natural language instruction and the current block tree as input
- Use structured output or tool calling to produce a proposed operation
- Each operation is a Pydantic model with required fields

### Phase 3: Validation and guardrail layer
- Before any operation is applied, validate it against the current block tree
- Reject operations that reference something ambiguous
- Allow one retry: if validation fails, feed the error back to the agent

### Phase 4: Postgres logging layer
- Two tables: `edit_log` and `eval_runs`
- Every instruction processed writes a row to `edit_log`

### Phase 5: Eval set construction
- Write 30 instructions across 5 categories
- Record the expected outcome ahead of time for every instruction

### Phase 6: Run the eval and score it
- Run all 30 instructions through the full pipeline
- Score: accuracy, refusal precision, refusal recall, false accept rate

### Phase 7: Reliability layer
- Cache for repeated or near-duplicate instructions
- Fallback to a cheaper model for simple operations

### Phase 8: README
- Write this last, once every number above is real

## Folder structure
```
blockguard/
  data/eval/eval_set_v1.json
  src/
    notion_client.py
    agent.py
    validate.py
    storage.py
    eval.py
  main.py
  docker-compose.yml
  requirements.txt
  README.md
```
