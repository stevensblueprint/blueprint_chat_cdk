import json
import logging
from pydantic import ValidationError
from schema import IngestionMessage
from router import route

logger = logging.getLogger(__name__)
logger.setLevel(logging.INFO)


def lambda_handler(event, context):
    records = event.get("Records", [])

    if not records:
        logger.warning("No records to process")
        return {"batchItemFailures": []}
    

    failures = []

    for record in records:
        message_id = record["messageId"]
        try:
            body = json.loads(record["body"])
            message = IngestionMessage(**body)
            result = route(message)
            logger.info(f"[{message_id}] Routed message: {result}")

        except ValidationError as e:
            logger.error(f"[{message_id}] Validation error: {e}")
            failures.append({"itemIdentifier": message_id})

        except Exception as e:
            logger.exception(f"[{message_id}] Error: {e}")
            failures.append({"itemIdentifier": message_id})

    return {"batchItemFailures": failures}