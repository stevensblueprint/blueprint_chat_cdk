import base64
import json
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from utils import get_safe_env

WIKI_API_KEY = get_safe_env("WIKI_API_KEY")
QUEUE_URL = get_safe_env("WEBHOOK_EVENTS_QUEUE_URL")
SQS_CLIENT = boto3.client("sqs")


def _response(status_code: int, body: dict):
    """
    Build an HTTP response dictionary with a JSON body and appropriate headers.
    
    Parameters:
        status_code (int): HTTP status code to return.
        body (dict): JSON-serializable object to include as the response body.
    
    Returns:
        dict: A response mapping with keys "statusCode" (int), "headers" (contains "Content-Type": "application/json"), and "body" (the JSON-serialized string of `body`).
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve an HTTP header value from the given event's "headers" using a case-insensitive lookup.
    
    Parameters:
        event (dict): The event object expected to contain a "headers" mapping.
        header_name (str): The header name to look up (case-insensitive).
    
    Returns:
        str | None: The header value if present, otherwise None.
    """
    headers = event.get("headers") or {}
    value = headers.get(header_name)
    if value is not None:
        return value
    return headers.get(header_name.lower())


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Determine whether the event contains an API key that exactly matches the provided expected_key.
    
    Checks the headers (in this order) "x-wiki-api-key", "x-api-key", and "authorization" for a supplied key. If the value from the authorization header starts with the case-insensitive prefix "Bearer ", that prefix is removed before comparison.
    
    Parameters:
        event (dict): Incoming request event containing a "headers" mapping.
        expected_key (str): The API key to validate against.
    
    Returns:
        True if a supplied key is present (after optional "Bearer " stripping) and exactly equals expected_key, False otherwise.
    """
    supplied_key = (
        _get_header(event, "x-wiki-api-key")
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
    Parse and decode the HTTP request body from an API Gateway-style event into a Python dict.
    
    Parameters:
        event (dict): Incoming event expected to contain a "body" field and optional "isBase64Encoded" boolean. The "body" may be a dict, a JSON string, or a base64-encoded JSON string when "isBase64Encoded" is true.
    
    Returns:
        dict: The parsed JSON object, or an empty dict when the body is missing or contains only whitespace.
    
    Raises:
        ValueError: If the body exists but is not a dict or string.
        json.JSONDecodeError: If the body is a string that cannot be parsed as JSON.
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
    Normalize an incoming webhook payload and raw HTTP event into the standardized event envelope.
    
    Converts the provided payload and raw_event into a schema-compliant dictionary containing top-level keys such as `schema_version`, `event_id`, `emitted_at`, `tenant`, `source`, `scope`, `change`, `actor`, `delivery_key`, and `source_details.raw_event`. Fields are populated from common payload keys with sensible defaults (e.g., "unknown" or current timestamp) when values are missing.
    
    Parameters:
        payload (dict): Parsed webhook payload from the provider; used to populate tenant, source, scope, change, actor, and versioning fields.
        raw_event (dict): Original HTTP event (headers, raw body, and metadata); included under `source_details.raw_event.headers`.
    
    Returns:
        dict: A normalized event envelope ready for downstream processing and enqueueing.
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
    source_type = "bookstack"
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
            "scope_type": payload.get("scope_type", "book"),
            "scope_id": payload.get("scope_id")
            or payload.get("container_id")
            or payload.get("book_id")
            or "unknown",
        },
        "change": {
            "operation": payload.get("operation")
            or payload.get("event_type")
            or "unknown",
            "object_type": payload.get("object_type") or payload.get("item_type") or "page",
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
    Enqueue a message to the configured SQS queue.
    
    Parameters:
        message (dict): The payload to send to SQS; must be JSON-serializable.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, ctx):
    """
    Handle an incoming webhook: authenticate the request, parse and normalize the payload, enqueue the normalized event, and return an HTTP response.
    
    Parameters:
        event (dict): Lambda event object (expected to contain headers and body as provided by API Gateway).
        ctx: Lambda context object.
    
    Returns:
        dict: HTTP response dictionary with status code and JSON body. Possible responses:
            - 401 with {"error": "Unauthorized"} when authentication fails.
            - 400 with {"error": "Invalid request payload"} when the request body is missing or malformed.
            - 500 with {"error": "Failed to enqueue event"} when sending to SQS fails.
            - 202 with {"status": "accepted", "event_id": <id>} when the event is accepted and enqueued.
    """
    if not _is_authorized(event or {}, WIKI_API_KEY):
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
