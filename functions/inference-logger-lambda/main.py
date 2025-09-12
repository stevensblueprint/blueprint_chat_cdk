# file: lambda/bedrock-usage-py/handler.py
import os
import json
import boto3
from datetime import datetime
from decimal import Decimal

dynamodb = boto3.resource("dynamodb")
MONTHLY_USAGE_TABLE = os.environ["MONTHLY_USAGE_TABLE"]
TRANSACTIONS_TABLE = os.environ["TRANSACTIONS_TABLE"]

monthly_tbl = dynamodb.Table(MONTHLY_USAGE_TABLE)
tx_tbl = dynamodb.Table(TRANSACTIONS_TABLE)

def _calculate_cost_from_tokens(input_tokens: int, output_tokens: int, model_id: str):
    if model_id == "anthropic.claude-3-haiku-20240307-v1:0":
        return 0.00000025 * input_tokens + 0.00000125 * output_tokens
    elif model_id == "anthropic.claude-sonnet-4-20250514-v1:0":
        return 0.000003 * input_tokens + 0.000015 * output_tokens
    else:
        return 0

def handler(event, _):
    body = json.loads(event.get("body", "{}"))

    user_arn = body.get("userArn")
    timestamp = body.get("timestamp")
    model_id = body.get("model_id")
    input_tokens = body.get("input_tokens")
    output_tokens = body.get("output_tokens")

    dt = datetime.strptime(timestamp, "%Y-%m-%dT%H:%M:%S")
    month_year = dt.strftime("%m_%Y")

    usage = {
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
    }

    cost = Decimal(str(_calculate_cost_from_tokens(input_tokens, output_tokens, model_id)))

    tx_item = {
        "userArn": user_arn,
        "timestamp": timestamp,
        "model_id": model_id,
        "usage": usage,
        "cost": cost
    }

    tx_tbl.put_item(Item=tx_item)

    monthly_tbl.update_item(
        Key={"userArn": user_arn, "month_year": month_year},
        UpdateExpression="""
            ADD invocations :one,
                cost :cost
        """,
        ExpressionAttributeValues={
            ":one": 1,
            ":cost": cost,
        },
    )

    return {"statusCode": 200, "body": json.dumps({"ok": True})}
