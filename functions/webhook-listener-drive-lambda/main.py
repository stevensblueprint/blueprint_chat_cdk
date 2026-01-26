from utils import get_safe_env

DRIVE_API_KEY = get_safe_env("DRIVE_API_KEY")


def handler(event, ctx):
    return {
        "statusCode": 200,
    }
