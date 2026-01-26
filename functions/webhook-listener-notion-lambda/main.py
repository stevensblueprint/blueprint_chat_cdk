from utils import get_safe_env

NOTION_KEY = get_safe_env("NOTION_API_KEY")


def handler(event, ctx):
    return {
        "statusCode": 200,
    }
