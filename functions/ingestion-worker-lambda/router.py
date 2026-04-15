from schema import IngestionMessage
from fetcher import fetch_notion_page_title, fetch_notion_blocks, fetch_drive_content, fetch_bookstack_markdown
from transformer import notion_page_to_markdown
from s3_writer import resolve_s3_key, write_document, delete_resource

_NOTION_EXT = ".md"
_NOTION_CONTENT_TYPE = "text/markdown"
_BOOKSTACK_EXT = ".md"
_BOOKSTACK_CONTENT_TYPE = "text/markdown"


def route(message: IngestionMessage, bucket: str) -> dict:
    workspace_id = message.resource.workspace_id or message.source

    if message.event_type == "deleted":
        delete_resource(bucket, message.source, workspace_id, message.resource.id)
        return {
            "action": "delete",
            "source": message.source,
            "resource_id": message.resource.id,
        }

    content, ext, content_type = _fetch_and_transform(message)
    key = resolve_s3_key(message.source, workspace_id, message.resource.id, ext)
    write_document(bucket, key, content, content_type)

    return {
        "action": "upsert",
        "source": message.source,
        "resource_id": message.resource.id,
        "s3_key": key,
    }


def _fetch_and_transform(message: IngestionMessage) -> tuple[bytes, str, str]:
    """Returns (content_bytes, file_extension, content_type)."""
    if message.source == "notion":
        title = fetch_notion_page_title(message.resource.id)
        blocks = fetch_notion_blocks(message.resource.id)
        md = notion_page_to_markdown(title, blocks)
        return md.encode("utf-8"), _NOTION_EXT, _NOTION_CONTENT_TYPE

    if message.source == "google_drive":
        content, ext, content_type = fetch_drive_content(message.resource.id)
        return content, ext, content_type

    if message.source == "bookstack":
        md = fetch_bookstack_markdown(message.resource.id)
        return md.encode("utf-8"), _BOOKSTACK_EXT, _BOOKSTACK_CONTENT_TYPE

    raise ValueError(f"Unroutable source: {message.source}")
