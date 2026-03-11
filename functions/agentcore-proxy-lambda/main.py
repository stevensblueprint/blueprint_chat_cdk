import json
import logging
import os
import traceback

import boto3

logger = logging.getLogger()
logger.setLevel(logging.DEBUG)

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
AGENT_RUNTIME_ENDPOINT = os.environ["AGENT_RUNTIME_ENDPOINT"]
REGION = os.environ.get("REGION", "us-east-1")

client = boto3.client("bedrock-agentcore", region_name=REGION)


def _invoke_agent(event) -> "StreamingBody":
    """Parse request, call AgentCore, and return the streaming body."""
    body = json.loads(event.get("body") or "{}")
    prompt = body.get("prompt", "")
    conversation_id = body.get("conversationId")

    if not prompt:
        raise ValueError("prompt is required")

    payload = {"prompt": prompt}
    if conversation_id:
        payload["conversationId"] = conversation_id

    logger.info(
        "Invoking agent runtime — ARN: %s, endpoint: %s, conversationId: %s",
        AGENT_RUNTIME_ARN, AGENT_RUNTIME_ENDPOINT, conversation_id,
    )

    response = client.invoke_agent_runtime(
        agentRuntimeArn=AGENT_RUNTIME_ARN,
        qualifier=AGENT_RUNTIME_ENDPOINT,
        payload=json.dumps(payload).encode("utf-8"),
    )

    logger.debug(
        "Agent runtime response — keys: %s, status: %s",
        list(response.keys()),
        response.get("ResponseMetadata", {}).get("HTTPStatusCode"),
    )

    body_key = next(
        (k for k in response if k != "ResponseMetadata" and hasattr(response[k], "read")),
        None,
    )
    logger.debug("Streaming response key: %s", body_key)
    return response[body_key]


def _http_method(event: dict) -> str:
    # Function URL events use requestContext.http.method; API GW uses httpMethod
    return (
        event.get("requestContext", {}).get("http", {}).get("method")
        or event.get("httpMethod")
        or "POST"
    ).upper()


def handler(*args):
    """Unified handler — called with (event, context) from API Gateway or
    (event, response_stream, context) when invoked via streaming Function URL."""

    if len(args) == 3:
        event, response_stream, context = args
        _handle_streaming(event, response_stream)
    else:
        event, context = args
        return _handle_buffered(event)


# ---------------------------------------------------------------------------
# Streaming path — Lambda Function URL with InvokeMode=RESPONSE_STREAM
# ---------------------------------------------------------------------------

def _handle_streaming(event: dict, response_stream) -> None:
    response_stream.setContentType("text/event-stream")

    if _http_method(event) == "OPTIONS":
        response_stream.write(b"")
        return

    try:
        streaming_body = _invoke_agent(event)
        for chunk in streaming_body.iter_chunks(1024):
            response_stream.write(chunk)
    except Exception as e:
        logger.error("Streaming error: %s\n%s", e, traceback.format_exc())
        response_stream.write(
            f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n".encode()
        )


# ---------------------------------------------------------------------------
# Buffered path — API Gateway REST integration (non-streaming fallback)
# ---------------------------------------------------------------------------

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,Accept,Origin,X-Requested-With",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}


def _handle_buffered(event: dict) -> dict:
    if _http_method(event) == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        streaming_body = _invoke_agent(event)
        raw = streaming_body.read()
        logger.debug("Raw output bytes: %s", raw)
        result = json.loads(raw)
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": json.dumps(result)}
    except ValueError as e:
        return {"statusCode": 400, "headers": CORS_HEADERS, "body": json.dumps({"error": str(e)})}
    except Exception as e:
        logger.error("Unhandled exception: %s\n%s", e, traceback.format_exc())
        return {"statusCode": 500, "headers": CORS_HEADERS, "body": json.dumps({"error": str(e)})}
