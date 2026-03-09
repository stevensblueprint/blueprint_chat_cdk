import json
import os
import time
import uuid

import boto3
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET", "")
CHAT_HISTORY_TABLE = os.environ.get("CHAT_HISTORY_TABLE", "ChatHistory")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

s3 = boto3.client("s3", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)

chat_table = dynamodb.Table(CHAT_HISTORY_TABLE)


class InvocationRequest(BaseModel):
    prompt: str
    conversationId: str | None = None


@app.get("/ping")
def ping():
    return {"status": "healthy"}


@app.post("/invocations")
def invocations(request: InvocationRequest):
    conversation_id = request.conversationId or str(uuid.uuid4())

    # 1. Fetch recent chat history from DynamoDB (last 10 turns)
    response = chat_table.query(
        KeyConditionExpression=boto3.dynamodb.conditions.Key("conversationId").eq(conversation_id),
        ScanIndexForward=False,
        Limit=10,
    )
    history_items = list(reversed(response.get("Items", [])))

    messages = []
    for item in history_items:
        messages.append({"role": item["role"], "content": item["content"]})

    # 2. Read documents from S3 for context
    doc_context = ""
    if DOCUMENT_BUCKET:
        try:
            listed = s3.list_objects_v2(Bucket=DOCUMENT_BUCKET, MaxKeys=5)
            for obj in listed.get("Contents", []):
                key = obj["Key"]
                try:
                    body = s3.get_object(Bucket=DOCUMENT_BUCKET, Key=key)["Body"].read().decode("utf-8", errors="ignore")
                    doc_context += f"\n--- Document: {key} ---\n{body[:3000]}\n"
                except Exception:
                    pass
        except Exception:
            pass

    # 3. Build system prompt with document context
    system_prompt = "You are a helpful assistant that answers questions about provided documents."
    if doc_context:
        system_prompt += f"\n\nAvailable documents:\n{doc_context}"

    # 4. Add current user message
    messages.append({"role": "user", "content": request.prompt})

    # 5. Call Bedrock converse
    bedrock_response = bedrock.converse(
        modelId=BEDROCK_MODEL_ID,
        system=[{"text": system_prompt}],
        messages=messages,
    )
    assistant_text = bedrock_response["output"]["message"]["content"][0]["text"]

    # 6. Persist user + assistant turns to DynamoDB
    now = int(time.time() * 1000)
    chat_table.put_item(Item={
        "conversationId": conversation_id,
        "timestamp": now,
        "role": "user",
        "content": request.prompt,
    })
    chat_table.put_item(Item={
        "conversationId": conversation_id,
        "timestamp": now + 1,
        "role": "assistant",
        "content": assistant_text,
    })

    return {"response": assistant_text, "conversationId": conversation_id}