import base64
import binascii
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
    Builds an HTTP-style response dictionary with a JSON-encoded body.
    
    Parameters:
        status_code (int): HTTP status code to set on the response.
        body (dict): Payload to JSON-encode into the response body.
    
    Returns:
        dict: A mapping containing 'statusCode' (int), 'headers' (includes Content-Type: application/json), and 'body' (JSON string of the provided payload).
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve a header value from an event's headers using a case-insensitive header name lookup.
    
    Parameters:
        event (dict): Incoming event object expected to contain a "headers" mapping.
        header_name (str): Name of the header to retrieve.
    
    Returns:
        str | None: The header value if present, `None` otherwise.
    """
    headers = event.get("headers") or {}
    target = header_name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _is_authorized(event: dict, expected_key: str) -> bool:
    """
    Validate request authorization by comparing an API key found in common headers to the expected key.
    
    Searches the headers "x-discord-api-key", "x-api-key", and "Authorization" (in that order) for a supplied key. If an Authorization header contains a Bearer token, a leading "Bearer " prefix is removed case-insensitively before comparison. Comparison is performed by exact equality against expected_key.
    
    Parameters:
        event (dict): Incoming request event containing headers.
        expected_key (str): The API key expected for authorized requests.
    
    Returns:
        bool: `true` if the supplied key matches `expected_key`, `false` otherwise.
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
    Parse and validate the Lambda HTTP event body and return its JSON payload.
    
    Parameters:
        event (dict): Incoming Lambda event expected to include a "body" key and optional "isBase64Encoded".
            - If "body" is a dict, it is returned unchanged.
            - If "body" is a base64-encoded string and "isBase64Encoded" is truthy, it will be decoded before parsing.
    
    Returns:
        dict: The parsed JSON object from the request body, or an empty dict when the body is None or empty/whitespace.
    
    Raises:
        ValueError: If the body is neither a dict nor a string, or if the body cannot be decoded or parsed as a JSON object.
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
    Produce a copy of the provided HTTP headers with sensitive values replaced by "[REDACTED]".
    
    Parameters:
        headers (dict | None): Mapping of header names to values. If `headers` is not a dict, an empty dict is returned. Keys that are not strings are ignored.
    
    Returns:
        dict: A headers dictionary where values for sensitive header names (e.g., authorization, proxy-authorization, x-api-key, x-discord-api-key, cookie, set-cookie and similar) are replaced with "[REDACTED]"; non-sensitive headers are preserved unchanged.
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
    Constructs a normalized event dictionary from the incoming payload and raw event.
    
    Parameters:
        payload (dict): The parsed request payload; optional keys such as
            tenant_id, workspace_id, connection_id, account_id, scope_type,
            scope_id/container_id/channel_id, operation/event_type, object_type/item_type,
            object_id/source_item_id/id, occurred_at/observed_at, content_changed,
            metadata_changed, permissions_changed, from_parent_id, to_parent_id,
            from_path, to_path, source_version/version, actor, and change_id may be
            used to populate corresponding fields in the normalized event.
        raw_event (dict): The original event dictionary (typically the raw request),
            used to include redacted headers and the original payload under
            source_details.raw_event.
    
    Returns:
        dict: A normalized event structure containing keys: schema_version, event_id,
        emitted_at, tenant, source, scope, change, actor, delivery_key, and
        source_details (which includes redacted raw headers and the original payload).
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
            "scope_type": payload.get("scope_type", "discord_channel"),
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
                "headers": _redact_headers(raw_event.get("headers")),
                "payload": payload,
            }
        },
    }


def _enqueue(message: dict):
    """
    Enqueues the provided dictionary as a JSON-formatted message to the configured SQS queue.
    
    Parameters:
        message (dict): The payload to send; it will be serialized to JSON and placed in the SQS message body.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, _ctx):
    """
    Handle an incoming webhook: authorize the request, parse and normalize the payload, enqueue the normalized event to SQS, and return an HTTP-like response.
    
    Parameters:
        event (dict): Lambda event dictionary containing headers and body.
        _ctx: Lambda context object (unused).
    
    Returns:
        dict: HTTP-like response with keys `statusCode`, `headers`, and `body` (JSON string). Possible status codes:
            - 202: accepted; `body` contains {"status": "accepted", "event_id": <id>}.
            - 401: unauthorized; `body` contains {"error": "Unauthorized"}.
            - 400: invalid request payload; `body` contains {"error": "Invalid request payload"}.
            - 500: failed to enqueue event; `body` contains {"error": "Failed to enqueue event"}.
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
