import json
import logging
import os
import time
import traceback
import uuid
from collections import OrderedDict

import boto3
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel

logging.basicConfig(level=logging.DEBUG, format="%(asctime)s %(levelname)s %(name)s — %(message)s")
logger = logging.getLogger(__name__)

app = FastAPI()

DOCUMENT_BUCKET = os.environ.get("DOCUMENT_BUCKET", "")
CHAT_HISTORY_TABLE = os.environ.get("CHAT_HISTORY_TABLE", "ChatHistory")
BEDROCK_MODEL_ID = os.environ.get("BEDROCK_MODEL_ID", "us.anthropic.claude-3-5-haiku-20241022-v1:0")
EMBEDDING_MODEL_ID = os.environ.get("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
VECTOR_BUCKET_NAME = os.environ.get("VECTOR_BUCKET_NAME", "")
VECTOR_INDEX_NAME = os.environ.get("VECTOR_INDEX_NAME", "documents")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")
TOP_K_DOCS = int(os.environ.get("TOP_K_DOCS", "3"))

logger.info(
    "Config — BEDROCK_MODEL_ID=%r, EMBEDDING_MODEL_ID=%r, VECTOR_BUCKET_NAME=%r, VECTOR_INDEX_NAME=%r, AWS_REGION=%r",
    BEDROCK_MODEL_ID, EMBEDDING_MODEL_ID, VECTOR_BUCKET_NAME, VECTOR_INDEX_NAME, AWS_REGION,
)

s3 = boto3.client("s3", region_name=AWS_REGION)
dynamodb = boto3.resource("dynamodb", region_name=AWS_REGION)
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
s3vectors = boto3.client("s3vectors", region_name=AWS_REGION)

chat_table = dynamodb.Table(CHAT_HISTORY_TABLE)

MAX_MEMORY_CONVERSATIONS = 100
MAX_MEMORY_TURNS = 20  # messages per conversation (10 user+assistant pairs)


class _ConversationCache(OrderedDict):
    """LRU cache: evicts the least-recently-used conversation when full."""

    def get_messages(self, conversation_id: str) -> list[dict] | None:
        if conversation_id not in self:
            return None
        self.move_to_end(conversation_id)
        return self[conversation_id]

    def append_turn(self, conversation_id: str, user_text: str, assistant_text: str) -> None:
        if conversation_id not in self:
            self[conversation_id] = []
        self.move_to_end(conversation_id)
        self[conversation_id].append({"role": "user", "content": [{"text": user_text}]})
        self[conversation_id].append({"role": "assistant", "content": [{"text": assistant_text}]})
        if len(self[conversation_id]) > MAX_MEMORY_TURNS:
            self[conversation_id] = self[conversation_id][-MAX_MEMORY_TURNS:]
        if len(self) > MAX_MEMORY_CONVERSATIONS:
            self.popitem(last=False)

    def seed(self, conversation_id: str, messages: list[dict]) -> None:
        """Populate cache from DynamoDB on first access."""
        self[conversation_id] = messages
        self.move_to_end(conversation_id)


_memory = _ConversationCache()

def _embed(text: str) -> list[float]:
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({"inputText": text[:8000]}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def _search_documents(query: str) -> tuple[str, list[str]]:
    """Return (doc_context, sources) for the most relevant chunks in S3 Vectors."""
    if not VECTOR_BUCKET_NAME:
        return "", []

    try:
        query_embedding = _embed(query)
        resp = s3vectors.query_vectors(
            vectorBucketName=VECTOR_BUCKET_NAME,
            indexName=VECTOR_INDEX_NAME,
            queryVector={"float32": query_embedding},
            topK=TOP_K_DOCS,
            returnMetadata=True,
        )

        doc_context = ""
        sources: list[str] = []
        for match in resp.get("vectors", []):
            metadata = match.get("metadata", {})
            key = metadata.get("documentKey", match["key"])
            text = metadata.get("text", "")
            distance = match.get("distance", 0)
            doc_context += f"\n--- Document: {key} (distance: {distance:.4f}) ---\n{text}\n"
            if key not in sources:
                sources.append(key)

        logger.debug("S3 Vectors search returned %d matches from: %s", len(sources), sources)
        return doc_context, sources

    except Exception as e:
        logger.warning("S3 Vectors search failed: %s", e)
        return "", []
class InvocationRequest(BaseModel):
    prompt: str
    conversationId: str | None = None


@app.middleware("http")
async def log_requests(request: Request, call_next):
    body = await request.body()
    logger.debug(
        "Incoming request — method: %s, path: %s, headers: %s, body: %r",
        request.method,
        request.url.path,
        dict(request.headers),
        body.decode("utf-8", errors="replace"),
    )
    response = await call_next(request)
    logger.debug(
        "Outgoing response — path: %s, status: %d",
        request.url.path,
        response.status_code,
    )
    if response.status_code == 422:
        logger.error(
            "422 Unprocessable Entity on %s — body sent was: %r",
            request.url.path,
            body.decode("utf-8", errors="replace"),
        )
    return response


@app.get("/ping")
def ping():
    return {"status": "healthy"}


@app.exception_handler(Exception)
async def unhandled_exception_handler(request: Request, exc: Exception):
    logger.error("Unhandled exception on %s %s: %s\n%s", request.method, request.url, exc, traceback.format_exc())
    return JSONResponse(status_code=500, content={"error": str(exc)})


@app.post("/invocations")
async def invocations(raw_request: Request):
    body = await raw_request.body()
    try:
        data = json.loads(body)
    except json.JSONDecodeError as e:
        logger.error("Failed to parse request body as JSON: %s — body: %r", e, body)
        return JSONResponse(status_code=400, content={"error": f"Invalid JSON: {e}"})

    try:
        request = InvocationRequest(**data)
    except Exception as e:
        logger.error("Request validation failed: %s — parsed data: %s", e, data)
        return JSONResponse(status_code=422, content={"error": str(e)})

    conversation_id = request.conversationId or str(uuid.uuid4())
    is_new = request.conversationId is None
    logger.info("Invocation — conversationId: %s (new=%s), prompt length: %d", conversation_id, is_new, len(request.prompt))

    cached = _memory.get_messages(conversation_id)
    if cached is not None:
        logger.debug("Short-term memory hit — %d messages for %s", len(cached), conversation_id)
        messages = list(cached)
    else:
        logger.debug("Short-term memory miss — querying DynamoDB for %s", conversation_id)
        db_response = chat_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key("conversationId").eq(conversation_id),
            ScanIndexForward=False,
            Limit=MAX_MEMORY_TURNS,
        )
        history_items = list(reversed(db_response.get("Items", [])))
        logger.debug("Retrieved %d history items from DynamoDB", len(history_items))
        messages = [{"role": item["role"], "content": [{"text": item["content"]}]} for item in history_items]
        _memory.seed(conversation_id, messages)

    doc_context, sources = _search_documents(request.prompt)

    system_prompt = "You are a helpful assistant that answers questions about provided documents. Your name is Byte, you are Blueprint's assistant"
    if doc_context:
        system_prompt += f"\n\nRelevant document excerpts:\n{doc_context}"

    messages.append({"role": "user", "content": [{"text": request.prompt}]})
    logger.debug(
        "Calling Bedrock converse_stream — modelId: %s, message count: %d, system prompt length: %d",
        BEDROCK_MODEL_ID, len(messages), len(system_prompt),
    )

    async def generate():
        full_text = ""
        try:
            bedrock_response = bedrock.converse_stream(
                modelId=BEDROCK_MODEL_ID,
                system=[{"text": system_prompt}],
                messages=messages,
            )
            for event in bedrock_response["stream"]:
                if "contentBlockDelta" in event:
                    token = event["contentBlockDelta"]["delta"].get("text", "")
                    if token:
                        full_text += token
                        yield f"data: {json.dumps({'type': 'token', 'text': token})}\n\n"
                elif "metadata" in event:
                    logger.debug("Bedrock usage: %s", event["metadata"].get("usage"))
        except Exception as e:
            logger.error("Bedrock streaming error: %s", e)
            yield f"data: {json.dumps({'type': 'error', 'error': str(e)})}\n\n"
            return

        _memory.append_turn(conversation_id, request.prompt, full_text)
        logger.debug("Short-term memory updated — %d messages for %s", len(_memory[conversation_id]), conversation_id)

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
            "content": full_text,
        })
        logger.debug("DynamoDB writes complete")
        logger.info("Response streamed — length: %d chars", len(full_text))

        yield f"data: {json.dumps({'type': 'done', 'conversationId': conversation_id, 'sources': sources})}\n\n"

    return StreamingResponse(generate(), media_type="text/event-stream")
