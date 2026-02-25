import json
from pydantic import ValidationError
from schema import IngestionMessage
from router import route

def lambda_handler(event, context):
    records = event["Records"]
    failures = []

    for record in records:
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            message = IngestionMessage(**body)
            result = route(message)
            print(f"[{message_id}] Routed: {result}")

        except ValidationError as e:
            print(f"[{message_id}] Validation failed: {e}")
            failures.append({"itemIdentifier": message_id})

        except Exception as e:
            print(f"[{message_id}] Error: {e}")
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}