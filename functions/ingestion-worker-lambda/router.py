from schema import IngestionMessage


def route(message: IngestionMessage) -> dict:
    if message.event_type == "deleted":
        return {
            "action": "delete",
            "source": message.source,
            "resource_id": message.resource.id,
            "content": None
        }

    if message.source == "google_drive":
        content_type = "export"
    elif message.source in ("notion", "bookstack"):
        content_type = "markdown"
    else:
        raise ValueError(f"Unroutable source: {message.source}")

    return {
        "action": "upsert",
        "source": message.source,
        "resource_id": message.resource.id,
        "content_type": content_type,
    }