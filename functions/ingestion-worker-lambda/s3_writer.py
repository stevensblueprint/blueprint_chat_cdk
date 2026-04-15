import logging
import boto3

logger = logging.getLogger(__name__)

_s3 = boto3.client("s3")


def resolve_s3_key(source: str, workspace_id: str, resource_id: str, ext: str) -> str:
    prefix = workspace_id if workspace_id else source
    return f"{prefix}/{source}/{resource_id}{ext}"


def write_document(bucket: str, key: str, content: bytes, content_type: str) -> None:
    _s3.put_object(Bucket=bucket, Key=key, Body=content, ContentType=content_type)
    logger.info("Wrote s3://%s/%s", bucket, key)


def delete_resource(bucket: str, source: str, workspace_id: str, resource_id: str) -> None:
    """Deletes all S3 objects for a resource regardless of file extension."""
    prefix = f"{workspace_id if workspace_id else source}/{source}/{resource_id}"
    paginator = _s3.get_paginator("list_objects_v2")
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        for obj in page.get("Contents", []):
            _s3.delete_object(Bucket=bucket, Key=obj["Key"])
            logger.info("Deleted s3://%s/%s", bucket, obj["Key"])
