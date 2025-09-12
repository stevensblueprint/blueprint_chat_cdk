# file: lambda/bedrock-usage-py/handler.py
import os
import json
import boto3
from datetime import datetime, timezone
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
MONTHLY_USAGE_TABLE = os.environ["MONTHLY_USAGE_TABLE"]
MONTHLY_LIMIT = float(os.environ["MONTHLY_LIMIT"])

monthly_tbl = dynamodb.Table(MONTHLY_USAGE_TABLE)

def handler(event, _):
    now = datetime.now()
    month_year = now.strftime("%m_%Y")

    body = json.loads(event.get("body", "{}"))
    user_arn = body.get("userArn")

    response = monthly_tbl.get_item(
        Key={
            "userArn": user_arn,
            "month_year": month_year
        }
    )

    item = response.get("Item")

    if item:
        monthly_usage = item.get("cost")
        if monthly_usage >= MONTHLY_LIMIT:
            return {"statusCode": 200, "body": json.dumps({"isAuthorized": False})}
        else:
            {"statusCode": 200, "body": json.dumps({"isAuthorized": True})}
    else:
        return {"statusCode": 200, "body": json.dumps({"isAuthorized": True})}
