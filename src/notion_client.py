import os
from typing import Optional, Any

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

    def query_data_source(self, data_source_id: str) -> list[dict]:
        results = []
        cursor = None
        while True:
            body = {} if cursor is None else {"start_cursor": cursor}
            data = self._post(f"/data_sources/{data_source_id}/query", body)
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

    def get_data_source(self, data_source_id: str) -> dict:
        return self._get(f"/data_sources/{data_source_id}")

    def extract_page_context(self, page_id: str) -> PageContext:
        page = self._resolve_page(page_id)
        title = self._extract_page_title(page)
        page_type = "database" if page.get("object") == "database" else "page"

        properties: dict[str, Any] = {}
        entries: list[BlockInfo] = []

        if page_type == "database":
            data_sources = page.get("data_sources", [])
            if data_sources:
                ds_id = data_sources[0].get("id", "")

                ds_info = self.get_data_source(ds_id)
                ds_props = ds_info.get("properties", {})
                for name, prop in ds_props.items():
                    prop_type = prop.get("type", "")
                    options = []
                    if prop_type == "select":
                        options = [o["name"] for o in prop.get("select", {}).get("options", [])]
                    if prop_type == "status":
                        options = [o["name"] for o in prop.get("status", {}).get("options", [])]
                    properties[name] = {"type": prop_type, "options": options}

                if ds_id:
                    db_entries = self.query_data_source(ds_id)
                    for entry in db_entries:
                        entry_id = entry["id"]
                        entry_title = self._extract_entry_title(entry)
                        entry_props = {k: self._extract_property_value(v) for k, v in entry.get("properties", {}).items()}
                        entries.append(BlockInfo(
                            id=entry_id,
                            type="child_database_entry",
                            text=f"{entry_title} | {entry_props}",
                            children=[],
                        ))
        else:
            blocks = self.extract_block_tree(page_id)

        return PageContext(
            page_id=page_id,
            title=title,
            page_type=page_type,
            properties=properties,
            blocks=entries if page_type == "database" else blocks,
        )

    def _resolve_page(self, page_id: str) -> dict:
        try:
            return self.get_page(page_id)
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 400:
                try:
                    return self.get_database(page_id)
                except httpx.HTTPStatusError:
                    pass
            raise

    def _extract_entry_title(self, entry: dict) -> str:
        props = entry.get("properties", {})
        for prop in props.values():
            prop_type = prop.get("type", "")
            if prop_type == "title":
                titles = prop.get("title", [])
                return "".join(t.get("plain_text", "") for t in titles if t.get("plain_text"))
        return "Untitled entry"

    def _extract_property_value(self, prop: dict) -> str:
        prop_type = prop.get("type", "")
        if prop_type == "select":
            s = prop.get("select")
            return s["name"] if s else ""
        if prop_type == "status":
            s = prop.get("status")
            return s["name"] if s else ""
        if prop_type == "date":
            d = prop.get("date")
            return d.get("start", "") if d else ""
        if prop_type == "rich_text":
            texts = prop.get("rich_text", [])
            return "".join(t.get("plain_text", "") for t in texts if t.get("plain_text"))
        if prop_type == "title":
            titles = prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in titles if t.get("plain_text"))
        if prop_type == "checkbox":
            return str(prop.get("checkbox", False))
        if prop_type == "number":
            return str(prop.get("number", ""))
        if prop_type == "email":
            return prop.get("email", "")
        return ""

    def extract_block_tree(self, block_id: str) -> list[BlockInfo]:
        blocks = self.get_block_children(block_id)
        return self._build_block_tree(blocks)

    def _build_block_tree(self, blocks: list[dict]) -> list[BlockInfo]:
        result = []
        for block in blocks:
            block_id = block["id"]
            block_type = block["type"]
            text = self._extract_text(block)
            children = []
            if block.get("has_children"):
                try:
                    child_blocks = self.get_block_children(block_id)
                    children = self._build_block_tree(child_blocks)
                except Exception:
                    pass
            result.append(BlockInfo(id=block_id, type=block_type, text=text, children=children))
        return result

    def _extract_text(self, block: dict) -> str:
        block_type = block.get("type", "")
        type_map = block.get(block_type, {})
        if block_type == "child_page":
            return type_map.get("title", "")
        rich_text = type_map.get("rich_text", [])
        return "".join(t.get("plain_text", "") for t in rich_text if t.get("plain_text"))

    def _extract_page_title(self, page: dict) -> str:
        if page.get("object") == "database":
            titles = page.get("title", [])
            return "".join(t.get("plain_text", "") for t in titles if t.get("plain_text"))
        properties = page.get("properties", {})
        title_prop = properties.get("title", properties.get("Name", {}))
        if isinstance(title_prop, dict):
            titles = title_prop.get("title", [])
            return "".join(t.get("plain_text", "") for t in titles if t.get("plain_text"))
        return "Untitled"

    def build_property_value(self, prop_type: str, value: str) -> Optional[dict]:
        if not value.strip():
            return None
        if prop_type == "select":
            return {"select": {"name": value}}
        elif prop_type == "status":
            return {"status": {"name": value}}
        elif prop_type == "date":
            return {"date": {"start": value}}
        elif prop_type == "checkbox":
            return {"checkbox": value.lower() in ("true", "yes", "done")}
        elif prop_type == "number":
            try:
                return {"number": float(value)}
            except ValueError:
                return {"number": 0}
        elif prop_type == "rich_text":
        return {"rich_text": [{"text": {"content": value}}]}


class MockNotionClient:
    def __init__(self, pages: Optional[dict] = None):
        self.pages = pages or {}

    def register_page(self, page_id: str, page_context: PageContext):
        self.pages[page_id] = page_context

    def extract_page_context(self, page_id: str) -> PageContext:
        if page_id not in self.pages:
            raise ValueError(f"Unknown page: {page_id}")
        return self.pages[page_id]

    def extract_block_tree(self, block_id: str) -> list[BlockInfo]:
        for page_id, ctx in self.pages.items():
            for block in ctx.blocks:
                found = self._find_block(block, block_id)
                if found:
                    return found.children if found.id == block_id else [found]
            for block in ctx.blocks:
                found = self._find_block(block, block_id)
                if found is not None:
                    subtree = self._collect_subtree(found)
                    return subtree
        raise ValueError(f"Unknown block: {block_id}")

    def _find_block(self, block: BlockInfo, block_id: str) -> Optional[BlockInfo]:
        if block.id == block_id:
            return block
        for child in block.children:
            found = self._find_block(child, block_id)
            if found:
                return found
        return None

    def _collect_subtree(self, block: BlockInfo) -> list[BlockInfo]:
        result = []
        for child in block.children:
            result.append(child)
            result.extend(self._collect_subtree(child))
        return result

    def update_block(self, block_id: str, properties: dict) -> dict:
        return {"id": block_id, "object": "block", "type": "updated"}

    def append_block_children(self, block_id: str, children: list[dict]) -> dict:
        return {"id": block_id, "object": "block", "type": "appended", "children": children}

    def update_page_property(self, page_id: str, properties: dict) -> dict:
        return {"id": page_id, "object": "page", "properties": properties}
        elif prop_type == "title":
            return {"title": [{"text": {"content": value}}]}
        elif prop_type == "url":
            return {"url": value}
        elif prop_type == "email":
            return {"email": value}
        elif prop_type == "phone_number":
            return {"phone_number": value}
        return {"rich_text": [{"text": {"content": value}}]}
