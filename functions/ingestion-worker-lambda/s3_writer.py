import logging
import boto3

logger = logging.getLogger(__name__)

_s3 = boto3.client("s3")

_SOURCE_EXT = {
    "notion": ".md",
    "bookstack": ".md",
    "google_drive": ".pdf",
}

_SOURCE_CONTENT_TYPE = {
    "notion": "text/markdown",
    "bookstack": "text/markdown",
    "google_drive": "application/pdf",
}


def resolve_s3_key(source: str, workspace_id: str, resource_id: str) -> str:
    ext = _SOURCE_EXT[source]
    prefix = workspace_id if workspace_id else source
    return f"{prefix}/{source}/{resource_id}{ext}"


def write_document(bucket: str, key: str, content: bytes, source: str) -> None:
    content_type = _SOURCE_CONTENT_TYPE[source]
    _s3.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
    logger.info("Wrote s3://%s/%s", bucket, key)


def delete_document(bucket: str, key: str) -> None:
    _s3.delete_object(Bucket=bucket, Key=key)
    logger.info("Deleted s3://%s/%s", bucket, key)
