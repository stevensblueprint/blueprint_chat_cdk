import base64
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
    Builds an HTTP-style JSON response dictionary suitable for API Gateway.
    
    Parameters:
        status_code (int): HTTP status code to return.
        body (dict): Object to serialize as the JSON response body.
    
    Returns:
        dict: A response dictionary containing `statusCode`, `headers` (with Content-Type application/json), and `body` (the JSON-serialized string of `body`).
    """
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    """
    Retrieve a header value from an event, checking the provided header name first and then its lowercase form.
    
    Parameters:
    	event (dict): The event object containing an optional "headers" mapping.
    	header_name (str): The header name to look up (case-insensitive).
    
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
    Validate that the incoming event contains the expected API key in common header locations.
    
    Parameters:
        event (dict): Incoming request/event dictionary containing headers.
        expected_key (str): The API key value expected for authorization.
    
    Returns:
        bool: `true` if a key is present in the headers (x-notion-api-key, x-api-key, or authorization), a leading "Bearer " prefix is removed if present, and the resulting key exactly equals `expected_key`; `false` otherwise.
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
    Parse and return the request body from a Lambda-style event as a dict.
    
    Parameters:
        event (dict): Lambda event object. The function reads `body` and `isBase64Encoded` keys:
            - If `body` is None or an empty/whitespace string, returns an empty dict.
            - If `body` is already a dict, returns it unchanged.
            - If `isBase64Encoded` is truthy, decodes `body` from base64 before parsing.
    
    Returns:
        dict: The parsed JSON object from the request body, or an empty dict when no content is provided.
    
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
    Builds a normalized event envelope from a Notion webhook payload and the original raw event.
    
    Parameters:
        payload (dict): Parsed webhook payload containing Notion event fields (e.g., ids, timestamps, operation, actor, and change metadata).
        raw_event (dict): Original incoming event object (typically includes transport-level fields such as headers) used for provenance.
    
    Returns:
        dict: A standardized event dictionary containing:
            - schema_version: Version of the envelope schema.
            - event_id: Generated UUID for this envelope.
            - emitted_at: ISO 8601 UTC timestamp when the envelope was created.
            - tenant: Mapping with tenant_id and workspace_id.
            - source: Mapping with source_type, connection_id, and account_id.
            - scope: Mapping with scope_type and scope_id.
            - change: Details about the change (operation, object_type, object_id, occurred_at, booleans for content/metadata/permissions changes, parent/path deltas, and source_version).
            - actor: Actor information from the payload or "unknown".
            - delivery_key: Composed string used to deduplicate or route the event.
            - source_details: Contains the original raw_event headers and the parsed payload.
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
                "headers": raw_event.get("headers") or {},
                "payload": payload,
            }
        },
    }


def _enqueue(message: dict):
    """
    Send a JSON-serialized message to the configured SQS queue.
    
    Parameters:
        message (dict): Event payload to enqueue; will be serialized to JSON before sending.
    """
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, ctx):
    """
    Handle an incoming Notion webhook: authenticate the request, parse and normalize the payload, enqueue a normalized event to SQS, and return an HTTP-like response.
    
    Parameters:
        event (dict): AWS Lambda event object representing the incoming request.
        ctx: AWS Lambda context object (unused).
    
    Returns:
        dict: HTTP-like response with keys `statusCode`, `headers`, and `body` (JSON string).
            - On authentication failure: 401 with body {"error": "Unauthorized"}.
            - On invalid request payload: 400 with body {"error": "Invalid request payload"}.
            - On enqueue failure: 500 with body {"error": "Failed to enqueue event"}.
            - On success: 202 with body {"status": "accepted", "event_id": <generated_event_id>}.
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
