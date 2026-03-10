import base64
import json
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from utils import get_safe_env

DRIVE_API_KEY = get_safe_env("DRIVE_API_KEY")
QUEUE_URL = get_safe_env("WEBHOOK_EVENTS_QUEUE_URL")
SQS_CLIENT = boto3.client("sqs")


def _response(status_code: int, body: dict):
    """
    Builds an HTTP-style response dictionary with a JSON-serialized body and Content-Type header.
    
    Parameters:
        status_code (int): HTTP status code to set in the response.
        body (dict): Payload to serialize as the JSON response body.
    
    Returns:
        dict: A mapping with keys "statusCode", "headers", and "body" where "body" is the JSON string of `body`.
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve a header value from an event, checking both the provided header name and its lowercase form.
    
    Parameters:
        event (dict): Event object that may contain a "headers" mapping.
        header_name (str): Header name to look up (e.g., "Authorization" or "x-api-key").
    
    Returns:
        str | None: The header value if found, `None` if the header is not present.
    """
    headers = event.get("headers") or {}
    value = headers.get(header_name)
    if value is not None:
        return value
    return headers.get(header_name.lower())


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Check whether the incoming event contains an API key that matches the expected key.
    
    Parameters:
        event (dict): HTTP event object containing request headers; headers may be under original or lowercase keys.
        expected_key (str): The API key to validate against.
    
    Returns:
        True if a supplied API key from the `x-drive-api-key`, `x-api-key`, or `authorization` header (with a leading "Bearer " prefix removed if present) exactly equals `expected_key`, False otherwise.
    """
    supplied_key = (
        _get_header(event, "x-drive-api-key")
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
    Parse the request body from a Lambda event and return it as a dictionary.
    
    If the event body is already a dict, it is returned unchanged. If the body is a string and the event flag `isBase64Encoded` is truthy, the string is decoded from base64 before parsing. Empty or missing bodies produce an empty dict.
    
    Parameters:
        event (dict): Lambda event containing a `body` field and optional `isBase64Encoded` flag.
    
    Returns:
        dict: The parsed JSON object from the request body, or an empty dict if the body is missing or empty.
    
    Raises:
        ValueError: If `body` is present but is neither a dict nor a string.
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
    Normalize an incoming Drive webhook payload into the internal event schema v1.0.
    
    Parameters:
        payload (dict): Parsed JSON payload from the webhook containing change and object fields.
        raw_event (dict): Original HTTP event dictionary (used to capture headers and original payload).
    
    Returns:
        dict: A normalized event dictionary with keys: schema_version, event_id, emitted_at, tenant,
        source, scope, change, actor, delivery_key, and source_details. Fields use sensible defaults
        when specific values are absent (e.g., IDs default to "unknown", timestamps default to the
        current UTC time).
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
    source_type = "gdrive"
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
            or payload.get("folder_id")
            or "unknown",
        },
        "change": {
            "operation": payload.get("operation")
            or payload.get("event_type")
            or "unknown",
            "object_type": payload.get("object_type") or payload.get("item_type") or "file",
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
    Enqueue a JSON-serializable dictionary as a message to the configured SQS queue.
    
    Parameters:
        message (dict): The payload to send; it will be JSON-serialized and delivered to the queue specified by the module's QUEUE_URL.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, ctx):
    """
    Process an incoming Drive webhook: authenticate, parse and normalize the payload, enqueue the normalized event, and return an HTTP response.
    
    Parameters:
        event (dict): Lambda event payload containing request headers and body (may be base64-encoded).
        ctx: Lambda context object (unused).
    
    Returns:
        dict: HTTP response object with keys `statusCode`, `headers`, and `body` (JSON string). On success the body contains `{"status": "accepted", "event_id": <uuid>}`; on failure the body contains `{"error": "<message>"}` and an appropriate status code (401, 400, or 500).
    """
    if not _is_authorized(event or {}, DRIVE_API_KEY):
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
