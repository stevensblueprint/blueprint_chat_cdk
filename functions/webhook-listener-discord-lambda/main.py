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
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(body),
    }


def _get_header(event: dict, header_name: str) -> str | None:
    headers = event.get("headers") or {}
    target = header_name.lower()
    for key, value in headers.items():
        if isinstance(key, str) and key.lower() == target:
            return value
    return None


def _is_authorized(event: dict, expected_key: str) -> bool:
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
        return json.loads(body)
    except (binascii.Error, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError("Invalid request payload") from exc


def _redact_headers(headers: dict | None) -> dict:
    if not isinstance(headers, dict):
        return {}

    # Redact auth/session secrets before forwarding raw event metadata.
    sensitive_header_names = {
        "authorization",
        "proxy-authorization",
        "x-api-key",
        "x-discord-api-key",
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
                "headers": _redact_headers(raw_event.get("headers")),
                "payload": payload,
            }
        },
    }


def _enqueue(message: dict):
    SQS_CLIENT.send_message(QueueUrl=QUEUE_URL, MessageBody=json.dumps(message))


def handler(event, _ctx):
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
