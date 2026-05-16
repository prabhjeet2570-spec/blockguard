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
    print("Eval command not yet implemented.")


def cmd_compare(args):
    load_dotenv()
    print("Compare command not yet implemented.")


def cmd_reset_workspace(args):
    load_dotenv()
    print("Reset workspace command not yet implemented.")


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
