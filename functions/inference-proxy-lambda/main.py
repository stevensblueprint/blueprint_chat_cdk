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

        response = bedrock_client.invoke_model(
            modelId=model_id,
            contentType="application/json",
            body=json.dumps(payload)
        )

        response_body = json.loads(response["body"].read().decode())
        result_text = response_body.get("content", [{}])[0].get("text", response_body)

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps({"response": result_text})
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
