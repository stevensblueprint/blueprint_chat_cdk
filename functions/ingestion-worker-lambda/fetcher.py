import os
import logging
import requests

logger = logging.getLogger(__name__)

NOTION_API_KEY = os.environ.get("NOTION_API_KEY", "")
DRIVE_API_KEY = os.environ.get("DRIVE_API_KEY", "")
WIKI_API_KEY = os.environ.get("WIKI_API_KEY", "")
WIKI_BASE_URL = os.environ.get("WIKI_BASE_URL", "")

# Google Workspace MIME types → (export_mime, file_extension)
_GDRIVE_EXPORT_MAP = {
    "application/vnd.google-apps.document": ("text/plain", ".txt"),
    "application/vnd.google-apps.spreadsheet": ("text/csv", ".csv"),
    "application/vnd.google-apps.presentation": ("text/plain", ".txt"),
}

# Native (non-Google) MIME types that are KB-compatible as-is
_GDRIVE_NATIVE_EXTS = {
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
    "text/plain": ".txt",
    "text/html": ".html",
    "text/csv": ".csv",
    "text/markdown": ".md",
}


def fetch_notion_page_title(page_id: str) -> str:
    url = f"https://api.notion.com/v1/pages/{page_id}"
    headers = {
        "Authorization": f"Bearer {NOTION_API_KEY}",
        "Notion-Version": "2022-06-28",
    }
    resp = requests.get(url, headers=headers, timeout=10)
    resp.raise_for_status()
    data = resp.json()
    for prop in data.get("properties", {}).values():
        if prop.get("type") == "title":
            return "".join(rt.get("plain_text", "") for rt in prop.get("title", []))
    return ""


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


def fetch_drive_metadata(file_id: str) -> dict:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"fields": "mimeType,name", "key": DRIVE_API_KEY}
    resp = requests.get(url, params=params, timeout=10)
    resp.raise_for_status()
    return resp.json()


def fetch_drive_content(file_id: str) -> tuple[bytes, str, str]:
    """
    Returns (content_bytes, file_extension, content_type).

    Google Workspace files are exported to a KB-compatible format.
    Native files (PDF, DOCX, etc.) are downloaded as-is.
    """
    metadata = fetch_drive_metadata(file_id)
    mime_type = metadata.get("mimeType", "")

    if mime_type in _GDRIVE_EXPORT_MAP:
        export_mime, ext = _GDRIVE_EXPORT_MAP[mime_type]
        content = _export_drive_file(file_id, export_mime)
        return content, ext, export_mime

    if mime_type in _GDRIVE_NATIVE_EXTS:
        ext = _GDRIVE_NATIVE_EXTS[mime_type]
        content = _download_drive_file(file_id)
        return content, ext, mime_type

    # Unknown type — best-effort plain text export
    logger.warning("Unknown Drive MIME type %s for file %s, exporting as text/plain", mime_type, file_id)
    content = _export_drive_file(file_id, "text/plain")
    return content, ".txt", "text/plain"


def _export_drive_file(file_id: str, export_mime: str) -> bytes:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}/export"
    params = {"mimeType": export_mime, "key": DRIVE_API_KEY}
    resp = requests.get(url, params=params, timeout=30)
    resp.raise_for_status()
    return resp.content


def _download_drive_file(file_id: str) -> bytes:
    url = f"https://www.googleapis.com/drive/v3/files/{file_id}"
    params = {"alt": "media", "key": DRIVE_API_KEY}
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
