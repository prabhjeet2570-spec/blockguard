#!/usr/bin/env python3
import argparse
import json
import os
import sys
import time

from dotenv import load_dotenv

from src.models import Clarify, Reject, UpdateProperty, AppendBlock, UpdateBlockText, ArchiveBlock
from src.notion_client import NotionClientWrapper, build_notion_block
from src.agent import Agent
from src.validate import Validator
from src.storage import Storage
from src.eval import EvalRunner


def cmd_setup(args):
    load_dotenv()
    storage = Storage()
    print("Creating database tables...")
    storage.setup()
    storage.close()
    print("Done. Tables created.")

    try:
        notion = NotionClientWrapper()
        me = notion._get("/users/me")
        name = me.get("name", "unknown")
        print(f"Notion connection OK — authenticated as: {name}")
    except Exception as e:
        print(f"Notion connection failed: {e}")
        print("Check your NOTION_TOKEN in .env")


def cmd_apply(args):
    load_dotenv()
    instruction = " ".join(args.instruction)
    page_id = args.page_id

    notion = NotionClientWrapper()
    agent = Agent()
    validator = Validator()
    storage = Storage()

    page_context = notion.extract_page_context(page_id)

    start = time.monotonic()
    agent_result = agent.process(instruction, page_context)
    latency_ms = int((time.monotonic() - start) * 1000)

    if isinstance(agent_result, Clarify):
        print(f"Clarification requested: {agent_result.reason}")
        storage.log_edit(
            instruction=instruction,
            proposed_operation=agent_result.model_dump(),
            validation_passed=True,
            validation_reason=None,
            outcome="clarified",
            validation_enabled=True,
            latency_ms=latency_ms,
            model=agent.model,
        )
        return

    if isinstance(agent_result, Reject):
        print(f"Rejected: {agent_result.reason}")
        storage.log_edit(
            instruction=instruction,
            proposed_operation=agent_result.model_dump(),
            validation_passed=False,
            validation_reason=agent_result.reason,
            outcome="rejected",
            validation_enabled=True,
            latency_ms=latency_ms,
            model=agent.model,
        )
        return

    applied = _apply_operation(notion, agent_result, page_context)
    if applied:
        print(f"Applied: {agent_result.operation}")
        storage.log_edit(
            instruction=instruction,
            proposed_operation=agent_result.model_dump(),
            validation_passed=True,
            outcome="applied",
            validation_enabled=True,
            latency_ms=latency_ms,
            model=agent.model,
        )
    else:
        print("Failed to apply operation.")


def _apply_operation(notion, operation, page_context) -> bool:
    try:
        if isinstance(operation, UpdateProperty):
            prop_schema = page_context.properties.get(operation.property_name, {})
            prop_type = (
                prop_schema.get("type", "rich_text")
                if isinstance(prop_schema, dict) else "rich_text"
            )
            value = notion.build_property_value(prop_type, operation.property_value)
            if value is None:
                return False
            notion.update_page_property(operation.page_ref, {operation.property_name: value})
            return True

        elif isinstance(operation, AppendBlock):
            block_data = build_notion_block(operation.block_type, operation.content)
            notion.append_block_children(operation.parent_block_id, [block_data])
            return True

        elif isinstance(operation, UpdateBlockText):
            notion.update_block(operation.block_id, {
                _block_type_for_text_update(page_context, operation.block_id): {
                    "rich_text": [{"text": {"content": operation.new_text}}],
                },
            })
            return True

        elif isinstance(operation, ArchiveBlock):
            notion.update_block(operation.block_id, {"archived": True})
            return True

    except Exception as e:
        print(f"Error applying operation: {e}")
        return False

    return False


def _block_type_for_text_update(page_context, block_id: str) -> str:
    def find_type(blocks):
        for b in blocks:
            if b.id == block_id or b.id.replace("-", "") == block_id.replace("-", ""):
                return b.type
            found = find_type(b.children)
            if found:
                return found
        return None

    return find_type(page_context.blocks) or "paragraph"


def cmd_log(args):
    load_dotenv()
    storage = Storage()
    logs = storage.get_recent_logs(limit=args.last)
    if not logs:
        print("No logs found.")
        return
    for log in logs:
        ts = log.get("created_at", "")
        if hasattr(ts, "strftime"):
            ts = ts.strftime("%Y-%m-%d %H:%M:%S")
        outcome = log.get("outcome", "?")
        instr = (log.get("instruction", "") or "")[:60]
        print(f"[{ts}] {outcome:10s} | {log.get('model', ''):30s} | {log.get('latency_ms', 0):4d}ms | {instr}")


def cmd_eval(args):
    load_dotenv()

    if args.report:
        storage = Storage()
        runs = storage.get_eval_runs(tag=args.tag)
        if not runs:
            print("No eval runs found.")
            return
        for run in runs:
            print(f"\nEval run — v{run.get('eval_set_version', '?')} (tag: {run.get('tag', 'none')})")
            print(f"  Accuracy:          {run.get('accuracy', 'N/A')}")
            print(f"  Refusal precision: {run.get('refusal_precision', 'N/A')}")
            print(f"  Refusal recall:    {run.get('refusal_recall', 'N/A')}")
            print(f"  False accept rate: {run.get('false_accept_rate', 'N/A')}")
            print(f"  Avg latency:       {run.get('avg_latency_ms', 'N/A')} ms")
            print(f"  P95 latency:       {run.get('p95_latency_ms', 'N/A')} ms")
            print(f"  Total cost:        ${run.get('total_cost_usd', 0):.4f}")
        return

    if not args.eval_set:
        print("Error: --set is required to run eval (or use --report to view past runs)")
        sys.exit(1)

    with open(args.eval_set) as f:
        eval_set = json.load(f)

    page_id_map = None
    if args.page_id_map:
        with open(args.page_id_map) as f:
            page_id_map = json.load(f)

    agent = Agent()
    validator = Validator()
    storage = Storage()

    runner = EvalRunner(agent, validator, storage)
    report = runner.run(
        eval_set,
        tag=args.tag,
        skip_validation=args.no_validate,
        live=args.live,
        page_id_map=page_id_map,
    )
    EvalRunner.print_report(report)


def cmd_compare(args):
    load_dotenv()
    storage = Storage()
    run_a = storage.get_eval_runs(tag=args.tag_a)
    run_b = storage.get_eval_runs(tag=args.tag_b)

    if not run_a or not run_b:
        print("One or both tags not found.")
        return

    a = run_a[-1]
    b = run_b[-1]

    print(f"\n{'='*60}")
    print(f"{'Metric':25s} {'A':15s} {'B':15s}")
    print(f"{'-'*60}")
    metrics = [
        "accuracy", "refusal_precision", "refusal_recall",
        "false_accept_rate", "avg_latency_ms", "p95_latency_ms", "total_cost_usd",
    ]
    for m in metrics:
        va = a.get(m, 0) or 0
        vb = b.get(m, 0) or 0
        diff = vb - va
        if isinstance(va, float):
            diff_str = f"(Δ {diff:+.4f})"
        else:
            diff_str = f"(Δ {diff:+.4f})"
        print(f"{m:25s} {va!r:>15} {vb!r:>15} {diff_str}")
    print(f"{'='*60}\n")


def cmd_reset_workspace(args):
    load_dotenv()
    if not args.eval_set or not args.page_id_map:
        print("Usage: python main.py reset-workspace --eval-set <path> --page-id-map <path>")
        return

    with open(args.eval_set) as f:
        eval_set = json.load(f)
    with open(args.page_id_map) as f:
        page_id_map = json.load(f)

    notion = NotionClientWrapper()
    pages_def = eval_set.get("pages", {})

    for ref, seed_page in pages_def.items():
        real_id = page_id_map.get(ref)
        if not real_id:
            print(f"  Skipping {ref}: no mapping in page-id-map")
            continue

        page_type = seed_page.get("type", "page")
        print(f"Resetting {ref} ({page_type})...")

        if page_type == "database":
            data_sources = seed_page.get("data_sources", [])
            ds_id = None
            if data_sources:
                ds_id = data_sources[0].get("id")
            if not ds_id:
                db_resp = notion._get(f"/databases/{real_id}")
                dss = db_resp.get("data_sources", [])
                if dss:
                    ds_id = dss[0].get("id")
            if not ds_id:
                print(f"  Skipping database {ref}: no data source ID found")
                continue
            rows = notion.query_data_source(ds_id)
            seed_entries = {b["text"].split("|")[0].strip(): b for b in seed_page.get("blocks", []) if b["type"] == "child_database_entry"}
            for row in rows:
                row_id = row["id"]
                row_props = row.get("properties", {})
                row_title = ""
                for k, v in row_props.items():
                    if v.get("type") == "title":
                        row_title = "".join(t.get("plain_text", "") for t in v.get("title", []))
                if row_title in seed_entries:
                    seed_entry = seed_entries[row_title]
                    seed_text = seed_entry.get("text", "")
                    seed_props_str = seed_text.split("|", 1)[-1].strip() if "|" in seed_text else "{}"
                    try:
                        seed_props = eval(seed_props_str)
                    except Exception:
                        continue
                    update = {}
                    for prop_name, prop_val in seed_props.items():
                        if prop_name in row_props:
                            prop_type = row_props[prop_name].get("type", "")
                            pv = notion.build_property_value(prop_type, str(prop_val) if prop_val is not None else "")
                            if pv is not None:
                                update[prop_name] = pv
                    if update:
                        notion.update_page_property(row_id, update)
                        print(f"    Updated entry '{row_title}': {update}")
        else:
            notion.reset_page_from_seed(real_id, seed_page.get("blocks", []))
            print(f"    Recreated page blocks")

    print("Done. Workspace reset to seed state.")


def main():
    load_dotenv()

    parser = argparse.ArgumentParser(description="blockguard — block-level editing agent for Notion")
    sub = parser.add_subparsers(dest="command", required=True)

    p_setup = sub.add_parser("setup", help="Create database tables and verify connections")
    p_setup.set_defaults(func=cmd_setup)

    p_apply = sub.add_parser("apply", help="Apply a natural language instruction to a Notion page")
    p_apply.add_argument("instruction", nargs="+", help="Natural language instruction")
    p_apply.add_argument("--page-id", required=True, help="Notion page ID to operate on")
    p_apply.set_defaults(func=cmd_apply)

    p_log = sub.add_parser("log", help="View recent edit log entries")
    p_log.add_argument("--last", type=int, default=10, help="Number of entries to show")
    p_log.set_defaults(func=cmd_log)

    p_eval = sub.add_parser("eval", help="Run evaluation or view results")
    p_eval.add_argument("--set", dest="eval_set", help="Path to eval set JSON file")
    p_eval.add_argument("--report", action="store_true", help="Print evaluation report from database")
    p_eval.add_argument("--tag", help="Tag for this eval run")
    p_eval.add_argument("--no-validate", action="store_true", help="Skip validation layer (ablation)")
    p_eval.add_argument("--live", action="store_true", help="Run against live Notion API")
    p_eval.add_argument("--page-id-map", help="Path to JSON mapping page refs to real Notion IDs")
    p_eval.set_defaults(func=cmd_eval)

    p_compare = sub.add_parser("compare", help="Compare two eval runs")
    p_compare.add_argument("--tag-a", required=True, help="Tag for the first run")
    p_compare.add_argument("--tag-b", required=True, help="Tag for the second run")
    p_compare.set_defaults(func=cmd_compare)

    p_reset = sub.add_parser("reset-workspace", help="Restore live Notion test pages to seed state")
    p_reset.add_argument("--eval-set", required=True, help="Path to eval set JSON with seed state")
    p_reset.add_argument("--page-id-map", required=True, help="Path to JSON mapping page refs to real Notion IDs")
    p_reset.set_defaults(func=cmd_reset_workspace)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
