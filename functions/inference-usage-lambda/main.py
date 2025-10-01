import os
import json
import boto3
from datetime import datetime
from datetime import datetime

dynamodb = boto3.resource("dynamodb")
MONTHLY_USAGE_TABLE = os.environ["MONTHLY_USAGE_TABLE"]
MONTHLY_LIMIT = float(os.environ["MONTHLY_LIMIT"])

monthly_tbl = dynamodb.Table(MONTHLY_USAGE_TABLE)


def handler(event, _):
    now = datetime.now()
    month_year = now.strftime("%m_%Y")

    headers = event.get("headers", "{}")
    user_arn = headers.get("userArn")

    response = monthly_tbl.get_item(Key={"userArn": user_arn, "month_year": month_year})

    item = response.get("Item")

    if item:
        monthly_usage = item.get("cost")
        return {
            "statusCode": 200,
            "body": json.dumps(
                {
                    "current_usage": str(monthly_usage),
                    "monthly_limit": str(MONTHLY_LIMIT),
                }
            ),
        }
    else:
        return {
            "statusCode": 200,
            "body": json.dumps({"current_usage": 0, "monthly_limit": MONTHLY_LIMIT}),
        }
