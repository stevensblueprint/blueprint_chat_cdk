# file: lambda/bedrock-usage-py/handler.py
import os
import json
import boto3
from datetime import datetime, timezone

dynamodb = boto3.resource("dynamodb")
MONTHLY_USAGE_TABLE = os.environ["MONTHLY_USAGE_TABLE"]
TRANSACTIONS_TABLE = os.environ["TRANSACTIONS_TABLE"]

monthly_tbl = dynamodb.Table(MONTHLY_USAGE_TABLE)
tx_tbl = dynamodb.Table(TRANSACTIONS_TABLE)


def _iso_to_month_year(iso_ts: str) -> str:
    dt = datetime.fromisoformat(iso_ts.replace("Z", "+00:00"))
    return dt.strftime("%Y-%m")


def _principal_to_user_id(detail: dict) -> str:
    ui = detail.get("userIdentity") or {}
    return ui.get("arn") or ui.get("principalId") or "unknown"


def handler(event, _context):
    detail = event.get("detail") or {}

    event_time = detail.get("eventTime") or datetime.now(timezone.utc).isoformat()
    region = detail.get("awsRegion", "unknown")
    event_name = detail.get("eventName", "unknown")
    event_id = detail.get("eventID", "")
    event_source_ip = detail.get("sourceIPAddress", "")

    request_params = detail.get("requestParameters") or {}
    model_id = request_params.get("modelId", "unknown")

    user_id = _principal_to_user_id(detail)
    month_year = _iso_to_month_year(event_time)

    usage = {
        "input_tokens": 0,
        "output_tokens": 0,
        "total_tokens": 0,
    }

    tx_item = {
        "userId": user_id,
        "timestamp": event_time,  # ISO time for natural sort
        "month_year": month_year,  # supports GSI "month-year-index"
        "model_id": model_id,  # supports GSI "model-id-index"
        "region": region,
        "event_name": event_name,
        "event_id": event_id,
        "source_ip": event_source_ip,
        "usage": usage,
    }

    tx_tbl.put_item(Item=tx_item)

    monthly_tbl.update_item(
        Key={"userId": user_id, "month_year": month_year},
        UpdateExpression="""
            ADD invocations :one,
                input_tokens :in_tok,
                output_tokens :out_tok,
                total_tokens :tot_tok
        """,
        ExpressionAttributeValues={
            ":one": 1,
            ":in_tok": usage["input_tokens"],
            ":out_tok": usage["output_tokens"],
            ":tot_tok": usage["total_tokens"],
        },
    )

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
