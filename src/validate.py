import re
from typing import Optional

from .models import (
    AgentResult, Clarify, Reject,
    UpdateProperty, AppendBlock, UpdateBlockText, ArchiveBlock,
    ValidationResult, ValidationError, PageContext, BlockInfo,
)

DESTRUCTIVE_KEYWORDS = ["delete", "remove", "archive", "trash", "destroy"]

ALLOWED_BLOCK_TYPES = {
    "paragraph", "to_do", "callout",
    "bulleted_list_item", "numbered_list_item",
    "heading_1", "heading_2", "heading_3",
}

CONJUNCTIVE_PATTERNS = [
    r"\band\b",
    r"\bthen\b",
    r"\balso\b",
    r"\bplus\b",
    r"\bas well\b",
]

ACTION_VERBS = [
    "set", "update", "change", "mark", "add", "append", "insert",
    "remove", "delete", "archive", "check", "uncheck", "complete",
    "move", "rename", "edit", "modify", "clear", "replace",
    "create", "make",
]


def check_multi_intent(instruction: str) -> ValidationResult:
    errors = []

    has_conjunction = any(
        re.search(p, instruction.lower())
        for p in CONJUNCTIVE_PATTERNS
    )

    if not has_conjunction:
        return ValidationResult(valid=True)

    clauses = _split_conjunctive_clauses(instruction)

    action_clauses = [c for c in clauses if _has_action_verb(c)]

    shared_verb_clauses = [
        c for c in clauses
        if not _has_action_verb(c) and _starts_with_noun_phrase(c)
    ]

    if len(action_clauses) + len(shared_verb_clauses) > 1:
        errors.append(ValidationError(
            field="instruction",
            message="multiple operations detected, please submit one edit at a time",
        ))
        return ValidationResult(valid=False, errors=errors)

    if len(action_clauses) > 1:
        errors.append(ValidationError(
            field="instruction",
            message="multiple operations detected, please submit one edit at a time",
        ))
        return ValidationResult(valid=False, errors=errors)

    return ValidationResult(valid=True)


def _split_conjunctive_clauses(instruction: str) -> list[str]:
    text = instruction.lower().strip()
    text = re.sub(r"[,;]+", " ", text)
    parts = re.split(r"\s+(and|then)\s+", text)
    result = []
    for part in parts:
        part = part.strip()
        if part and part not in ("and", "then"):
            result.append(part)
    return result or [text]


def _has_action_verb(clause: str) -> bool:
    words = clause.split()
    for verb in ACTION_VERBS:
        if verb in words:
            return True
    return False


def _starts_with_noun_phrase(clause: str) -> bool:
    return bool(re.match(r"^\s*(?:a|an|the)\s+", clause))


class Validator:
    def validate(
        self,
        operation: AgentResult,
        page_context: PageContext,
        instruction: str,
    ) -> ValidationResult:
        if isinstance(operation, (Clarify, Reject)):
            return ValidationResult(valid=True)

        multi_intent_result = check_multi_intent(instruction)
        if not multi_intent_result.valid:
            return multi_intent_result

        errors = []

        if isinstance(operation, UpdateProperty):
            errors.extend(self._validate_update_property(operation, page_context, instruction))
        elif isinstance(operation, AppendBlock):
            errors.extend(self._validate_append_block(operation, page_context, instruction))
        elif isinstance(operation, UpdateBlockText):
            errors.extend(self._validate_update_block_text(operation, page_context, instruction))
        elif isinstance(operation, ArchiveBlock):
            errors.extend(self._validate_archive_block(operation, page_context, instruction))

        if errors:
            return ValidationResult(valid=False, errors=errors)
        return ValidationResult(valid=True)

    def _normalize_id(self, id_str: str) -> str:
        return id_str.replace("-", "")

    def _normalize_select_value(
        self,
        property_name: str,
        property_value: str,
        page_context: PageContext,
    ) -> str:
        if property_name not in page_context.properties:
            return property_value

        prop_schema = page_context.properties[property_name]
        prop_type = prop_schema if isinstance(prop_schema, str) else prop_schema.get("type", "")

        if prop_type == "select":
            options_raw = (
                prop_schema.get("options", [])
                if isinstance(prop_schema, dict) else []
            )
            if options_raw:
                value_lower = property_value.lower().strip()
                for opt in options_raw:
                    if opt.lower().strip() == value_lower:
                        return opt

        return property_value

    def _check_destructive_action(
        self, operation: AgentResult, instruction: str
    ) -> list[ValidationError]:
        errors = []
        instruction_lower = instruction.lower()
        has_keyword = any(kw in instruction_lower for kw in DESTRUCTIVE_KEYWORDS)

        is_destructive_op = isinstance(operation, ArchiveBlock)

        if is_destructive_op and not has_keyword:
            errors.append(ValidationError(
                field="operation",
                message=(
                    "Destructive operation requires explicit keyword like "
                    "'delete', 'remove', or 'archive' in the instruction"
                ),
            ))

        return errors

    def _find_block_by_id(
        self, block_id: str, blocks: list[BlockInfo], normalized: bool = False
    ) -> Optional[BlockInfo]:
        for block in blocks:
            match = (
                (self._normalize_id(block.id) == self._normalize_id(block_id))
                if normalized else (block.id == block_id)
            )
            if match:
                return block
            found = self._find_block_by_id(block_id, block.children, normalized)
            if found:
                return found
        return None

    def _check_block_exists(
        self, block_id: str, page_context: PageContext
    ) -> Optional[ValidationError]:
        found = (
            self._find_block_by_id(block_id, page_context.blocks)
            or self._find_block_by_id(block_id, page_context.blocks, normalized=True)
        )
        if not found:
            return ValidationError(
                field="block_id",
                message=f"Block {block_id} does not exist on page '{page_context.title}'",
            )
        return None

    def _check_schema(
        self,
        property_name: str,
        property_value: str,
        page_context: PageContext,
    ) -> list[ValidationError]:
        errors = []

        if property_name not in page_context.properties:
            known = list(page_context.properties.keys())
            errors.append(ValidationError(
                field="property_name",
                message=f"Property '{property_name}' not found. Available: {known}",
            ))
            return errors

        prop_schema = page_context.properties[property_name]
        prop_type = prop_schema if isinstance(prop_schema, str) else prop_schema.get("type", "")

        if prop_type == "select":
            options_raw = (
                prop_schema.get("options", [])
                if isinstance(prop_schema, dict) else []
            )
            if options_raw:
                value_lower = property_value.lower().strip()
                opts_lower = {o.lower().strip(): o for o in options_raw}
                if value_lower not in opts_lower:
                    errors.append(ValidationError(
                        field="property_value",
                        message=(
                            f"Invalid select value '{property_value}' for property "
                            f"'{property_name}'. Allowed: {options_raw}"
                        ),
                    ))

        elif prop_type == "date":
            ISO_DATE_RE = r"^\d{4}-\d{2}-\d{2}$"
            if not re.match(ISO_DATE_RE, property_value.strip()):
                errors.append(ValidationError(
                    field="property_value",
                    message=(
                        f"Invalid date value '{property_value}' for property "
                        f"'{property_name}'. Expected ISO format YYYY-MM-DD."
                    ),
                ))

        return errors

    def _validate_update_property(
        self, op: UpdateProperty, ctx: PageContext, instruction: str
    ) -> list[ValidationError]:
        errors = []

        normalized = self._normalize_select_value(op.property_name, op.property_value, ctx)
        if normalized != op.property_value:
            op.property_value = normalized

        schema_errors = self._check_schema(op.property_name, op.property_value, ctx)
        errors.extend(schema_errors)

        return errors

    def _validate_append_block(
        self, op: AppendBlock, ctx: PageContext, instruction: str
    ) -> list[ValidationError]:
        errors = []

        block_err = self._check_block_exists(op.parent_block_id, ctx)
        if block_err:
            errors.append(block_err)

        if op.block_type not in ALLOWED_BLOCK_TYPES:
            errors.append(ValidationError(
                field="block_type",
                message=f"Invalid block type '{op.block_type}'. Allowed: {sorted(ALLOWED_BLOCK_TYPES)}",
            ))

        if not op.content.strip():
            errors.append(ValidationError(
                field="content",
                message="Block content cannot be empty",
            ))

        dest_errors = self._check_destructive_action(operation=op, instruction=instruction)
        errors.extend(dest_errors)

        return errors

    def _validate_update_block_text(
        self, op: UpdateBlockText, ctx: PageContext, instruction: str
    ) -> list[ValidationError]:
        errors = []

        block_err = self._check_block_exists(op.block_id, ctx)
        if block_err:
            errors.append(block_err)

        if not op.new_text.strip():
            errors.append(ValidationError(
                field="new_text",
                message="Block text cannot be empty",
            ))

        dest_errors = self._check_destructive_action(operation=op, instruction=instruction)
        errors.extend(dest_errors)

        return errors

    def _validate_archive_block(
        self, op: ArchiveBlock, ctx: PageContext, instruction: str
    ) -> list[ValidationError]:
        errors = []

        block_err = self._check_block_exists(op.block_id, ctx)
        if block_err:
            errors.append(block_err)

        dest_errors = self._check_destructive_action(operation=op, instruction=instruction)
        errors.extend(dest_errors)

        return errors
