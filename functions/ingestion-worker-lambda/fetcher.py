import os
import logging
import requests

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
DRIVE_API_KEY = os.environ.get("DRIVE_API_KEY", "")
WIKI_API_KEY = os.environ.get("WIKI_API_KEY", "")
WIKI_BASE_URL = os.environ.get("WIKI_BASE_URL", "")


def fetch_notion_blocks(page_id: str) -> list:
    url = f"https://api.notion.com/v1/blocks/{page_id}/children"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
    }
    results = []
    cursor = None
    while True:
        params = {"page_size": 100}
        if cursor:
            params["start_cursor"] = cursor
        resp = requests.get(url, headers=headers, params=params, timeout=10)
        resp.raise_for_status()
        data = resp.json()
        results.extend(data.get("results", []))
        if not data.get("has_more"):
            break
        cursor = data.get("next_cursor")
    return results


def fetch_drive_export(file_id: str) -> bytes:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
    params = {"mimeType": "application/pdf", "key": DRIVE_API_KEY}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.content


def fetch_bookstack_markdown(page_id: str) -> str:
    base = WIKI_BASE_URL.rstrip("/")
    url = f"{base}/api/pages/{page_id}/export/markdown"
    headers = {"Authorization": f"Token {WIKI_API_KEY}"}
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    return resp.text
