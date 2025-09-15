import os
import json
import boto3
from botocore.exceptions import ClientError

REGION = "us-east-1"
GLOBAL_MAX_TOKENS_PER_CALL = int(os.environ.get("GLOBAL_MAX_TOKENS_PER_CALL", 1024))

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,x-api-key,Accept,Origin,X-Requested-With",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}

def handler(event, _):
    try:
        body = json.loads(event.get("body", "{}"))
        model_id = body.get("modelId")
        messages = body.get("messages")

        if not model_id or not messages:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Missing required fields: modelId, messages"})
            }

        bedrock_client = boto3.client("bedrock-runtime", region_name=REGION)

        max_tokens = min(body.get("max_tokens", 256), GLOBAL_MAX_TOKENS_PER_CALL)

        payload = {
            "anthropic_version": "bedrock-2023-05-31",
            "messages": [{"role": "user", "content": messages}],
            "max_tokens": max_tokens,
            "temperature": body.get("temperature", 0.5),
        }

        response = bedrock_client.invoke_model_with_response_stream(
            modelId=model_id,
            contentType="application/json",
            accept="application/json",
            body=json.dumps(payload)
        )

        usage_totals = {
            "inputTokens": 0,
            "outputTokens": 0,
            "cacheReadTokens": 0,
            "cacheWriteTokens": 0,
        }

        def stream_generator():
            for event in response["body"]:
                if "chunk" in event:
                    raw_bytes = event["chunk"]["bytes"]
                    try:
                        parsed = json.loads(raw_bytes.decode("utf-8"))

                        if "metadata" in parsed and "usage" in parsed["metadata"]:
                            usage = parsed["metadata"]["usage"]
                            usage_totals["inputTokens"] += usage.get("inputTokens", 0)
                            usage_totals["outputTokens"] += usage.get("outputTokens", 0)
                            usage_totals["cacheReadTokens"] += usage.get("cacheReadInputTokens", 0)
                            usage_totals["cacheWriteTokens"] += usage.get("cacheWriteInputTokens", 0)
                    except Exception:
                        pass

                    yield raw_bytes

                else:
                    yield json.dumps(event).encode("utf-8")

        print("final token usage: ", usage_totals)

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": stream_generator()
        }

    except ClientError as err:
        code = err.response["Error"]["Code"]
        if code == "AccessDeniedException":
            return {
                "statusCode": 403,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "error": "Access denied. Check IAM permissions and model access in Bedrock console.",
                    "details": str(err)
                })
            }
        elif code == "ValidationException":
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({
                    "error": "Invalid request parameters.",
                    "details": str(err)
                })
            }
        else:
            return {
                "statusCode": 500,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "Internal server error", "details": str(err)})
            }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": "Internal server error", "details": str(e)})
        }
