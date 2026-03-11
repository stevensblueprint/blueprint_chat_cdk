import base64
import binascii
import json
import uuid
from collections.abc import Mapping
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from utils import get_safe_env

DRIVE_API_KEY = get_safe_env("DRIVE_API_KEY")
QUEUE_URL = get_safe_env("WEBHOOK_EVENTS_QUEUE_URL")
SQS_CLIENT = boto3.client("sqs")


def _response(status_code: int, body: dict):
    """
    Builds a standardized HTTP response dictionary with a JSON-encoded body.
    
    Returns:
        A dict containing `statusCode`, `headers` (Content-Type: application/json), and `body` (JSON string of `body`).
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Return the value of a header from an event's headers using case-insensitive header name lookup.
    
    Parameters:
        event (dict): Incoming event dictionary expected to contain a "headers" mapping.
        header_name (str): Header name to look up (case-insensitive).
    
    Returns:
        The header value if present, otherwise None.
    """
    headers = event.get("headers")
    if not isinstance(headers, Mapping):
        return None
    target = header_name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target and isinstance(value, str):
            return value
    return None


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Check whether the incoming event supplies the expected API key.
    
    Extracts the API key from the event headers (checks `x-drive-api-key`, `x-api-key`, then `authorization` supporting a `Bearer ` scheme) and compares it to `expected_key`.
    
    Parameters:
        event (dict): The raw request event containing headers.
        expected_key (str): The API key to validate against.
    
    Returns:
        bool: `true` if the event supplies the expected API key, `false` otherwise.
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
    Parse and validate the HTTP request body from a Lambda proxy event into a dict.
    
    This function accepts the event produced by API Gateway / Lambda proxy integration and returns a dictionary representation of the request body. If the event has no body or the body is empty after decoding, an empty dict is returned. If the event contains a string body and the event's `isBase64Encoded` flag is truthy, the body is base64-decoded before JSON parsing.
    
    Parameters:
        event (dict): Lambda proxy event containing the request body and optional `isBase64Encoded` flag.
    
    Returns:
        dict: Parsed JSON object from the request body, or an empty dict when no body is present or body is empty.
    
    Raises:
        ValueError: If the body has an unsupported type, or if the body cannot be decoded or parsed as a JSON object.
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
    Redacts sensitive header values from a headers mapping and returns a safe copy.
    
    Parameters:
        headers (dict | None): Original headers mapping; non-dict inputs produce an empty dict.
    
    Returns:
        dict: A new headers dictionary where values for sensitive header names (case-insensitive):
            authorization, proxy-authorization, x-api-key, x-discord-api-key, x-drive-api-key,
            x-notion-api-key, x-wiki-api-key, cookie, and set-cookie
        are replaced with the string "[REDACTED]". Keys that are not strings are ignored and omitted.
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
    Normalize a Drive webhook payload and its raw HTTP event into the standardized event schema version "1.0".
    
    Parameters:
        payload (dict): Parsed webhook payload received from the source; may contain identifiers, timestamps, and change details.
        raw_event (dict): Original HTTP event dictionary (used to capture and redact headers and include raw payload context).
    
    Returns:
        dict: A normalized event containing keys such as `schema_version`, `event_id`, `emitted_at`, `tenant`, `source`, `scope`, `change`, `actor`, `delivery_key`, and `source_details`. If the incoming payload lacks identifiers or timestamps, `event_id` and `emitted_at` are generated and sensible defaults (e.g., "unknown") are used for missing fields.
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
                "headers": _redact_headers(raw_event.get("headers")),
                "payload": payload,
            }
        },
    }


def _enqueue(message: dict):
    """
    Send a JSON-serializable message dictionary to the configured SQS queue.
    
    Parameters:
        message (dict): Payload to be JSON-encoded and enqueued to the SQS queue configured by QUEUE_URL.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, _ctx):
    """
    Handle an incoming webhook request: authenticate the caller, parse and normalize the payload, enqueue the normalized event to SQS, and return an HTTP-style response.
    
    Parameters:
        event (dict): Incoming request event (API Gateway/Lambda-style) containing headers and body.
    
    Returns:
        dict: HTTP-style response with keys:
            - statusCode (int): HTTP status code (e.g., 202, 400, 401, 500).
            - headers (dict): Response headers (includes Content-Type: application/json).
            - body (str): JSON-encoded object with result details. On success: contains `status: "accepted"` and `event_id`. On error: contains `error` with a short message.
    """
    if not _is_authorized(event or {}, DRIVE_API_KEY):
        return _response(401, {"error": "Unauthorized"})

    try:
        payload = _parse_body(event or {})
    except ValueError:
        return _response(400, {"error": "Invalid request payload"})

    normalized_event = _normalize_event(payload, event or {})

    try:
        _enqueue(normalized_event)
    except (BotoCoreError, ClientError):
        return _response(500, {"error": "Failed to enqueue event"})

    return _response(202, {"status": "accepted", "event_id": normalized_event["event_id"]})
