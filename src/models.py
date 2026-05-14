from pydantic import BaseModel, Field
from typing import Annotated, Union, Optional, Literal
from enum import Enum


class OperationType(str, Enum):
    UPDATE_PROPERTY = "update_property"
    APPEND_BLOCK = "append_block"
    UPDATE_BLOCK_TEXT = "update_block_text"


class UpdateProperty(BaseModel):
    operation: Literal["update_property"] = "update_property"
    page_ref: str
    property_name: str
    property_value: str


class AppendBlock(BaseModel):
    operation: Literal["append_block"] = "append_block"
    page_ref: str
    parent_block_id: str
    block_type: Literal[
        "paragraph", "to_do", "callout",
        "bulleted_list_item", "numbered_list_item",
        "heading_1", "heading_2", "heading_3",
    ]
    content: str


class UpdateBlockText(BaseModel):
    operation: Literal["update_block_text"] = "update_block_text"
    page_ref: str
    block_id: str
    new_text: str


class ArchiveBlock(BaseModel):
    operation: Literal["archive_block"] = "archive_block"
    page_ref: str
    block_id: str


class Clarify(BaseModel):
    operation: Literal["clarify"] = "clarify"
    reason: str


class Reject(BaseModel):
    operation: Literal["reject"] = "reject"
    reason: str


AgentOperation = Annotated[
    Union[UpdateProperty, AppendBlock, UpdateBlockText, ArchiveBlock],
    Field(discriminator="operation")
]

AgentResult = Annotated[
    Union[UpdateProperty, AppendBlock, UpdateBlockText, ArchiveBlock, Clarify, Reject],
    Field(discriminator="operation")
]


class ValidationError(BaseModel):
    field: str
    message: str


class ValidationResult(BaseModel):
    valid: bool
    errors: list[ValidationError] = []


class BlockInfo(BaseModel):
    id: str
    type: str
    text: str = ""
    children: list["BlockInfo"] = []


class PageContext(BaseModel):
    page_id: str
    title: str
    page_type: str = "page"
    properties: dict = {}
    blocks: list[BlockInfo] = []
