import base64
import binascii
import json
import uuid
from datetime import datetime, timezone

import boto3
from botocore.exceptions import BotoCoreError, ClientError
from utils import get_safe_env

NOTION_KEY = get_safe_env("NOTION_API_KEY")
QUEUE_URL = get_safe_env("WEBHOOK_EVENTS_QUEUE_URL")
SQS_CLIENT = boto3.client("sqs")


def _response(status_code: int, body: dict):
    """
    Builds an API Gateway-style HTTP response dictionary.
    
    Parameters:
        status_code (int): HTTP status code to return.
        body (dict): Response payload which will be JSON-encoded.
    
    Returns:
        dict: A dictionary with keys "statusCode" (int), "headers" (includes "Content-Type": "application/json"), and "body" (the JSON-encoded string of `body`).
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve a header value from an event in a case-insensitive manner.
    
    Parameters:
        event (dict): Event dictionary that may contain a "headers" mapping.
        header_name (str): Name of the header to look up (case-insensitive).
    
    Returns:
        str | None: The header value if found, `None` if the header is not present.
    """
    headers = event.get("headers") or {}
    target = header_name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Check whether the incoming event supplies the expected API key.
    
    Checks the `x-notion-api-key`, `x-api-key`, and `authorization` headers (case-insensitive). If an authorization value starts with "Bearer ", the bearer prefix is stripped before comparison.
    
    Parameters:
        event (dict): HTTP-style event dictionary containing request headers.
        expected_key (str): The API key expected to authenticate the request.
    
    Returns:
        bool: `True` if a supplied key matches `expected_key`, `False` otherwise.
    """
    supplied_key = (
        _get_header(event, "x-notion-api-key")
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
    Parse and validate the HTTP request body from an API Gateway-style event.
    
    Parses event["body"] and returns a dictionary representation. If the body is None or empty (after optional base64 decoding and whitespace stripping) an empty dict is returned. If the body is already a dict it is returned unchanged. If the body is a JSON string, it is parsed and must produce a dict.
    
    Parameters:
        event (dict): API Gateway-style event containing keys like "body" and optional "isBase64Encoded".
    
    Returns:
        dict: The parsed JSON object from the request body, or an empty dict when the body is absent or empty.
    
    Raises:
        ValueError: If the body is of an unexpected type or cannot be decoded/parsed as a JSON object.
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
    Return a copy of the provided HTTP headers with sensitive authentication and session header values redacted.
    
    If `headers` is not a dict, an empty dict is returned. Non-string header keys are ignored. Header names matched case-insensitively against a built-in sensitive set (e.g., authorization, x-api-key, cookie) have their values replaced with the literal "[REDACTED]"; other header values are preserved.
     
    Parameters:
        headers (dict | None): Mapping of header names to values; may be None.
    
    Returns:
        dict: A headers mapping where sensitive header values are replaced with "[REDACTED]".
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
    Normalize a webhook payload and raw event into the standardized event envelope used by the system.
    
    Parameters:
        payload (dict): Parsed JSON payload from the source webhook containing event fields (e.g., ids, timestamps, operation, actor, and change flags).
        raw_event (dict): Original incoming event object (typically the Lambda event); only its headers are used and will be redacted.
    
    Returns:
        dict: A normalized event dictionary with keys:
            - schema_version: envelope schema version.
            - event_id: generated UUID for this envelope.
            - emitted_at: ISO 8601 UTC timestamp when the envelope was created.
            - tenant: mapping with tenant_id and workspace_id.
            - source: mapping with source_type, connection_id, and account_id.
            - scope: mapping with scope_type and scope_id.
            - change: mapping describing the change (operation, object_type, object_id, occurred_at, content_changed, metadata_changed, permissions_changed, parent/path fields, and source_version).
            - actor: actor identifier.
            - delivery_key: composed string used for idempotency/routing.
            - source_details: original payload and redacted headers under raw_event.
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
    source_type = "notion"
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
            "scope_type": payload.get("scope_type", "page"),
            "scope_id": payload.get("scope_id")
            or payload.get("container_id")
            or payload.get("page_id")
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
    Send a JSON-encoded message to the configured SQS queue.
    
    Parameters:
        message (dict): Payload to send; will be JSON-encoded and used as the SQS MessageBody.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, _ctx):
    """
    Process an incoming webhook event: authorize the request, parse and normalize the payload, enqueue the normalized event, and return an HTTP-style response.
    
    Parameters:
        event (dict): Lambda event payload (headers and body expected) from API Gateway or equivalent.
    
    Returns:
        dict: HTTP-style response with keys:
            - statusCode (int): HTTP status code (e.g., 202, 400, 401, 500).
            - headers (dict): Response headers (includes "Content-Type": "application/json").
            - body (str): JSON-encoded object containing a status or error message and, on success, "event_id".
    """
    if not _is_authorized(event or {}, NOTION_KEY):
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
