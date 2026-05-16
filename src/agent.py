import json
import os
from typing import Optional

import openai

from .models import (
    AgentResult, Clarify, Reject,
    UpdateProperty, AppendBlock, UpdateBlockText, ArchiveBlock,
    PageContext, BlockInfo,
)

SYSTEM_PROMPT = """You are an AI assistant that translates natural language instructions into structured block-level operations on a Notion page.

Available operations:

1. `update_property` — Update a property (status, owner, due date, etc.) on a database entry page.
2. `append_block` — Append a new block under a parent block on a page.
3. `update_block_text` — Update the text of an existing block.
4. `archive_block` — Archive (delete) a block. Only use when the instruction explicitly says to delete, remove, archive, or trash.
5. `clarify` — Ask for clarification when the instruction is ambiguous, references something that doesn't exist, or cannot be safely executed.
6. `reject` — Reject the instruction if it is invalid, unsafe, malformed, or contains multiple distinct operations.

Rules:
- If the instruction is ambiguous (e.g., two blocks match a description, or what to do is unclear), return clarify with a clear reason.
- If the instruction references something that does not exist in the page context, return clarify.
- If the instruction contains more than one distinct edit operation (e.g. "do X and Y"), return reject with reason "multiple operations detected, please submit one edit at a time".
- If the instruction implies deletion, archiving, or removal without explicit keywords (delete, remove, archive), return reject.
- Pick the correct operation and fill in all fields accurately using the page context below.
- Block IDs are provided in the page context. Use the exact ID values from the context.
- For update_property, the page_ref should be the database entry's ID.
- For append_block, page_ref is the page ID and parent_block_id is the block to append under.
- For update_block_text, page_ref is the page ID and block_id is the block to update."""

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "update_property",
            "description": "Update a property (e.g. status, owner, due date) on a database entry page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_ref": {"type": "string", "description": "The page or entry ID to update"},
                    "property_name": {"type": "string", "description": "The name of the property to update"},
                    "property_value": {"type": "string", "description": "The new value for the property"},
                },
                "required": ["page_ref", "property_name", "property_value"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "append_block",
            "description": "Append a new block as a child of a parent block on a page.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_ref": {"type": "string", "description": "The page ID"},
                    "parent_block_id": {"type": "string", "description": "The ID of the parent block to append under"},
                    "block_type": {
                        "type": "string",
                        "enum": [
                            "paragraph", "to_do", "callout",
                            "bulleted_list_item", "numbered_list_item",
                            "heading_1", "heading_2", "heading_3",
                        ],
                    },
                    "content": {"type": "string", "description": "The text content of the block"},
                },
                "required": ["page_ref", "parent_block_id", "block_type", "content"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "update_block_text",
            "description": "Update the text content of an existing block.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_ref": {"type": "string", "description": "The page ID"},
                    "block_id": {"type": "string", "description": "The ID of the block to update"},
                    "new_text": {"type": "string", "description": "The new text content"},
                },
                "required": ["page_ref", "block_id", "new_text"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "archive_block",
            "description": "Archive (delete) a block. Only use when the instruction explicitly says to delete, remove, archive, or trash content.",
            "parameters": {
                "type": "object",
                "properties": {
                    "page_ref": {"type": "string", "description": "The page ID"},
                    "block_id": {"type": "string", "description": "The ID of the block to archive"},
                },
                "required": ["page_ref", "block_id"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "clarify",
            "description": "Request clarification when an instruction is ambiguous or references something that does not exist.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why clarification is needed"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "reject",
            "description": "Reject an instruction that is invalid, unsafe, malformed, or contains multiple distinct operations.",
            "parameters": {
                "type": "object",
                "properties": {
                    "reason": {"type": "string", "description": "Why the instruction is being rejected"},
                },
                "required": ["reason"],
                "additionalProperties": False,
            },
        },
    },
]


class Agent:
    def __init__(self, model: Optional[str] = None):
        self.model = model or os.environ.get("LLM_MODEL", "openai/gpt-4o-mini")
        api_key = os.environ.get("OPENROUTER_API_KEY", "")
        base_url = os.environ.get("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")
        self.client = openai.OpenAI(
            api_key=api_key,
            base_url=base_url,
            default_headers={
                "HTTP-Referer": "https://github.com/anomalyco/blockguard",
                "X-Title": "blockguard",
            },
        )

    def process(
        self,
        instruction: str,
        page_context: PageContext,
        retry_error: Optional[str] = None,
    ) -> AgentResult:
        content = f"Instruction: {instruction}\n\nPage context:\n{self._build_page_summary(page_context)}"
        if retry_error:
            content += f"\n\nPrevious attempt failed validation with error: {retry_error}\nPlease try a different interpretation."

        messages = [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": content},
        ]

        response = self.client.chat.completions.create(
            model=self.model,
            messages=messages,
            tools=TOOLS,
            tool_choice="auto",
            max_tokens=1024,
        )

        msg = response.choices[0].message

        if msg.tool_calls:
            tool_call = msg.tool_calls[0]
            name = tool_call.function.name
            args = json.loads(tool_call.function.arguments)

            if name == "clarify":
                return Clarify(reason=args.get("reason", ""))
            elif name == "reject":
                return Reject(reason=args.get("reason", ""))
            elif name == "update_property":
                return UpdateProperty(**args)
            elif name == "append_block":
                return AppendBlock(**args)
            elif name == "update_block_text":
                return UpdateBlockText(**args)
            elif name == "archive_block":
                return ArchiveBlock(**args)

            raise ValueError(f"Unknown tool: {name}")

        text = msg.content or ""
        if "clarify" in text.lower():
            return Clarify(reason="The model returned a text response instead of a tool call.")
        return Reject(reason="The model returned a text response instead of a tool call.")

    def _build_page_summary(self, page_context: PageContext) -> str:
        lines = [f"Page ID: {page_context.page_id}"]
        lines.append(f"Title: {page_context.title}")
        lines.append(f"Type: {page_context.page_type}")

        if page_context.properties:
            lines.append("")
            lines.append("Database properties:")
            for name, prop in page_context.properties.items():
                if isinstance(prop, dict):
                    prop_type = prop.get("type", "unknown")
                    options = prop.get("options", [])
                    if options:
                        lines.append(f"  - {name} ({prop_type}): {options}")
                    else:
                        lines.append(f"  - {name} ({prop_type})")
                else:
                    lines.append(f"  - {name} ({prop})")

        if page_context.page_type == "database":
            lines.append("")
            lines.append("Entries in this database (use the entry's ID as page_ref for update_property):")
            for block in page_context.blocks:
                self._format_block(block, lines, 0)
        elif page_context.blocks:
            lines.append("")
            lines.append("Block tree:")
            for block in page_context.blocks:
                self._format_block(block, lines, 0)

        return "\n".join(lines)

    def _format_block(self, block: BlockInfo, lines: list[str], depth: int):
        prefix = "  " * depth
        text_preview = block.text[:120] if block.text else "(no text)"
        lines.append(f"{prefix}[{block.id}] ({block.type}) {text_preview}")
        for child in block.children:
            self._format_block(child, lines, depth + 1)
