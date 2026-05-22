# blockguard — Build & Eval Results

## Summary

A Python CLI agent that translates natural language into block-level Notion edits with a guardrail validation layer, logging every attempt to Postgres. Built to spec using `openai/gpt-4o-mini` via OpenRouter with function calling, against Notion API v2025-09-03.

## Ablation Results (30-instruction eval set, live Notion workspace)

| Metric | Validation ON | Validation OFF | Delta |
|---|---|---|---|
| Accuracy | 1.0000 | 1.0000 | 0 |
| Refusal precision | 1.0000 | 0.8148 | -0.1852 |
| Refusal recall | 1.0000 | 0.8148 | -0.1852 |
| False accept rate | 0.0000 | 0.1852 | +0.1852 |
| Avg latency | 1049 ms | 1143 ms | +94 ms |
| P95 latency | 1488 ms | 1799 ms | +311 ms |

Without validation, 5 of 27 clarify/reject-expected instructions were falsely accepted (all compound instructions the model processed as single operations). The validation layer's multi-intent heuristic caught all 5, driving false accept rate to zero.

## Eval Set Composition

30 instructions across 5 categories:

| Category | Count | Expected outcome |
|---|---|---|
| `near_ambiguous` | 6 | clarify |
| `coreference_no_antecedent` | 5 | clarify |
| `schema_adjacent_invalid` | 6 | reject |
| `case_or_format_variance` | 3 | update_property |
| `compound` | 10 | reject |

## Guardrail Layer — 5 Explicit Checks

1. **Schema validation**: property existence + type match (select options, date format)
2. **Case-insensitive select normalization**: silently canonicalizes ("in progress" → "In Progress")
3. **Block existence**: referenced block IDs validated against fetched block tree
4. **Multi-intent detection**: conjunction heuristics with action verb clause counting
5. **Destructive action guardrail**: ArchiveBlock requires explicit keywords in instruction

## Known Limitations

- **Multi-intent detection is heuristic-only**: no model-based second classification layer
- **ArchiveBlock not in eval set**: defined and wired through full stack but no current test
- **reset-workspace flattens nested blocks**: seed block trees flattened to siblings

## Bugs Fixed During Implementation

1. Case normalization was dead code — `_normalize_select_value` existed but was never called
2. Date validation was missing — non-ISO strings like "next quarter" passed through silently
3. Multi-intent heuristic missed shared-verb compounds — second clause had no verb
4. Eval runner miscounted Reject outcomes as "applied"
5. Page context resolution used leaked loop variable
6. Notion API v2025-09-03 separates schema from databases — properties live on data sources
