import base64
import binascii
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
    Builds an HTTP-style JSON response dictionary for API Gateway-compatible lambdas.
    
    Parameters:
        status_code (int): HTTP status code to return.
        body (dict): Data to serialize as the JSON response body.
    
    Returns:
        dict: A mapping with keys `statusCode`, `headers` (Content-Type set to application/json), and `body` (a JSON-encoded string).
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve a header value from an event's headers using case-insensitive name matching.
    
    Parameters:
        event (dict): The event object containing a "headers" mapping.
        header_name (str): The header name to look up (case-insensitive).
    
    Returns:
        str | None: The header value if found, `None` otherwise.
    """
    headers = event.get("headers") or {}
    target = header_name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Determine whether the incoming event supplies the expected API key.
    
    Parameters:
        event (dict): HTTP-style event containing request headers.
        expected_key (str): The API key value to validate against.
    
    Returns:
        `true` if the event contains a matching API key, `false` otherwise.
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
    Parse and validate the HTTP-like event body and return it as a dict.
    
    Parameters:
        event (dict): The incoming event object; may contain "body" (str or dict) and "isBase64Encoded" (truthy) keys.
    
    Returns:
        dict: The parsed JSON object. Returns an empty dict when there is no body or the body is empty.
    
    Raises:
        ValueError: If the body has an unsupported type or cannot be decoded/parsed as a JSON object.
    """
    body = event.get("body")
    if body is None:
        return {}
    if isinstance(body, dict):
        return body
    if not isinstance(body, str):
        raise ValueError("Invalid body type")

    try:
        if event.get("isBase64Encoded"):
            body = base64.b64decode(body, validate=True).decode("utf-8")
        if not body.strip():
            return {}
        parsed = json.loads(body)
        if not isinstance(parsed, dict):
            raise ValueError("Invalid request payload")
        return parsed
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid request payload") from exc


def _redact_headers(headers: dict | None) -> dict:
    """
    Redact sensitive header values and return a copy suitable for logging or forwarding.
    
    Parameters:
        headers (dict | None): Mapping of HTTP header names to values; may be None.
    
    Returns:
        dict: A new headers mapping where values for common sensitive header names (for example
        Authorization, API keys, Cookie, Set-Cookie and similar) are replaced with "[REDACTED]".
        Non-string header names are omitted and original header name casing is preserved for keys kept.
    """
    if not isinstance(headers, dict):
        return {}

    # Redact auth/session secrets before forwarding raw event metadata.
    sensitive_header_names = {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-discord-api-key",
        "x-drive-api-key",
        "x-notion-api-key",
        "x-wiki-api-key",
        "cookie",
        "set-cookie",
    }

    redacted_headers = {}
    for key, value in headers.items():
        if not isinstance(key, str):
            continue
        if key.lower() in sensitive_header_names:
            redacted_headers[key] = "[REDACTED]"
        else:
            redacted_headers[key] = value
    return redacted_headers


def _normalize_event(payload: dict, raw_event: dict) -> dict:
    """
    Produce a normalized event dictionary containing standardized metadata and source details.
    
    Parameters:
        payload (dict): Parsed webhook payload with fields from the source system. Missing identifiers and fields are populated with sensible defaults (e.g., "unknown") where applicable.
        raw_event (dict): Original raw event/request object; headers from this object are included under `source_details.raw_event.headers` after redaction.
    
    Returns:
        dict: A standardized event structure including keys: `schema_version`, `event_id`, `emitted_at`, `tenant`, `source`, `scope`, `change`, `actor`, `delivery_key`, and `source_details`. Boolean change flags are coerced to `bool`, timestamps and IDs are generated when absent, and raw headers are redacted.
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
                "headers": _redact_headers(raw_event.get("headers")),
                "payload": payload,
            }
        },
    }


def _enqueue(message: dict):
    """
    Enqueue a JSON-serializable message to the configured SQS queue.
    
    Parameters:
        message (dict): The payload to send; will be JSON-serialized and delivered to the service queue configured by the environment.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, _ctx):
    """
    Handle an incoming webhook: authorize the request, parse and normalize the payload, enqueue the normalized event, and return an HTTP-style response.
    
    Parameters:
        event (dict): The Lambda event object (headers and body expected); used for authorization, payload extraction, and raw event metadata.
    
    Returns:
        dict: An HTTP-like response with keys `statusCode`, `headers`, and `body` (JSON-serializable). On success the body contains `status: "accepted"` and `event_id`; on failure the body contains an `error` message and an appropriate status code.
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
