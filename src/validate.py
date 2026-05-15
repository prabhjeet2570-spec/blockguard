from typing import Optional

from .models import (
    AgentResult, Clarify, Reject,
    UpdateProperty, AppendBlock, UpdateBlockText, ArchiveBlock,
    ValidationResult, ValidationError, PageContext, BlockInfo,
)


class Validator:
    def validate(
        self,
        operation: AgentResult,
        page_context: PageContext,
        instruction: str,
    ) -> ValidationResult:
        if isinstance(operation, (Clarify, Reject)):
            return ValidationResult(valid=True)

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
            import re
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
        schema_errors = self._check_schema(op.property_name, op.property_value, ctx)
        return schema_errors

    def _validate_append_block(
        self, op: AppendBlock, ctx: PageContext, instruction: str
    ) -> list[ValidationError]:
        errors = []

        block_err = self._check_block_exists(op.parent_block_id, ctx)
        if block_err:
            errors.append(block_err)

        allowed_types = {
            "paragraph", "to_do", "callout",
            "bulleted_list_item", "numbered_list_item",
            "heading_1", "heading_2", "heading_3",
        }
        if op.block_type not in allowed_types:
            errors.append(ValidationError(
                field="block_type",
                message=f"Invalid block type '{op.block_type}'. Allowed: {sorted(allowed_types)}",
            ))

        if not op.content.strip():
            errors.append(ValidationError(
                field="content",
                message="Block content cannot be empty",
            ))

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

        return errors

    def _validate_archive_block(
        self, op: ArchiveBlock, ctx: PageContext, instruction: str
    ) -> list[ValidationError]:
        errors = []

        block_err = self._check_block_exists(op.block_id, ctx)
        if block_err:
            errors.append(block_err)

        return errors
