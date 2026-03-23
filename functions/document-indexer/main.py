import json
import logging
import os

import boto3

logger = logging.getLogger()
logger.setLevel(logging.INFO)

VECTOR_BUCKET_NAME = os.environ["VECTOR_BUCKET_NAME"]
VECTOR_INDEX_NAME = os.environ["VECTOR_INDEX_NAME"]
EMBEDDING_MODEL_ID = os.environ.get("EMBEDDING_MODEL_ID", "amazon.titan-embed-text-v2:0")
AWS_REGION = os.environ.get("AWS_REGION", "us-east-1")

CHUNK_SIZE = 1500
CHUNK_OVERLAP = 200

s3 = boto3.client("s3")
bedrock = boto3.client("bedrock-runtime", region_name=AWS_REGION)
s3vectors = boto3.client("s3vectors", region_name=AWS_REGION)


def _chunk_text(text: str) -> list[str]:
    chunks = []
    start = 0
    while start < len(text):
        end = min(start + CHUNK_SIZE, len(text))
        chunks.append(text[start:end])
        if end == len(text):
            break
        start += CHUNK_SIZE - CHUNK_OVERLAP
    return chunks


def _embed(text: str) -> list[float]:
    resp = bedrock.invoke_model(
        modelId=EMBEDDING_MODEL_ID,
        body=json.dumps({"inputText": text[:8000]}),
        contentType="application/json",
        accept="application/json",
    )
    return json.loads(resp["body"].read())["embedding"]


def _index_document(bucket: str, key: str) -> None:
    logger.info("Indexing document: s3://%s/%s", bucket, key)
    body = s3.get_object(Bucket=bucket, Key=key)["Body"].read().decode("utf-8", errors="ignore")
    chunks = _chunk_text(body)
    logger.info("Split %s into %d chunks", key, len(chunks))

    vectors = []
    for i, chunk in enumerate(chunks):
        vectors.append({
            "key": f"{key}#{i}",
            "data": {"float32": _embed(chunk)},
            "metadata": {"documentKey": key, "text": chunk},
        })

    # PutVectors accepts up to 100 per call
    for i in range(0, len(vectors), 100):
        s3vectors.put_vectors(
            vectorBucketName=VECTOR_BUCKET_NAME,
            indexName=VECTOR_INDEX_NAME,
            vectors=vectors[i:i + 100],
        )

    logger.info("Stored %d vectors for %s", len(vectors), key)


def _delete_document(key: str) -> None:
    """Delete all vectors belonging to a document by listing with its key prefix."""
    logger.info("Removing vectors for deleted document: %s", key)
    keys_to_delete = []
    paginator_kwargs = {
        "vectorBucketName": VECTOR_BUCKET_NAME,
        "indexName": VECTOR_INDEX_NAME,
    }
    next_token = None
    while True:
        if next_token:
            paginator_kwargs["nextToken"] = next_token
        resp = s3vectors.list_vectors(**paginator_kwargs, maxResults=1000)
        for v in resp.get("vectors", []):
            if v["key"].startswith(f"{key}#"):
                keys_to_delete.append(v["key"])
        next_token = resp.get("nextToken")
        if not next_token:
            break

    if keys_to_delete:
        for i in range(0, len(keys_to_delete), 100):
            s3vectors.delete_vectors(
                vectorBucketName=VECTOR_BUCKET_NAME,
                indexName=VECTOR_INDEX_NAME,
                keys=keys_to_delete[i:i + 100],
            )
        logger.info("Deleted %d vectors for %s", len(keys_to_delete), key)
    else:
        logger.info("No vectors found for %s", key)


def handler(event, context):
    for record in event.get("Records", []):
        event_name = record["eventName"]
        bucket = record["s3"]["bucket"]["name"]
        key = record["s3"]["object"]["key"]

        try:
            if event_name.startswith("ObjectCreated"):
                _index_document(bucket, key)
            elif event_name.startswith("ObjectRemoved"):
                _delete_document(key)
            else:
                logger.warning("Unhandled event type: %s", event_name)
        except Exception as e:
            logger.error("Failed to process %s on s3://%s/%s: %s", event_name, bucket, key, e)
            raise
