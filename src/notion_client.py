import os
from typing import Optional

import httpx

from .models import BlockInfo, PageContext

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2025-09-03"


def build_notion_block(block_type: str, content: str) -> dict:
    if block_type == "to_do":
        return {
            "object": "block",
            "type": "to_do",
            "to_do": {
                "rich_text": [{"type": "text", "text": {"content": content}}],
                "checked": False,
            },
        }
    return {
        "object": "block",
        "type": block_type,
        block_type: {
            "rich_text": [{"type": "text", "text": {"content": content}}],
        },
    }


class NotionClientWrapper:
    def __init__(self, token: Optional[str] = None):
        token = token or os.environ.get("NOTION_TOKEN", "")
        self.token = token
        self._http = httpx.Client(
            base_url=NOTION_API_BASE,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
                "Notion-Version": NOTION_VERSION,
            },
        )

    def _get(self, path: str) -> dict:
        resp = self._http.get(path)
        resp.raise_for_status()
        return resp.json()

    def _patch(self, path: str, body: dict) -> dict:
        resp = self._http.patch(path, json=body)
        resp.raise_for_status()
        return resp.json()

    def _post(self, path: str, body: dict) -> dict:
        resp = self._http.post(path, json=body)
        resp.raise_for_status()
        return resp.json()

    def get_page(self, page_id: str) -> dict:
        return self._get(f"/pages/{page_id}")

    def get_database(self, database_id: str) -> dict:
        return self._get(f"/databases/{database_id}")

    def get_block_children(self, block_id: str) -> list[dict]:
        results = []
        cursor = None
        while True:
            params = "" if cursor is None else f"?start_cursor={cursor}"
            data = self._get(f"/blocks/{block_id}/children{params}")
            results.extend(data.get("results", []))
            if not data.get("has_more"):
                break
            cursor = data.get("next_cursor")
        return results

    def update_block(self, block_id: str, properties: dict) -> dict:
        return self._patch(f"/blocks/{block_id}", properties)

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        return self._patch(f"/blocks/{block_id}/children", {"children": children})

    def update_page_property(self, page_id: str, properties: dict) -> dict:
        return self._patch(f"/pages/{page_id}", {"properties": properties})

    def delete_block(self, block_id: str) -> dict:
        return self._patch(f"/blocks/{block_id}", {"archived": True})
