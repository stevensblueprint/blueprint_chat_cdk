import base64
import json
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from utils import get_safe_env

DISCORD_API_KEY = get_safe_env("DISCORD_API_KEY")
QUEUE_URL = get_safe_env("WEBHOOK_EVENTS_QUEUE_URL")
SQS_CLIENT = boto3.client("sqs")


def _response(status_code: int, body: dict):
    """
    Builds an API Gateway-compatible HTTP response object with a JSON-encoded body.
    
    Returns:
        dict: HTTP response containing:
            - "statusCode": the provided HTTP status code,
            - "headers": {"Content-Type": "application/json"},
            - "body": the JSON string encoding of the given `body` dictionary.
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve a header value from the provided event by checking the given header name and, if not found, its lowercase form.
    
    Parameters:
        event (dict): The incoming event dictionary containing a "headers" mapping.
        header_name (str): The header name to look up (original case).
    
    Returns:
        str | None: The header value if present, otherwise `None`.
    """
    headers = event.get("headers") or {}
    value = headers.get(header_name)
    if value is not None:
        return value
    return headers.get(header_name.lower())


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Validate that the incoming event contains the expected API key in supported headers.
    
    Checks the x-discord-api-key, x-api-key, and Authorization headers (supports a "Bearer " prefix) and compares the extracted key to expected_key.
    
    Returns:
        True if the supplied key matches `expected_key`, False otherwise.
    """
    supplied_key = (
        _get_header(event, "x-discord-api-key")
        or _get_header(event, "x-api-key")
        or _get_header(event, "authorization")
    )
    if not supplied_key:
        return False
    if supplied_key.lower().startswith("bearer "):
        supplied_key = supplied_key[7:].strip()
    return supplied_key == expected_key


def _parse_body(event: dict) -> dict:
    """
    Parse the incoming event's body and return it as a dictionary, handling base64 decoding and empty bodies.
    
    Parameters:
        event (dict): Lambda-style event containing a `body` key (may be a dict or a JSON string) and optional `isBase64Encoded` flag.
    
    Returns:
        dict: The parsed JSON object from the body, or an empty dict when no body is provided or the body is empty/whitespace.
    
    Raises:
        ValueError: If `body` exists but is neither a dict nor a string.
    """
    body = event.get("body")
    if body is None:
        return {}
    if isinstance(body, dict):
        return body
    if not isinstance(body, str):
        raise ValueError("Invalid body type")
    if event.get("isBase64Encoded"):
        body = base64.b64decode(body).decode("utf-8")
    if not body.strip():
        return {}
    return json.loads(body)


def _normalize_event(payload: dict, raw_event: dict) -> dict:
    """
    Builds a normalized event dictionary with a fixed schema from an incoming payload and the original raw event.
    
    Parameters:
        payload (dict): Parsed webhook payload used to derive identifiers, timestamps, tenant/source/scope fields, and change metadata.
        raw_event (dict): Original HTTP event (including headers) preserved under source_details.raw_event.
    
    Returns:
        dict: Normalized event containing keys: `schema_version`, `event_id`, `emitted_at`, `tenant`, `source`, `scope`, `change`, `actor`, `delivery_key`, and `source_details`.
    """
    now = datetime.now(timezone.utc).isoformat()
    event_id = str(uuid.uuid4())
    source_change_id = payload.get("change_id") or payload.get("id") or event_id
    object_id = (
        payload.get("object_id")
        or payload.get("source_item_id")
        or payload.get("id")
        or "unknown"
    )
    source_type = "discord"
    connection_id = payload.get("connection_id", "unknown")

    return {
        "schema_version": "1.0",
        "event_id": event_id,
        "emitted_at": now,
        "tenant": {
            "tenant_id": payload.get("tenant_id", "unknown"),
            "workspace_id": payload.get("workspace_id", "unknown"),
        },
        "source": {
            "source_type": source_type,
            "connection_id": connection_id,
            "account_id": payload.get("account_id", "unknown"),
        },
        "scope": {
            "scope_type": payload.get("scope_type", "directory"),
            "scope_id": payload.get("scope_id")
            or payload.get("container_id")
            or payload.get("channel_id")
            or "unknown",
        },
        "change": {
            "operation": payload.get("operation")
            or payload.get("event_type")
            or "unknown",
            "object_type": payload.get("object_type") or payload.get("item_type") or "message",
            "object_id": object_id,
            "occurred_at": payload.get("occurred_at") or payload.get("observed_at") or now,
            "content_changed": bool(payload.get("content_changed", False)),
            "metadata_changed": bool(payload.get("metadata_changed", False)),
            "permissions_changed": bool(payload.get("permissions_changed", False)),
            "from_parent_id": payload.get("from_parent_id"),
            "to_parent_id": payload.get("to_parent_id"),
            "from_path": payload.get("from_path"),
            "to_path": payload.get("to_path"),
            "source_version": payload.get("source_version") or payload.get("version"),
        },
        "actor": payload.get("actor", "unknown"),
        "delivery_key": f"{source_type}:{connection_id}:{source_change_id}:{object_id}",
        "source_details": {
            "raw_event": {
                "headers": raw_event.get("headers") or {},
                "payload": payload,
            }
        },
    }


def _enqueue(message: dict):
    """
    Enqueue a message by JSON-serializing it and sending it to the configured SQS queue.
    
    Parameters:
        message (dict): The payload to send; it will be JSON-encoded and delivered to QUEUE_URL as the message body.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, ctx):
    """
    Process a Discord webhook request: authorize the caller, parse and normalize the payload, enqueue the normalized event to SQS, and return an HTTP response.
    
    Parameters:
        event (dict): The Lambda event payload (headers and body) containing the incoming webhook request.
        ctx: The Lambda context object (not used by this function).
    
    Returns:
        dict: An HTTP response dictionary with keys like `statusCode`, `headers`, and JSON `body`. Possible responses:
            - 202: Accepted — contains `{"status":"accepted","event_id": <uuid>}` on success.
            - 401: Unauthorized — when the request fails authentication.
            - 400: Invalid request payload — when the body cannot be parsed as JSON.
            - 500: Failed to enqueue event — when sending the normalized event to SQS fails.
    """
    if not _is_authorized(event or {}, DISCORD_API_KEY):
        return _response(401, {"error": "Unauthorized"})

    try:
        payload = _parse_body(event or {})
    except (ValueError, json.JSONDecodeError):
        return _response(400, {"error": "Invalid request payload"})

    normalized_event = _normalize_event(payload, event or {})

    try:
        _enqueue(normalized_event)
    except (BotoCoreError, ClientError):
        return _response(500, {"error": "Failed to enqueue event"})

    return _response(202, {"status": "accepted", "event_id": normalized_event["event_id"]})
