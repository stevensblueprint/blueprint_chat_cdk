from schema import IngestionMessage
from fetcher import fetch_notion_blocks, fetch_drive_export, fetch_bookstack_markdown
from transformer import notion_blocks_to_markdown
from s3_writer import resolve_s3_key, write_document, delete_document


def route(message: IngestionMessage, bucket: str) -> dict:
    workspace_id = message.resource.workspace_id or message.source

    if message.event_type == "deleted":
        key = resolve_s3_key(message.source, workspace_id, message.resource.id)
        delete_document(bucket, key)
        return {
            "action": "delete",
            "source": message.source,
            "resource_id": message.resource.id,
            "s3_key": key,
        }

    content = _fetch_and_transform(message)
    key = resolve_s3_key(message.source, workspace_id, message.resource.id)
    write_document(bucket, key, content, message.source)

    return {
        "action": "upsert",
        "source": message.source,
        "resource_id": message.resource.id,
        "s3_key": key,
    }


def _fetch_and_transform(message: IngestionMessage) -> bytes:
    if message.source == "notion":
        blocks = fetch_notion_blocks(message.resource.id)
        return notion_blocks_to_markdown(blocks).encode("utf-8")

    if message.source == "google_drive":
        return fetch_drive_export(message.resource.id)

    if message.source == "bookstack":
        return fetch_bookstack_markdown(message.resource.id).encode("utf-8")

    raise ValueError(f"Unroutable source: {message.source}")
