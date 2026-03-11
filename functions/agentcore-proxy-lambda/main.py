import json
import os
import boto3

AGENT_RUNTIME_ARN = os.environ["AGENT_RUNTIME_ARN"]
REGION = os.environ.get("REGION", "us-east-1")

client = boto3.client("bedrock-agentcore", region_name=REGION)

CORS_HEADERS = {
    "Access-Control-Allow-Origin": "*",
    "Access-Control-Allow-Headers": "Content-Type,Authorization,Accept,Origin,X-Requested-With",
    "Access-Control-Allow-Methods": "POST,OPTIONS",
    "Content-Type": "application/json",
}


def handler(event, context):
    if event.get("httpMethod") == "OPTIONS":
        return {"statusCode": 200, "headers": CORS_HEADERS, "body": ""}

    try:
        body = json.loads(event.get("body") or "{}")
        prompt = body.get("prompt", "")
        conversation_id = body.get("conversationId")

        if not prompt:
            return {
                "statusCode": 400,
                "headers": CORS_HEADERS,
                "body": json.dumps({"error": "prompt is required"}),
            }

        payload = {"prompt": prompt}
        if conversation_id:
            payload["conversationId"] = conversation_id

        response = client.invoke_agent_runtime(
            agentRuntimeArn=AGENT_RUNTIME_ARN,
            qualifier="DEFAULT",
            payload=json.dumps(payload).encode("utf-8"),
        )

        result = json.loads(response["output"].read())

        return {
            "statusCode": 200,
            "headers": CORS_HEADERS,
            "body": json.dumps(result),
        }

    except Exception as e:
        return {
            "statusCode": 500,
            "headers": CORS_HEADERS,
            "body": json.dumps({"error": str(e)}),
        }
