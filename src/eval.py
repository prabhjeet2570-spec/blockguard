import json
import math
import os
import time
from typing import Any, Optional

from .models import (
    AgentResult, Clarify, Reject,
    UpdateProperty, AppendBlock, UpdateBlockText,
    PageContext, BlockInfo, ValidationResult,
)
from .agent import Agent
from .validate import Validator
from .notion_client import MockNotionClient


class EvalRunner:
    def __init__(self, agent: Agent, validator: Validator, storage=None):
        self.agent = agent
        self.validator = validator
        self.storage = storage

    @staticmethod
    def _collect_page_refs(instructions: list[dict]) -> list[str]:
        refs: set[str] = set()
        for item in instructions:
            expected = item.get("expected", {})
            pr = expected.get("page_ref", "")
            if pr:
                refs.add(pr)
            prs = expected.get("page_refs", [])
            if prs:
                refs.update(prs)
        return sorted(refs)

    @staticmethod
    def _build_mock_context(page_def: dict, ref: str) -> PageContext:
        blocks = [BlockInfo(**b) for b in page_def.get("blocks", [])]
        return PageContext(
            page_id=page_def.get("id", ref),
            title=page_def.get("title", ""),
            page_type=page_def.get("type", "page"),
            properties=page_def.get("properties", {}),
            blocks=blocks,
        )

    @staticmethod
    def _merge_page_contexts(contexts: list[PageContext]) -> PageContext:
        if not contexts:
            return PageContext(page_id="default", title="Default Page", blocks=[])
        if len(contexts) == 1:
            return contexts[0]
        titles = [c.title for c in contexts if c.title]
        merged_title = " + ".join(titles)
        merged_properties: dict[str, Any] = {}
        merged_blocks: list[BlockInfo] = []
        for ctx in contexts:
            merged_properties.update(ctx.properties)
            merged_blocks.extend(ctx.blocks)
        return PageContext(
            page_id=contexts[0].page_id,
            title=merged_title,
            page_type=contexts[0].page_type,
            properties=merged_properties,
            blocks=merged_blocks,
        )

    def run(
        self,
        eval_set: dict,
        tag: Optional[str] = None,
        skip_validation: bool = False,
        live: bool = False,
        page_id_map: Optional[dict[str, str]] = None,
    ) -> dict:
        pages_def = eval_set.get("pages", {})
        instructions = eval_set.get("instructions", [])
        version = eval_set.get("version", "v1")

        all_needed_refs = self._collect_page_refs(instructions)

        if live and page_id_map:
            from .notion_client import NotionClientWrapper
            notion = NotionClientWrapper()
            page_context_map: dict[str, PageContext] = {}
            for ref in all_needed_refs:
                real_id = page_id_map.get(ref)
                if real_id:
                    page_context_map[ref] = notion.extract_page_context(real_id)
        else:
            mock = MockNotionClient()
            page_context_map = {}
            for ref in all_needed_refs:
                pdef = pages_def.get(ref)
                if pdef:
                    ctx = self._build_mock_context(pdef, ref)
                    page_context_map[ref] = ctx
                    mock.register_page(pdef.get("id", ref), ctx)
            notion = mock

        results = []
        latencies = []
        total_cost = 0.0

        real_ops_count = 0
        real_ops_correct = 0
        clarify_reject_expected_count = 0
        clarify_reject_issued_count = 0
        false_accepts = 0
        false_refusals = 0

        for item in instructions:
            instruction = item["instruction"]
            expected = item.get("expected", {})
            expected_type = expected.get("type", "")

            page_ref = expected.get("page_ref", "")
            page_refs = expected.get("page_refs", [])

            if page_ref:
                refs_to_use = [page_ref]
            elif page_refs:
                refs_to_use = list(page_refs)
            else:
                refs_to_use = []

            if refs_to_use:
                if live and page_id_map:
                    resolved = []
                    for ref in refs_to_use:
                        ctx = page_context_map.get(ref)
                        if ctx:
                            resolved.append(ctx)
                        else:
                            real_id = page_id_map.get(ref, ref)
                            ctx = notion.extract_page_context(real_id)
                            resolved.append(ctx)
                    page_context = self._merge_page_contexts(resolved)
                else:
                    resolved = []
                    for ref in refs_to_use:
                        ctx = page_context_map.get(ref)
                        if ctx:
                            resolved.append(ctx)
                        else:
                            pdef = pages_def.get(ref)
                            if pdef:
                                resolved.append(self._build_mock_context(pdef, ref))
                    page_context = self._merge_page_contexts(resolved)
            else:
                page_context = self._default_context()

            start = time.monotonic()
            agent_result = self.agent.process(instruction, page_context)
            latency_ms = int((time.monotonic() - start) * 1000)
            latencies.append(latency_ms)
            total_cost += self._estimate_cost(self.agent.model)

            is_clarify = isinstance(agent_result, Clarify)
            is_reject = isinstance(agent_result, Reject)

            if skip_validation:
                validation_passed = True
                validation_reason = None
                outcome = (
                    "clarified" if is_clarify
                    else "rejected" if is_reject
                    else "applied"
                )
            else:
                validation = self.validator.validate(agent_result, page_context, instruction)
                validation_passed = validation.valid
                validation_reason = "; ".join(e.message for e in validation.errors) if validation.errors else None
                if is_clarify:
                    outcome = "clarified"
                elif is_reject:
                    outcome = "rejected"
                elif not validation_passed:
                    outcome = "rejected"
                else:
                    outcome = "applied"

            result_entry = {
                "id": item["id"],
                "instruction": instruction,
                "expected_type": expected_type,
                "actual_type": self._result_type(agent_result),
                "agent_result": agent_result.model_dump() if hasattr(agent_result, "model_dump") else {},
                "validation_passed": validation_passed if not skip_validation else True,
                "validation_reason": validation_reason,
                "outcome": outcome,
                "latency_ms": latency_ms,
                "correct": None,
            }

            if expected_type in ("update_property", "append_block", "update_block_text"):
                real_ops_count += 1
                is_correct = self._match_operation(agent_result, expected)
                result_entry["correct"] = is_correct
                if is_correct:
                    real_ops_correct += 1
                if outcome in ("clarified", "rejected"):
                    false_refusals += 1

            elif expected_type in ("clarify", "reject"):
                clarify_reject_expected_count += 1
                if outcome in ("clarified", "rejected"):
                    clarify_reject_issued_count += 1
                if outcome == "applied":
                    false_accepts += 1

            results.append(result_entry)

            if self.storage:
                self.storage.log_edit(
                    instruction=instruction,
                    proposed_operation=agent_result.model_dump() if hasattr(agent_result, "model_dump") else None,
                    validation_passed=validation_passed,
                    validation_reason=validation_reason,
                    outcome=outcome,
                    validation_enabled=not skip_validation,
                    latency_ms=latency_ms,
                    estimated_cost_usd=self._estimate_cost(self.agent.model),
                    model=self.agent.model,
                    eval_run_tag=tag,
                )

        accuracy = (real_ops_correct / real_ops_count) if real_ops_count > 0 else 0.0
        refusal_recall = (
            clarify_reject_issued_count / clarify_reject_expected_count
            if clarify_reject_expected_count > 0 else 0.0
        )
        refusal_precision = (
            clarify_reject_issued_count / (clarify_reject_issued_count + false_refusals)
            if (clarify_reject_issued_count + false_refusals) > 0 else 0.0
        )
        false_accept_rate = (
            false_accepts / clarify_reject_expected_count
            if clarify_reject_expected_count > 0 else 0.0
        )

        sorted_latencies = sorted(latencies)
        avg_latency_ms = round(sum(latencies) / len(latencies)) if latencies else 0
        p95_latency_ms = self._percentile(sorted_latencies, 95) if latencies else 0

        report = {
            "version": version,
            "tag": tag,
            "accuracy": round(accuracy, 4),
            "refusal_precision": round(refusal_precision, 4),
            "refusal_recall": round(refusal_recall, 4),
            "false_accept_rate": round(false_accept_rate, 4),
            "avg_latency_ms": avg_latency_ms,
            "p95_latency_ms": p95_latency_ms,
            "total_cost_usd": round(total_cost, 4),
            "total_instructions": len(instructions),
            "results": results,
        }

        if self.storage:
            self.storage.save_eval_run(
                tag=tag or "untagged",
                eval_set_version=version,
                validation_enabled=not skip_validation,
                total_instructions=len(instructions),
                accuracy=report["accuracy"],
                refusal_precision=report["refusal_precision"],
                refusal_recall=report["refusal_recall"],
                false_accept_rate=report["false_accept_rate"],
                avg_latency_ms=report["avg_latency_ms"],
                p95_latency_ms=report["p95_latency_ms"],
                total_cost_usd=report["total_cost_usd"],
            )

        return report

    def _default_context(self) -> PageContext:
        return PageContext(page_id="default", title="Default Page", blocks=[])

    def _result_type(self, result: AgentResult) -> str:
        if isinstance(result, Clarify):
            return "clarify"
        if isinstance(result, Reject):
            return "reject"
        return result.operation

    def _match_operation(self, result: AgentResult, expected: dict) -> bool:
        if not expected:
            return False
        if isinstance(result, (Clarify, Reject)):
            return False
        if hasattr(result, "operation") and result.operation != expected.get("type"):
            return False
        for key, value in expected.items():
            if key in ("type", "page_ref"):
                continue
            if not hasattr(result, key):
                return False
            actual = getattr(result, key)
            if isinstance(actual, str):
                if actual != value:
                    return False
            elif str(actual).lower() != str(value).lower():
                return False
        return True

    def _estimate_cost(self, model: str) -> float:
        rates = {
            "openai/gpt-4o-mini": 0.00015,
            "gpt-4o-mini": 0.00015,
            "openai/gpt-4o": 0.0025,
            "gpt-4o": 0.0025,
        }
        return rates.get(model, 0.00015)

    def _percentile(self, sorted_data: list, p: int) -> int:
        if not sorted_data:
            return 0
        k = (p / 100.0) * (len(sorted_data) - 1)
        f = math.floor(k)
        c = math.ceil(k)
        if f == c:
            return int(sorted_data[int(k)])
        return int(sorted_data[f] * (c - k) + sorted_data[c] * (k - f))

    @staticmethod
    def print_report(report: dict):
        print(f"\n{'='*50}")
        print(f"Eval Report — v{report['version']}")
        if report.get("tag"):
            print(f"Tag: {report['tag']}")
        print(f"{'='*50}")
        print(f"Total instructions:      {report['total_instructions']}")
        print(f"Accuracy:               {report['accuracy']:.4f}")
        print(f"Refusal precision:      {report['refusal_precision']:.4f}")
        print(f"Refusal recall:         {report['refusal_recall']:.4f}")
        print(f"False accept rate:      {report['false_accept_rate']:.4f}")
        print(f"Avg latency:            {report['avg_latency_ms']} ms")
        print(f"P95 latency:            {report['p95_latency_ms']} ms")
        print(f"Total cost:             ${report['total_cost_usd']:.4f}")
        print(f"{'='*50}\n")

        for r in report.get("results", []):
            ex_type = r.get("expected_type", "")
            outcome = r.get("outcome", "")
            if ex_type in ("update_property", "append_block", "update_block_text"):
                marker = "✓" if r.get("correct") else "✗"
            elif ex_type in ("clarify", "reject"):
                marker = "✓" if outcome in ("clarified", "rejected") else "✗"
            else:
                marker = "?"
            print(
                f"  [{marker}] {r['id']:20s} | "
                f"exp={ex_type:20s} | act={r['actual_type']:20s} | "
                f"out={outcome:10s} | {'OK' if r['validation_passed'] else 'BLOCKED'}"
            )
            if r.get("validation_reason"):
                print(f"       └─ {r['validation_reason']}")

